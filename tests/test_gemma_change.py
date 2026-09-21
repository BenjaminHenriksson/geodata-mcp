import json
import threading
from unittest.mock import MagicMock, Mock

import httpx
import pytest

import analysis_ops
from connectors import gemma_change as gemma


def detection(**kw):
    return {"concept": "building", "change_type": "extension", "confidence_label": "high",
            "before": "Grass beside an existing hall", "after": "Larger roof",
            "evidence": "Roof edges extend north", "bbox_1000": [100, 200, 400, 600], **kw}


WINDOW = {"tile_id": "r0c0", "ulx": 619650, "uly": 6922150,
          "lrx": 619850, "lry": 6921950}


@pytest.mark.parametrize("backend,method", [("sam3", "mask_compare"), ("gemma", "vision_compare")])
def test_backend_enqueued_with_matching_method(monkeypatch, backend, method):
    conn = MagicMock()
    conn.execute.return_value.fetchone.side_effect = [
        ("wms",), ("wms",), (None,), (None,), ("POLYGON EMPTY",), (False, 2, .01)]
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = conn
    monkeypatch.setattr(analysis_ops.db, "app_pool", lambda: pool)
    monkeypatch.setattr(analysis_ops.sessions, "ws_schema_for", lambda _: "ws_12345678")
    monkeypatch.setattr(analysis_ops.sessions, "ensure_ws_schema", lambda _: "ws_12345678")
    submit = Mock(return_value={"job_id": 42})
    monkeypatch.setattr(analysis_ops.job_ops, "submit", submit)
    result = analysis_ops.run("workspace", "change_detect", {
        "area": "0,0,100,100", "concepts": ["building"], "collection_a": "before",
        "collection_b": "after", "table_name": "changes", "backend": backend})
    assert result == {"job_id": 42}
    payload = submit.call_args.args[1]
    assert payload["backend"] == backend and payload["method"] == method
    assert submit.call_args.args[2] == "workspace"


@pytest.mark.parametrize("params", [{"backend": "unknown"}, {"backend": None},
    {"backend": "gemma", "method": "mask_compare"},
    {"backend": "sam3", "method": "vision_compare"}])
def test_invalid_backend_or_method_rejected_before_db(params):
    result = analysis_ops.run("workspace", "change_detect", {
        "area": "0,0,100,100", "concepts": ["building"], "collection_a": "before",
        "collection_b": "after", "table_name": "changes", **params})
    assert "error" in result


def test_box_mapping_preserves_northing_axis_and_change_semantics():
    row = gemma.candidate_rows(WINDOW, [detection()])[0]
    assert row[:5] == ("r0c0", "building", "changed", "extension", "high")
    assert row[-4:] == (619670, 6922030, 619730, 6922110)
    assert gemma.candidate_rows(WINDOW, [detection(change_type="demolition")])[0][2] == "disappeared"


@pytest.mark.parametrize("change", [
    detection(bbox_1000=[0, 0, 1001, 100]), detection(bbox_1000=[100, 0, 0, 100]),
    detection(bbox_1000=[0, 0, float("nan"), 100]), detection(bbox_1000=[False, 0, 100, 100]),
    detection(bbox_1000=[0, 0, 100]), detection(concept="unrequested"),
    detection(change_type="invented"), detection(confidence_label=.99), detection(evidence=""),
])
def test_invalid_model_output_is_not_silently_treated_as_no_change(change):
    with pytest.raises(ValueError, match="invalid change"):
        gemma.parse_changes(json.dumps({"changes": [change]}), ["building"])


def test_stream_request_and_response_contract():
    def handle(request):
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer fixture-secret"
        assert body["provider"] == {"only": ["deepinfra/turbo"], "allow_fallbacks": False}
        assert body["max_tokens"] == 16384 and body["reasoning"]["enabled"]
        parts = body["messages"][0]["content"]
        images = [p["image_url"] for p in parts if p["type"] == "image_url"]
        assert len(images) == 2 and all(x["detail"] == "high" for x in images)
        payload = json.dumps({"changes": [detection()]})
        events = [
            {"choices": [{"delta": {"reasoning": "not retained"}}]},
            {"choices": [{"delta": {"content": payload}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 123, "completion_tokens": 456, "cost": .001}},
        ]
        return httpx.Response(200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events)
                              + "data: [DONE]\n\n")
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        changes, usage = gemma.detect(client, "fixture-secret", {"a": b"before", "b": b"after"},
                                      ["building"], {"a": "2019", "b": "2023"}, WINDOW)
    assert changes == [detection()]
    assert usage == {"prompt_tokens": 123, "completion_tokens": 456, "cost": .001}


@pytest.mark.parametrize("finish,content", [("length", '{"changes": []}'),
                                           ("stop", "not json"), (None, "")])
def test_incomplete_or_invalid_reply_retains_usage_but_not_candidates(finish, content):
    data = {"choices": [{"delta": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20}}
    with httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text="data: " + json.dumps(data) + "\n\n"))) as client:
        changes, usage = gemma.detect(client, "key", {"a": b"a", "b": b"b"}, ["building"], {}, WINDOW)
    assert changes is None and usage["completion_tokens"] == 20


def test_provider_failure_does_not_leak_upstream_body():
    with httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(401, text="secret request data"))) as client:
        with pytest.raises(RuntimeError, match="HTTP 401") as error:
            gemma.detect(client, "fixture-secret", {"a": b"a", "b": b"b"}, ["building"], {}, WINDOW)
    assert "secret" not in str(error.value)


def test_concurrent_pairs_are_bounded_and_coverage_distinguishes_errors(monkeypatch):
    barrier = threading.Barrier(4, timeout=5)
    def detect(client, key, pngs, concepts, collections, window):
        n = int(window["tile_id"])
        if n < 4:
            barrier.wait()
        return (None if n == 4 else []), {"prompt_tokens": 20, "completion_tokens": 5, "cost": .001}
    monkeypatch.setattr(gemma, "detect", detect)
    statuses = {str(n): None for n in range(5)}
    pairs = (({**WINDOW, "tile_id": str(n)}, {"a": b"a", "b": b"b"}) for n in range(5))
    rows, model = gemma.infer(None, pairs, ["building"], {}, statuses, ("key", 4))
    assert rows == [] and list(statuses.values()) == ["analyzed"] * 4 + ["error"]
    assert model["requests"] == 5 and model["usage_cumulative"]["prompt_tokens"] == 100


def test_all_invalid_responses_fail_the_job(monkeypatch):
    monkeypatch.setattr(gemma, "detect", lambda *args: (None, {}))
    statuses = {"r0c0": None}
    with pytest.raises(RuntimeError, match="No image pair"):
        gemma.infer(None, [(WINDOW, {})], ["building"], {}, statuses, ("key", 1))
    assert statuses == {"r0c0": "error"}


def test_missing_key_does_not_contact_any_provider(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        gemma.settings()
