"""Viewer access controls for host maintenance, using an isolated database."""
import uuid

import pytest
import test_workspace_dashboards as support
from fastapi.testclient import TestClient

client = support.client
principal = support.principal
viewer = support.viewer


def test_service_routes_reject_anonymous_and_non_admin(client, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unauthorized request reached the controller")
    monkeypatch.setattr(viewer.service_admin, "request", forbidden)
    for method, path in (("get", "/admin/services"), ("post", "/admin/services/action")):
        assert getattr(client, method)(path).status_code == 403
        with TestClient(viewer.app) as public:
            assert getattr(public, method)(path, follow_redirects=False).headers["location"] == "/login"
    assert 'href="/admin/services"' not in client.get("/dashboard").text


def test_admin_action_requires_csrf_and_attributes_actor(client, principal, monkeypatch):
    support.records("UPDATE app.api_keys SET is_admin=true WHERE id=%s", (principal[1],))
    calls = []
    monkeypatch.setattr(viewer.service_admin, "request", lambda *a: calls.append(a) or {})
    data = {"service": "sam3", "action": "restart", "request_id": str(uuid.uuid4())}
    assert client.post("/admin/services/action", data=data).status_code == 403
    assert not calls
    data["csrf"] = viewer.viewer_auth.csrf_token(principal[1])
    response = client.post("/admin/services/action", data=data, follow_redirects=False)
    assert response.status_code == 303 and "accepted=true" in response.headers["location"]
    assert calls[0][2]["actor_id"] == principal[1]
    assert calls[0][2]["service"] == "sam3"
    support.records("UPDATE app.api_keys SET is_admin=false WHERE id=%s", (principal[1],))
    assert client.post("/admin/services/action", data=data).status_code == 403
    assert len(calls) == 1


def test_service_page_escapes_values_and_handles_controller_failure(client, principal, monkeypatch):
    support.records("UPDATE app.api_keys SET is_admin=true WHERE id=%s", (principal[1],))
    data = {"checked_at": "2026-09-21T12:00:00Z", "history": [], "services": [{
        "id": "sam3", "name": "<img src=x onerror=alert(1)>", "kind": "systemd",
        "state": "active", "api_health": "healthy", "model_loaded": False,
        "actions": ["restart"], "unit": "sam3.service"}]}
    monkeypatch.setattr(viewer.service_admin, "request", lambda *a: data)
    response = client.get("/admin/services")
    assert response.status_code == 200
    assert "Ej laddad" in response.text and "Underhållslogg" in response.text
    assert "<img src=x" not in response.text and "&lt;img" in response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert 'name="csrf"' in response.text
    def unavailable(*a):
        raise viewer.service_admin.Unavailable("Tjänsteövervakningen kan inte nås.")
    monkeypatch.setattr(viewer.service_admin, "request", unavailable)
    response = client.get("/admin/services")
    assert "kan inte nås" in response.text and 'role="alert"' in response.text


def test_disabled_admin_cannot_control_services(client, principal, monkeypatch):
    support.records("UPDATE app.api_keys SET is_admin=true,disabled=true WHERE id=%s", (principal[1],))
    monkeypatch.setattr(viewer.service_admin, "request", lambda *a: pytest.fail("controller called"))
    assert client.post("/admin/services/action", follow_redirects=False).headers["location"] == "/login"

def test_disabled_service_has_no_start_control(client, principal, monkeypatch):
    support.records("UPDATE app.api_keys SET is_admin=true WHERE id=%s", (principal[1],))
    data = {"checked_at": "2026-09-21T12:00:00Z", "history": [], "services": [{
        "id": "sam3", "name": "SAM3 API", "kind": "systemd", "state": "inactive",
        "disabled_reason": "Avstängd för Gemma.", "actions": ["start", "restart"]}]}
    monkeypatch.setattr(viewer.service_admin, "request", lambda *a: data)
    response = client.get("/admin/services")
    assert "Avstängd för Gemma." in response.text
    assert 'action="/admin/services/action"' not in response.text
