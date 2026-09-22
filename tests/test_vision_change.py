import json
import threading
import time
from unittest.mock import MagicMock, Mock

import httpx
import pytest

import analysis_ops
from connectors import vision_change as vision


pytestmark = pytest.mark.usefixtures("vision_endpoint")

def detection(**kw):
    return {"concept": "building", "change_type": "extension", "confidence_label": "high",
            "before": "Grass beside an existing hall", "after": "Larger roof",
            "evidence": "Roof edges extend north", "bbox_1000": [100, 200, 400, 600], **kw}


WINDOW = {"tile_id": "r0c0", "ulx": 619650, "uly": 6922150,
          "lrx": 619850, "lry": 6921950}


@pytest.mark.parametrize("params,backend,method", [
    ({}, "vision", "vision_compare"),
    ({"backend": "sam3"}, "sam3", "mask_compare"),
    ({"backend": "vision"}, "vision", "vision_compare"),
])
def test_backend_enqueued_with_matching_method(monkeypatch, params, backend, method):
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
        "collection_b": "after", "table_name": "changes", **params})
    assert result == {"job_id": 42}
    payload = submit.call_args.args[1]
    assert payload["backend"] == backend and payload["method"] == method
    assert submit.call_args.args[2] == "workspace"


@pytest.mark.parametrize("params", [{"backend": "unknown"}, {"backend": None},
    {"backend": "vision", "method": "mask_compare"},
    {"backend": "sam3", "method": "vision_compare"}])
def test_invalid_backend_or_method_rejected_before_db(params):
    result = analysis_ops.run("workspace", "change_detect", {
        "area": "0,0,100,100", "concepts": ["building"], "collection_a": "before",
        "collection_b": "after", "table_name": "changes", **params})
    assert "error" in result


def test_box_mapping_preserves_northing_axis_and_change_semantics():
    row = vision.candidate_rows(WINDOW, [detection()])[0]
    assert row[:5] == ("r0c0", "building", "changed", "extension", "high")
    assert row[-4:] == (619670, 6922030, 619730, 6922110)
    assert vision.candidate_rows(WINDOW, [detection(change_type="demolition")])[0][2] == "disappeared"


@pytest.mark.parametrize("change", [
    detection(bbox_1000=[0, 0, 1001, 100]), detection(bbox_1000=[100, 0, 0, 100]),
    detection(bbox_1000=[0, 0, float("nan"), 100]), detection(bbox_1000=[False, 0, 100, 100]),
    detection(bbox_1000=[0, 0, 100]), detection(concept="unrequested"),
    detection(change_type="invented"), detection(confidence_label=.99), detection(evidence=""),
])
def test_invalid_model_output_is_not_silently_treated_as_no_change(change):
    with pytest.raises(ValueError, match="invalid change"):
        vision.parse_changes(json.dumps({"changes": [change]}), ["building"])


def test_stream_request_and_response_contract():
    def handle(request):
        body = json.loads(request.content)
        assert request.headers["authorization"] == "Bearer fixture-secret"
        assert "provider" not in body and "reasoning" not in body
        assert str(request.url) == "https://vision.example.test/v1/chat/completions"
        assert body["model"] == "test-vision-model"
        assert body["max_tokens"] == 16384
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
        changes, usage = vision.detect(client, "fixture-secret", {"a": b"before", "b": b"after"},
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
        changes, usage = vision.detect(client, "key", {"a": b"a", "b": b"b"}, ["building"], {}, WINDOW)
    assert changes is None and usage["completion_tokens"] == 20


def test_provider_failure_does_not_leak_upstream_body():
    with httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(401, text="secret request data"))) as client:
        with pytest.raises(RuntimeError, match="HTTP 401") as error:
            vision.detect(client, "fixture-secret", {"a": b"a", "b": b"b"}, ["building"], {}, WINDOW)
    assert "secret" not in str(error.value)


def test_concurrent_pairs_are_bounded_and_coverage_distinguishes_errors(monkeypatch):
    barrier = threading.Barrier(4, timeout=5)
    def detect(client, key, pngs, concepts, collections, window):
        n = int(window["tile_id"])
        if n < 4:
            barrier.wait()
        return (None if n == 4 else []), {"prompt_tokens": 20, "completion_tokens": 5, "cost": .001}
    monkeypatch.setattr(vision, "detect", detect)
    statuses = {str(n): None for n in range(5)}
    pairs = (({**WINDOW, "tile_id": str(n)}, {"a": b"a", "b": b"b"}) for n in range(5))
    rows, model = vision.infer(None, pairs, ["building"], {}, statuses, ("key", 4))
    assert rows == [] and list(statuses.values()) == ["analyzed"] * 4 + ["error"]
    assert model["requests"] == 5 and model["usage_cumulative"]["prompt_tokens"] == 100


def test_all_invalid_responses_fail_the_job(monkeypatch):
    monkeypatch.setattr(vision, "detect", lambda *args: (None, {}))
    statuses = {"r0c0": None}
    with pytest.raises(RuntimeError, match="No image pair"):
        vision.infer(None, [(WINDOW, {})], ["building"], {}, statuses, ("key", 1))
    assert statuses == {"r0c0": "error"}


def test_missing_endpoint_does_not_contact_any_provider(monkeypatch):
    monkeypatch.delenv("VISION_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="VISION_BASE_URL"):
        vision.settings()


def test_retry_after_is_respected_and_attempts_are_reported(monkeypatch):
    calls, cooldowns = [], []
    def detect(*args):
        calls.append(True)
        if len(calls) < 3:
            raise vision.vision_api.EndpointError(429, 7)
        return [], {"prompt_tokens": 9}
    monkeypatch.setattr(vision, "detect", detect)
    monkeypatch.setattr(vision._RetryGate, "defer", lambda self, seconds: cooldowns.append(seconds))
    statuses = {"r0c0": None}
    _, info = vision.infer(None, [(WINDOW, {})], ["building"], {}, statuses, ("key", 1))
    assert cooldowns == [7, 7]
    assert info["request_attempts"] == 3 and info["requests"] == 1
    assert info["usage_cumulative"]["prompt_tokens"] == 9 and info["complete"]


def test_partial_failure_is_explicit_and_does_not_discard_successful_tiles(monkeypatch):
    def detect(client, key, pngs, concepts, collections, w):
        if w["tile_id"] == "bad":
            raise vision.vision_api.EndpointError(422)
        return [detection()], {}
    monkeypatch.setattr(vision, "detect", detect)
    statuses = {"good": None, "bad": None}
    rows, info = vision.infer(None, [({**WINDOW, "tile_id": t}, {}) for t in statuses],
                              ["building"], {}, statuses, ("key", 2))
    assert len(rows) == 1 and statuses == {"good": "analyzed", "bad": "error"}
    assert info["tile_failures"] == {"bad": {"reason": "http_422", "attempts": 1}}
    assert not info["complete"]


def test_cancelled_run_does_not_submit_requests_or_claim_complete(monkeypatch):
    detect = Mock()
    monkeypatch.setattr(vision, "detect", detect)
    cancel = threading.Event()
    cancel.set()
    statuses = {"r0c0": None, "remaining": None}
    rows, info = vision.infer(None, [(WINDOW, {})], ["building"], {}, statuses,
                              ("key", 4), cancel=cancel)
    assert not rows and set(statuses.values()) == {"cancelled"}
    assert not info["complete"] and info["request_attempts"] == 0
    detect.assert_not_called()


def test_result_order_is_independent_of_completion_order(monkeypatch):
    def detect(client, key, pngs, concepts, collections, w):
        if w["tile_id"] == "a":
            time.sleep(.02)
        return [detection()], {}
    monkeypatch.setattr(vision, "detect", detect)
    statuses = {t: None for t in "ab"}
    rows, info = vision.infer(None, [({**WINDOW, "tile_id": t}, {}) for t in "ab"],
                              ["building"], {}, statuses, ("key", 2))
    assert len(rows) == 1 and rows[0].row[0] == "a"
    assert info["reconciliation"] == {
        "raw_candidates": 2, "candidates": 1, "method": "conservative_complete_link"}
    assert info["usage_cumulative"] == {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0}
    assert info["reported_usage_only"] is True


def test_retry_after_http_header_is_typed_without_leaking_body():
    with httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(429, headers={"Retry-After": "12"}, text="private"))) as client:
        with pytest.raises(vision.vision_api.EndpointError) as error:
            vision.detect(client, "key", {"a": b"a", "b": b"b"}, ["building"], {}, WINDOW)
    assert error.value.status_code == 429 and error.value.retry_after == 12
    assert "private" not in str(error.value)
