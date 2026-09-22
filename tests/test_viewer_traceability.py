"""Traceability authorization and safe projections, without a live database."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import main as viewer
import traceability

VIEW = "v_" + "a" * 24
OWNER = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
WORKSPACE = "33333333-3333-3333-3333-333333333333"
REF = "ws_abcd1234.changes"
NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(viewer.dbq, "get_pool", MagicMock())
    monkeypatch.setattr(viewer.viewer_auth, "SECRET", "fixture-only-secret")
    monkeypatch.setattr(viewer.dbq, "api_key_valid", lambda conn, key: key in (OWNER, OTHER))
    monkeypatch.setattr(viewer.dbq, "workspace_owned",
                        lambda conn, key, ws: {"id": WORKSPACE} if key == OWNER and ws == WORKSPACE else None)
    monkeypatch.setattr(viewer.dbq, "get_view", lambda conn, vid: {
        "view_id": VIEW, "workspace_id": WORKSPACE, "title": "Map", "version": 1,
        "spec": {"layers": [{"ref": REF}]},
    } if vid == VIEW else None)
    monkeypatch.setattr(viewer.dbq, "columns",
                        lambda *args: [("fid", "int8"), ("evidence", "text"), ("geom", "geometry")])
    return TestClient(viewer.app)


@pytest.mark.parametrize("key,owner", [(None, False), (OTHER, False), (OWNER, True)])
def test_audit_requires_actual_workspace_owner_not_map_capability(client, monkeypatch, key, owner):
    called = []
    def data(conn, view, *, owner):
        called.append(owner)
        return {"audit": {"events": [{"id": "private-event"}] if owner else []}}
    monkeypatch.setattr(traceability, "map_details", data)
    if key:
        client.cookies.set(viewer.viewer_auth.COOKIE_NAME, viewer.viewer_auth.make_cookie(key))
    response = client.get(f"/v/{VIEW}/traceability")
    assert response.status_code == 200 and called == [owner]
    assert ("private-event" in response.text) is owner
    assert response.headers["cache-control"] == "private, no-store"


def test_unknown_map_and_unlisted_layer_never_fetch_evidence(client, monkeypatch):
    fetch = MagicMock()
    monkeypatch.setattr(traceability, "map_details", fetch)
    monkeypatch.setattr(traceability, "feature_record", fetch)
    assert client.get("/v/v_" + "b" * 24 + "/traceability").status_code == 404
    assert client.get(f"/v/{VIEW}/feature-evidence?layer=ws_deadbeef.private&identity=1").status_code == 403
    fetch.assert_not_called()


def test_feature_endpoint_uses_existing_capability_and_only_stable_keys(client, monkeypatch):
    fetch = MagicMock(return_value={"fid": 1, "evidence": "visible roof"})
    monkeypatch.setattr(traceability, "feature_record", fetch)
    result = client.get(f"/v/{VIEW}/feature-evidence?layer={REF}&identity=1")
    assert result.status_code == 200 and result.json()["properties"]["evidence"] == "visible roof"
    assert fetch.call_args.args[1:] == ("ws_abcd1234", "changes", ["fid", "evidence"], "fid", "1")
    assert client.get(f"/v/{VIEW}/feature-evidence?layer={REF}&key=password&identity=x").status_code == 422


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///private/file", "https://user:secret@host.test/a"])
def test_unsafe_source_links_are_omitted(url):
    assert traceability.safe_url(url) is None


def test_evidence_projection_preserves_conflicting_observations_and_safe_citations():
    properties = {
        "fid": 1, "confidence_label": "high", "geometry_kind": "bbox",
        "observations": '[{"tile_id":"a","change_type":"demolition","evidence":"removed roof"},'
                        '{"tile_id":"b","change_type":"new_building","evidence":"green surface","api_key":"SECRET"}]',
        "document_url": "https://source.test/permit.pdf?token=SECRET#page=4",
        "page": 4, "api_key": "SECRET", "raw_prompt": "PRIVATE",
    }
    data = traceability.feature_details(properties)
    assert [o["change_type"] for o in data["observations"]] == ["demolition", "new_building"]
    assert data["document_url"] == "https://source.test/permit.pdf#page=4"
    assert "SECRET" not in str(data) and "PRIVATE" not in str(data)


def test_map_history_is_scoped_and_never_exposes_raw_sql_or_job_payload(monkeypatch):
    queries = []
    fixtures = [
        [{"id": "dataset", "ref_table": REF, "title": "Orthophoto", "external_id": "2024",
          "updated_at": NOW, "schema_summary": {"datetime_min": "2024-06-01", "api_key": "SECRET"},
          "source_title": "City", "kind": "wms", "url": "https://city.test/wms?token=SECRET", "attribution": "City"}],
        [{"id": 8, "ts": NOW, "kind": "change_detect", "object_ref": REF,
          "input_tables": [REF, "ws_deadbeef.private"], "job_id": 7,
          "details": {"backend": "vision", "sql_text": "PRIVATE", "api_key": "SECRET",
                      "model": {"complete": False, "tile_failures": {"r0": {"reason": "http_429", "attempts": 3}}}}}],
        [{"id": 7, "kind": "change_detect", "status": "done", "attempts": 1, "result": {
            "tiles_skipped": 1, "raw_prompt": "PRIVATE", "source_sha256": "digest"}}],
    ]
    def rows(conn, query, params):
        queries.append((query, params))
        return fixtures.pop(0)
    monkeypatch.setattr(traceability, "_rows", rows)
    data = traceability.map_details(None, {"view_id": VIEW, "workspace_id": WORKSPACE,
        "title": "Map", "version": 2, "spec": {"layers": [{"ref": REF}]}}, owner=False)
    assert len(queries) == 3  # No private audit queries for capability readers.
    assert "workspace_id = %s" in queries[1][0] and queries[1][1][1] == WORKSPACE
    assert queries[2][1] == (WORKSPACE, [7])
    assert data["processing"][0]["inputs"] == [REF]
    assert data["processing"][0]["details"]["model"]["tile_failures"]["r0"]["reason"] == "http_429"
    assert data["sources"][0]["dates"]["datetime_min"] == "2024-06-01"
    assert "SECRET" not in str(data) and "PRIVATE" not in str(data)
    assert "workspace_url" not in data["audit"]


def test_private_activity_query_filters_map_layers_jobs_and_workspace(monkeypatch):
    calls = []
    responses = [[], [], []]
    def rows(conn, query, params):
        calls.append((query, params))
        return responses.pop(0)
    monkeypatch.setattr(traceability, "_rows", rows)
    result = traceability.map_details(None, {"view_id": VIEW, "workspace_id": WORKSPACE,
        "title": "Map", "version": 1, "spec": {"layers": [{"ref": REF}]}}, owner=True)
    query, params = calls[-1]
    assert "q.referenced_tables && %s::text[]" in query
    assert "r.job_id = ANY(%s::bigint[])" in query
    assert params == (WORKSPACE, [REF], WORKSPACE, [])
    assert "q.sql_text" not in query and "api_key_id" not in query
    assert result["audit"]["workspace_url"] == f"/workspaces/{WORKSPACE}#audit"


def test_document_lookup_comes_from_stored_feature_only(monkeypatch):
    conn, calls = MagicMock(), []
    def rows(conn, query, params):
        calls.append((query, params))
        if len(calls) == 1:
            return [{"fid": 3, "document_id": "doc-id", "page": 7}]
        return [{"id": "doc-id", "title": "Recorded decision", "source_url": "https://source.test/decision.pdf?signature=SECRET"}]
    monkeypatch.setattr(traceability, "_rows", rows)
    result = traceability.feature_record(conn, "ref", "decisions", ["fid", "document_id", "page", "password"], "fid", "3")
    assert calls[0][1] == ("3",)
    assert "password" not in calls[0][0].as_string()
    assert calls[1][1] == ("doc-id",)
    assert result["document_url"] == "https://source.test/decision.pdf" and result["page"] == 7


def test_maplibre_traceability_is_additive_and_origo_unchanged():
    import page
    html = page.maplibre_page(VIEW, nonce="test")
    assert "/static/traceability.js" in html and "tracePanel.feature" in html
    assert "src=\"/static/imagery/index.js\"" not in html  # Existing lazy import retained.
    assert 'import("/static/imagery/index.js")' in html
    assert "traceability" not in page.origo_page(VIEW, nonce="test")

def test_review_flag_and_conflicts_are_allowlisted_without_promoting_confidence():
    data = traceability.feature_details({
        "review_required": True, "confidence_label": "high",
        "conflicting_observations": '[{"tile_id":"r2","change_type":"demolition","evidence":"roof absent","raw_prompt":"PRIVATE"}]',
    })
    assert data["review_required"] is True and data["confidence_label"] == "high"
    assert data["conflicting_observations"][0]["change_type"] == "demolition"
    assert "PRIVATE" not in str(data)

def test_public_document_identifiers_survive_without_access_tokens():
    assert traceability.safe_url("https://source.test/download?documentId=42&version=2&token=SECRET#page=7") == (
        "https://source.test/download?documentId=42&version=2#page=7")
