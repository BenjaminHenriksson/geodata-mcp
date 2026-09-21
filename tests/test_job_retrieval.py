from unittest.mock import Mock

import pytest

import db
import export_ops
import job_ops
import load_ops
import server


@pytest.fixture
def export_job(monkeypatch):
    job = {"id": 7, "kind": "export", "workspace_id": "workspace", "status": "done",
           "payload": {"format": "csv"},
           "result": {"object_key": "exports/one/file.csv", "sidecar_key": "exports/one/citation.md"}}
    monkeypatch.setattr(db, "get_job", lambda _: job)
    enqueue = Mock(side_effect=AssertionError("retrieval must never enqueue work"))
    monkeypatch.setattr(db, "enqueue_job", enqueue)
    client = Mock()
    client.presigned_get_object.side_effect = lambda bucket, key, **kw: "https://fixture/" + key
    monkeypatch.setattr(export_ops, "_minio_client", lambda: client)
    return job, client


def test_repeated_export_retrieval_reuses_artifacts(export_job):
    _, client = export_job
    for _ in range(3):
        result = export_ops.result("workspace", 7)
        assert result == {"job_id": 7, "status": "done", "format": "csv", "expires_hours": 24,
                          "url": "https://fixture/exports/one/file.csv",
                          "sidecar_url": "https://fixture/exports/one/citation.md"}
    assert client.presigned_get_object.call_count == 6


@pytest.mark.parametrize("state", ["queued", "running", "error", "cancelled"])
def test_unfinished_exports_do_not_sign_or_enqueue(export_job, state):
    job, client = export_job
    job.update(status=state, result=None, error="fixture")
    result = export_ops.result("workspace", 7)
    assert result["job_id"] == 7
    if state in ("queued", "running"):
        assert result["status"] == state and "export(job_id=" in result["note"]
    else:
        assert "error" in result
    client.presigned_get_object.assert_not_called()


def test_unowned_job_cannot_be_read_waited_signed_or_cancelled(export_job, monkeypatch):
    _, client = export_job
    wait = Mock(side_effect=AssertionError("must check ownership before waiting"))
    monkeypatch.setattr(db, "wait_for_job", wait)
    monkeypatch.setattr(db, "app_pool", Mock(side_effect=AssertionError("must not mutate")))
    expected = {"error": "no job with id 7"}
    assert job_ops.status(7, 10, workspace_id="stranger") == expected
    assert job_ops.cancel(7, workspace_id="stranger") == expected
    assert export_ops.result("stranger", 7) == expected
    client.presigned_get_object.assert_not_called()


def test_export_retrieval_rejects_other_job_kinds(export_job):
    job, client = export_job
    job["kind"] = "ingest_pdf"
    assert export_ops.result("workspace", 7) == {"error": "job 7 is not an export job"}
    client.presigned_get_object.assert_not_called()


def test_status_long_poll_is_bounded_and_missing_jobs_are_reported(export_job, monkeypatch):
    job, _ = export_job
    job["status"] = "running"
    wait = Mock(return_value=None)
    monkeypatch.setattr(db, "wait_for_job", wait)
    assert job_ops.status(7, 1000, workspace_id="workspace") == {"error": "no job with id 7"}
    wait.assert_called_once_with(7, timeout_s=25.0)


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), "invalid"])
def test_invalid_timeouts_are_errors(timeout):
    assert job_ops.status(7, timeout) == {"error": "timeout_s must be a finite number"}


def test_job_listing_passes_workspace_filter(monkeypatch):
    recent = Mock(return_value=[])
    monkeypatch.setattr(db, "recent_jobs", recent)
    assert load_ops.jobs("workspace") == {"jobs": []}
    recent.assert_called_once_with(20, "workspace")


def test_export_argument_forms_are_unambiguous(monkeypatch):
    monkeypatch.setattr(server, "_ws", lambda *args: Mock(id="workspace"))
    result = server.export.__wrapped__(layers=["ref.test"], job_id=7)
    assert "OR job_id" in result["error"]
