"""Controller tests use fake processes and HTTP; never contact a host service."""
import importlib.util
import json
from pathlib import Path
from threading import Event
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location(
    "service_control_app", Path(__file__).resolve().parents[1] / "services/control/app.py")
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    token = "isolated-test-token-" + "x" * 32
    (tmp_path / "token").write_text(token)
    config = {
        "database": str(tmp_path / "audit.sqlite"), "token_file": str(tmp_path / "token"),
        "targets": {
            "worker": {"name": "Worker", "kind": "docker", "project": "isolated",
                       "service": "worker", "actions": ["start", "restart"]},
            "db": {"name": "Database", "kind": "docker", "project": "isolated",
                   "service": "postgres", "actions": []},
            "sam3": {"name": "SAM3", "kind": "systemd", "unit": "isolated-sam3.service",
                     "health_url": "http://127.0.0.1:18200/healthz", "actions": ["restart"]},
        }}
    (tmp_path / "config").write_text(json.dumps(config))
    monkeypatch.setenv("SERVICE_CONTROL_CONFIG", str(tmp_path / "config"))
    calls = []

    def command(args, timeout=8):
        calls.append(args)
        if args[:3] == ["docker", "ps", "-aq"]:
            return "a" * 64
        if args[:2] == ["docker", "inspect"]:
            return json.dumps([{"State": {"Status": "running", "Running": True,
                                          "Health": {"Status": "healthy"}},
                                "RestartCount": 2, "Config": {"Image": "test:latest",
                                                              "Env": ["SECRET=never-show"]}}])
        if args[:2] == ["docker", "stats"]:
            return json.dumps({"ID": "a" * 12, "CPUPerc": "0.2%", "MemUsage": "12MiB / 1GiB",
                               "PIDs": "4"})
        if args[:3] == ["systemctl", "--user", "show"]:
            return "ActiveState=active\nSubState=running\nMemoryCurrent=1024\nNRestarts=0"
        return ""

    monkeypatch.setattr(control, "command", command)
    return config, token, calls


def payload(**kwargs):
    return dict(id=str(uuid4()), service="worker", action="restart",
                actor_id=str(uuid4()), actor="Operator", **kwargs)


def test_authentication_and_allowlist(setup):
    _, token, calls = setup
    app = control.create_app()
    with TestClient(app) as client:
        assert client.get("/status").status_code == 401
        assert client.post("/actions", json=payload()).status_code == 401
        assert not calls
        for service, action, status in (("eneo", "restart", 403), ("db", "restart", 403),
                                        ("worker", "exec", 422), ("../worker", "restart", 422)):
            req = payload()
            req.update(service=service, action=action)
            assert client.post("/actions", json=req,
                               headers={"Authorization": "Bearer " + token}).status_code == status
        assert not calls


def test_snapshot_redacts_inspect_and_cold_model_is_healthy(setup, monkeypatch):
    _, token, _ = setup
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda req: httpx.Response(
        200, json={"ok": True, "backend": "transformers", "model_loaded": False, "token": "secret"}))
    monkeypatch.setattr(control.httpx, "Client", lambda **kw: real_client(transport=transport, **kw))
    app = control.create_app()
    with TestClient(app) as client:
        response = client.get("/status", headers={"Authorization": "Bearer " + token})
        assert response.status_code == 200
        assert "never-show" not in response.text and "secret" not in response.text
        services = response.json()["services"]
        assert services[0]["health"] == "healthy"
        assert services[0]["cpu"] == "0.2%"
        assert services[2]["api_health"] == "healthy"
        assert services[2]["model_loaded"] is False


def test_unreachable_api_and_missing_container_are_not_healthy(setup, monkeypatch):
    cfg, _, _ = setup
    controller = control.Controller(cfg)
    real_client = httpx.Client
    def fail(request):
        raise httpx.ConnectError("secret URL", request=request)
    monkeypatch.setattr(control.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(fail), **kw))
    row = controller.inspect("sam3", cfg["targets"]["sam3"])
    assert row["api_health"] == "unreachable"
    monkeypatch.setattr(control, "command", lambda *a, **kw: "")
    row = controller.snapshot()["services"][0]
    assert row["state"] == "unknown"
    controller.executor.shutdown()


def test_restart_is_audited_and_duplicate_request_is_idempotent(setup, monkeypatch):
    _, token, calls = setup
    original = control.command
    entered, release = Event(), Event()
    def blocking(args, timeout=8):
        if args[:2] == ["docker", "restart"]:
            entered.set()
            assert release.wait(5)
        return original(args, timeout)
    monkeypatch.setattr(control, "command", blocking)
    app = control.create_app()
    req = payload()
    headers = {"Authorization": "Bearer " + token}
    with TestClient(app) as client:
        try:
            assert client.post("/actions", json=req, headers=headers).status_code == 202
            assert entered.wait(3)
            assert app.state.controller.history()[0]["status"] == "queued"
            assert client.post("/actions", json=req, headers=headers).status_code == 202
            assert client.post("/actions", json=payload(), headers=headers).status_code == 409
        finally:
            release.set()
    rows = app.state.controller.history()
    assert len(rows) == 1 and rows[0]["status"] == "success" and rows[0]["actor"] == "Operator"
    assert len([c for c in calls if c[:2] == ["docker", "restart"]]) == 1
    assert next(c for c in calls if c[:2] == ["docker", "restart"]) == [
        "docker", "restart", "--time", "20", "a" * 64]


@pytest.mark.parametrize("failure,expected", [
    (control.subprocess.CalledProcessError(1, "docker", stderr="secret"), "error"),
    (control.subprocess.TimeoutExpired("docker", 50), "unknown"),
])
def test_failed_and_uncertain_actions_are_persisted(setup, monkeypatch, failure, expected):
    cfg, _, _ = setup
    controller = control.Controller(cfg)
    original = control.command
    def fail(args, timeout=8):
        if args[:2] == ["docker", "restart"]:
            raise failure
        return original(args, timeout)
    monkeypatch.setattr(control, "command", fail)
    controller.submit(control.Action(**payload()))
    controller.executor.shutdown(wait=True)
    assert controller.history()[0]["status"] == expected
    assert "secret" not in json.dumps(controller.history())


def test_interrupted_action_is_never_replayed(setup):
    cfg, _, calls = setup
    controller = control.Controller(cfg)
    with controller.db() as conn:
        conn.execute("INSERT INTO actions VALUES (?,?,?,?,?,?)",
                     (str(uuid4()), "worker", "restart", str(uuid4()), "Operator", control.now()))
    controller.executor.shutdown()
    recovered = control.Controller(cfg)
    assert recovered.history()[0]["status"] == "unknown"
    assert not calls
    recovered.executor.shutdown()

def test_intentionally_disabled_service_cannot_start_or_restart(setup):
    cfg, _, calls = setup
    cfg["targets"]["sam3"]["disabled_reason"] = "Avstängd för Vision."
    controller = control.Controller(cfg)
    row = controller.inspect("sam3", cfg["targets"]["sam3"])
    assert row["disabled_reason"] == "Avstängd för Vision."
    assert row["actions"] == [] and "api_health" not in row
    request = payload()
    request.update(service="sam3", action="restart")
    with pytest.raises(control.HTTPException) as error:
        controller.submit(control.Action(**request))
    assert error.value.status_code == 403
    assert controller.history() == []
    assert all("restart" not in c for c in calls)
    controller.executor.shutdown()
