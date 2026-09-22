import datetime
from unittest.mock import MagicMock, Mock

import pytest

import db
import load_ops
import analysis_ops


@pytest.mark.parametrize(
    "job, expected",
    [
        (
            None,
            {
                "status": "queued",
                "note": "still running — poll with load(op='status', job_id=...)",
            },
        ),
        (
            {"status": "running"},
            {
                "status": "running",
                "note": "still running — poll with load(op='status', job_id=...)",
            },
        ),
        (
            {"status": "done", "result": {"rows": 2}},
            {"status": "done", "result": {"rows": 2}},
        ),
        ({"status": "done", "result": {}, "error": ""}, {"status": "done"}),
        (
            {"status": "error", "error": "failed"},
            {"status": "error", "error": "failed"},
        ),
        (
            {"status": "cancelled", "error": "cancelled"},
            {"status": "cancelled", "error": "cancelled"},
        ),
    ],
)
def test_ingest_job_response(monkeypatch, job, expected):
    monkeypatch.setattr(
        load_ops,
        "_dataset_and_source",
        lambda _: {
            "id": "dataset",
            "kind": "document",
            "source_kind": "pdf",
            "external_id": "https://example.test/a.pdf",
            "title": "Document",
        },
    )
    enqueue = Mock(return_value=7)
    wait = Mock(return_value=job)
    monkeypatch.setattr(db, "enqueue_job", enqueue)
    monkeypatch.setattr(db, "wait_for_job", wait)
    assert load_ops.ingest("workspace", "dataset", None, "ref") == {
        "job_id": 7,
        "kind": "ingest_pdf",
        **expected,
    }
    enqueue.assert_called_once_with(
        "ingest_pdf",
        {
            "dataset_id": "dataset",
            "title": "Document",
            "url": "https://example.test/a.pdf",
        },
        "workspace",
    )
    wait.assert_called_once_with(7, timeout_s=8.0)


def test_status_serializes_database_values(monkeypatch):
    monkeypatch.setattr(
        db,
        "get_job",
        lambda _: {
            "status": "done",
            "created_at": datetime.datetime(2026, 1, 1),
        },
    )
    assert load_ops.status("7") == {
        "status": "done",
        "created_at": "2026-01-01 00:00:00",
    }


@pytest.mark.parametrize("status", ["queued", "running", "done", "error", "cancelled"])
def test_analysis_job_response(monkeypatch, status):
    conn = MagicMock()
    rows = [("wms",), ("wms",), (None,), (None,), ("POLYGON EMPTY",), (False, 2, 0.01)]
    conn.execute.return_value.fetchone.side_effect = rows
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = conn
    monkeypatch.setattr(db, "app_pool", lambda: pool)
    monkeypatch.setattr(analysis_ops.sessions, "ws_schema_for", lambda _: "ws_12345678")
    monkeypatch.setattr(
        analysis_ops.sessions, "ensure_ws_schema", lambda _: "ws_12345678"
    )
    enqueue = Mock(return_value=7)
    monkeypatch.setattr(db, "enqueue_job", enqueue)
    monkeypatch.setattr(
        db,
        "wait_for_job",
        lambda *args, **kw: {
            "status": status,
            "result": {"table": "ws_12345678.changes"},
            "error": "detail",
        },
    )
    result = analysis_ops.run(
        "workspace",
        "change_detect",
        {
            "area": "0,0,100,100",
            "concepts": ["building"],
            "collection_a": "before",
            "collection_b": "after",
            "table_name": "changes",
        },
    )
    assert result == {
        "job_id": 7,
        "kind": "change_detect",
        "status": status,
        "result": {"table": "ws_12345678.changes"},
        "error": "detail",
        **(
            {
                "note": "Vision compares paired image crops through the model endpoint; results are approximate "
                "bounding boxes, not segmented footprints — poll with analyze(op='status', job_id=...)"
            }
            if status in ("queued", "running")
            else {}
        ),
    }
    assert enqueue.call_args.args[0] == "change_detect"
    assert enqueue.call_args.args[1]["target_schema"] == "ws_12345678"
    assert enqueue.call_args.args[2] == "workspace"


@pytest.mark.parametrize("route", ["status", "submit"])
def test_document_jobs_return_complete_text_and_source_links(monkeypatch, route):
    import job_ops
    full_text = "Full source text. " * 200 + "Only the outer row of plots is included."
    source_url = "https://example.test/" + "long-document-name-" * 30 + ".pdf#page=3"
    result = {"mode": "transcribe", "pages": [{"page": 3, "text": full_text,
              "source_url": source_url, "uncertainties": ["handwriting"]}]}
    job = {"id": 7, "kind": "inspect", "status": "done", "workspace_id": "owned",
           "result": result, "created_at": datetime.datetime(2026, 1, 1)}
    monkeypatch.setattr(db, "get_job", lambda _: job)
    monkeypatch.setattr(job_ops, "enqueue", lambda *a, **kw: (7, job))
    if route == "status":
        response = job_ops.status(7, workspace_id="owned")
        assert response["created_at"] == "2026-01-01 00:00:00"
    else:
        response = job_ops.submit("inspect", {}, "owned", "pending")
    assert response["result"] == result


def test_query_cell_formatting_keeps_its_existing_preview_limit():
    import geometry
    text = "x" * 800
    assert geometry.jsonable_row({"nested": {"text": text}}) == {
        "nested": {"text": "x" * 400 + geometry.ELLIPSIS}}
