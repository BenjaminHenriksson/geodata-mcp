"""Isolated integration tests. Never fall back to deployment environment or data.

Set GEODATA_TEST_DATABASE_URL to the disposable PostgreSQL app DSN, plus the
normal DATABASE_URL_* test DSNs. See docs/workspace-dashboards.md.
"""
import os
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from fastapi.testclient import TestClient

if not os.environ.get("GEODATA_TEST_DATABASE_URL"):
    pytest.skip("explicit isolated database required", allow_module_level=True)
if os.environ["GEODATA_TEST_DATABASE_URL"] != os.environ.get("DATABASE_URL_APP"):
    raise RuntimeError("test database must match the application test DSN")

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services/mcp"), str(ROOT / "services/viewer")]

import audit
import dashboard_data
import main as viewer
import server
import sessions
import viewer_auth

import db


def context(key):
    return SimpleNamespace(request_context=SimpleNamespace(
        request=SimpleNamespace(headers={"authorization": "Bearer " + key})))


def records(query, params=()):
    with psycopg.connect(os.environ["GEODATA_TEST_DATABASE_URL"]) as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else []


@pytest.fixture(scope='session', autouse=True)
def close_pools():
    yield
    for pool in db._pools.values():
        pool.close()


@pytest.fixture
def principal():
    key = "dashboard-test-" + uuid.uuid4().hex
    kid = records("INSERT INTO app.api_keys(key_hash, name) VALUES (%s, %s) RETURNING id::text",
                  (sessions.hash_key(key), "test-" + key[-8:]))[0][0]
    w = sessions.get_or_create_workspace(kid, "default", activate=True)
    return key, kid, w


@pytest.fixture
def client(principal):
    with TestClient(viewer.app) as c:
        c.cookies.set(viewer_auth.COOKIE_NAME, viewer_auth.make_cookie(principal[1]))
        yield c


@pytest.mark.parametrize("sql,failed", [
    ("SELECT 42 AS answer", False),
    ("DELETE FROM ref.anything", True),
    ("SELECT * FROM ref.missing_table", True),
    ("SELECT 1 / 0", True),
    ("SELECT 1 / (random()::int * 0)", True),
])
def test_sql_success_rejection_and_database_errors(principal, sql, failed):
    result = server.query(sql, ctx=context(principal[0]))
    assert bool(result.get("error")) is failed
    row = records("""SELECT c.api_key_id::text, r.workspace_id, r.status, c.sql_text,
                            q.query_id::text, q.api_key_id::text, q.mcp_call_id::text, q.duration_ms
                     FROM app.mcp_calls c JOIN app.mcp_call_results r USING(call_id)
                     JOIN app.query_log q ON q.query_id = r.query_id WHERE c.call_id = %s""",
                  (result["audit_call_id"],))[0]
    assert row[:4] == (principal[1], principal[2].id, "error" if failed else "success", sql)
    assert row[4:7] == (result["query_id"], principal[1], result["audit_call_id"])
    assert row[7] is not None


def test_every_tool_registered_with_original_schema():
    import asyncio
    tools = asyncio.run(server.mcp.list_tools())
    assert len(tools) == 8
    for tool in tools:
        assert "args" not in tool.inputSchema["properties"]
        assert "ctx" not in tool.inputSchema["properties"]
    assert next(t for t in tools if t.name == "query").inputSchema["required"] == ["sql"]


@pytest.mark.parametrize("tool,arguments", [
    ("search", {}), ("load", {"op": "invalid"}), ("analyze", {"op": "list"}),
    ("workspace", {"op": "current"}), ("layer", {"op": "invalid"}),
    ("map", {"op": "invalid"}), ("export", {"layers": [], "format": "invalid"}),
])
def test_all_tools_audited(principal, tool, arguments):
    out = getattr(server, tool)(**arguments, ctx=context(principal[0]))
    row = records("SELECT c.tool_name, c.api_key_id::text, r.status FROM app.mcp_calls c "
                  "JOIN app.mcp_call_results r USING(call_id) WHERE c.call_id = %s",
                  (out["audit_call_id"],))[0]
    assert row == (tool, principal[1], "error" if out.get("error") else "success")


def test_layer_sql_audit_including_rejected_writes(principal):
    ctx = context(principal[0])
    made = server.layer(op="create", name="example", sql="SELECT 7 AS value", ctx=ctx)
    assert "error" not in made
    rejected = server.layer(op="create", name="bad", sql="DELETE FROM ref.forbidden", ctx=ctx)
    assert rejected.get("error")
    with db.app_pool().connection() as conn:
        events = dashboard_data.audit_events(conn, workspace_id=principal[2].id, kind="sql")
    assert {e["status"] for e in events["events"]} == {"success", "error"}
    assert len(events["events"]) == 2


def test_workspace_switch_rename_delete_attribution(principal, client):
    ctx = context(principal[0])
    out = server.workspace(op="new", name="separate", ctx=ctx)
    target = records("SELECT id::text FROM app.workspaces WHERE ws_schema = %s", (out["ws_schema"],))[0][0]
    call = records("SELECT workspace_id FROM app.mcp_call_results WHERE call_id = %s",
                   (out["audit_call_id"],))[0][0]
    assert call == target
    server.workspace(op="use", name="default", ctx=ctx)
    renamed = server.workspace(op="rename", name="separate", new_name="renamed", ctx=ctx)
    deleted = server.workspace(op="delete", name="renamed", ctx=ctx)
    for out in (renamed, deleted):
        assert not out.get("error")
        assert records("SELECT workspace_id FROM app.mcp_call_results WHERE call_id = %s",
                       (out["audit_call_id"],))[0][0] == target
    assert client.get(f"/workspaces/{target}").status_code == 404
    records("UPDATE app.api_keys SET is_admin = true WHERE id = %s", (principal[1],))
    page = client.get("/admin/audit")
    assert deleted["audit_call_id"] in page.text


def test_user_admin_and_disabled_boundaries(principal, client):
    other_key = "other-" + uuid.uuid4().hex
    kid = records("INSERT INTO app.api_keys(key_hash, name) VALUES (%s, 'other-owner') RETURNING id::text",
                  (sessions.hash_key(other_key),))[0][0]
    other = sessions.get_or_create_workspace(kid, "hidden-workspace", activate=True)
    server.query("SELECT 'hidden-sql'", ctx=context(other_key))
    assert client.get("/").headers.get("content-type", "").startswith("text/html")
    assert client.get("/dashboard").status_code == 200
    assert "hidden-workspace" not in client.get("/dashboard").text
    for path in ("/admin", "/admin/audit"):
        assert client.get(path).status_code == 403
    assert client.get(f"/workspaces/{other.id}").status_code == 404
    assert client.get("/workspaces/not-a-uuid").status_code == 404
    own_page = client.get(f"/workspaces/{principal[2].id}")
    assert "hidden-sql" not in own_page.text
    assert own_page.headers["cache-control"] == "private, no-store"
    assert "'unsafe-inline'" not in own_page.headers["content-security-policy"].split("style-src")[0]
    records("UPDATE app.api_keys SET is_admin = true WHERE id = %s", (principal[1],))
    assert "hidden-workspace" in client.get("/admin").text
    assert "hidden-sql" in client.get(f"/workspaces/{other.id}").text
    records("UPDATE app.api_keys SET is_admin = false WHERE id = %s", (principal[1],))
    assert client.get("/admin").status_code == 403
    records("UPDATE app.api_keys SET disabled = true WHERE id = %s", (principal[1],))
    assert client.get("/dashboard", follow_redirects=False).headers["location"] == "/login"


def test_anonymous_and_login_navigation(principal):
    with TestClient(viewer.app) as c:
        for path in ("/dashboard", "/admin", "/admin/audit", f"/workspaces/{principal[2].id}"):
            assert c.get(path, follow_redirects=False).headers["location"] == "/login"
        response = c.post("/login", data={"key": principal[0]}, follow_redirects=False)
        assert response.headers["location"] == "/dashboard"
        assert "HttpOnly" in response.headers["set-cookie"]


def test_audit_filters_pagination_and_html_escaping(principal, client):
    ctx = context(principal[0])
    payload = "<img src=x onerror=alert(1)>"
    server.query(f"SELECT '{payload}'", ctx=ctx)
    server.query("DELETE FROM ref.nope", ctx=ctx)
    for _ in range(51):
        server.workspace(ctx=ctx)
    path = f"/workspaces/{principal[2].id}"
    html = client.get(path + "?kind=sql&status=success").text
    assert "&lt;img" in html and payload not in html
    assert "DELETE FROM" not in html
    errors = client.get(path + "?kind=sql&status=error").text
    assert "DELETE FROM" in errors and "&lt;img" not in errors
    first = client.get(path + "?kind=mcp")
    second = client.get(path + "?kind=mcp&page=2")
    assert first.text.count('class="audit"') == 50
    assert second.text.count('class="audit"') == 3
    for suffix in ("?kind=bad", "?status=bad", "?page=0", "?page=100001"):
        assert client.get(path + suffix).status_code == 422


def test_workspace_totals_are_not_limited_to_map_preview(principal, client):
    records("""INSERT INTO app.map_views(view_id, workspace_id, title, spec)
               SELECT 'v_' || substr(md5(random()::text), 1, 24), %s, 'Map', '{}'::jsonb
               FROM generate_series(1, 25)""", (principal[2].id,))
    with db.app_pool().connection() as conn:
        data = dashboard_data.overview(conn, principal[1])
    assert data["totals"]["maps"] == 25
    assert data["workspaces"][0]["maps"] == 25


def test_credentials_and_payload_not_copied(principal):
    out = server.load(op="inline", rows=[{"secret": "sensitive-row"}], table_name="data",
                      source="sensitive-source", ctx=context(principal[0]))
    text = str(records("SELECT to_jsonb(c)::text FROM app.mcp_calls c WHERE call_id = %s",
                       (out["audit_call_id"],)))
    assert principal[0] not in text
    assert "sensitive-row" not in text
    assert "sensitive-source" not in text
    assert "ctx" not in text


def test_concurrent_attribution_is_isolated(principal):
    key = "concurrent-" + uuid.uuid4().hex
    kid = records("INSERT INTO app.api_keys(key_hash) VALUES (%s) RETURNING id::text",
                  (sessions.hash_key(key),))[0][0]
    sessions.get_or_create_workspace(kid, "default", activate=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(server.query, "SELECT 1", ctx=context(k)) for k in (principal[0], key)]
        calls = [f.result()["audit_call_id"] for f in futures]
    for call_id, expected in zip(calls, (principal[1], kid), strict=True):
        assert records("SELECT api_key_id::text FROM app.query_log WHERE mcp_call_id = %s",
                       (call_id,))[0][0] == expected
    assert audit.current_workspace() is None


def test_append_only_and_agent_ledger_permissions(principal):
    out = server.query("SELECT 1", ctx=context(principal[0]))
    for table in ("mcp_calls", "mcp_call_results", "query_log"):
        for operation in (f"DELETE FROM app.{table}", f"TRUNCATE app.{table}"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                records(operation)
    for url in (os.environ["DATABASE_URL_RO"], os.environ["DATABASE_URL_WS"]):
        with psycopg.connect(url) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM app.mcp_calls")
    assert records("SELECT 1 FROM app.mcp_calls WHERE call_id = %s", (out["audit_call_id"],))


def test_audit_storage_failure_prevents_dispatch(principal, monkeypatch):
    workspace = principal[2]
    dispatched = []
    @audit.tool(lambda ctx: workspace)
    def fake(ctx=None):
        dispatched.append(True)
        return {}
    monkeypatch.setattr(db, "app_pool", lambda: (_ for _ in ()).throw(RuntimeError("unavailable")))
    out = fake()
    assert "not executed" in out["error"]
    assert not dispatched


def test_completion_failure_retains_start_and_warns(principal, monkeypatch):
    original = db.app_pool
    @audit.tool(lambda ctx: principal[2])
    def fake(ctx=None):
        monkeypatch.setattr(db, "app_pool", lambda: (_ for _ in ()).throw(RuntimeError("unavailable")))
        return {"committed": True}
    out = fake()
    monkeypatch.setattr(db, "app_pool", original)
    assert out["committed"] and out["audit_warning"]
    assert records("SELECT 1 FROM app.mcp_calls WHERE call_id = %s", (out["audit_call_id"],))
    assert not records("SELECT 1 FROM app.mcp_call_results WHERE call_id = %s", (out["audit_call_id"],))


def test_polled_job_id_is_correlated(principal):
    job_id = records("INSERT INTO app.jobs(kind, workspace_id) VALUES ('export', %s) RETURNING id",
                     (principal[2].id,))[0][0]
    out = server.load(op="status", job_id=job_id, ctx=context(principal[0]))
    assert records("SELECT job_id FROM app.mcp_call_results WHERE call_id = %s",
                   (out["audit_call_id"],))[0][0] == job_id


def test_unexpected_exception_is_audited_without_payload(principal):
    @audit.tool(lambda ctx: principal[2])
    def fake(ctx=None):
        raise RuntimeError("sensitive-source-url")
    out = fake()
    assert "RuntimeError" in out["error"]
    assert "sensitive-source-url" not in str(out)
    assert records("SELECT status FROM app.mcp_call_results WHERE call_id = %s",
                   (out["audit_call_id"],))[0][0] == "error"
