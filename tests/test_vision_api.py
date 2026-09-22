import json

import httpx
import pytest

from connectors import vision_api

pytestmark = pytest.mark.usefixtures("vision_endpoint")


@pytest.mark.parametrize("base,model,key", [
    ("https://images.example.test/api/v1/", "image-reader-v2", "fixture-token"),
    ("http://127.0.0.1:9080/v1", "local-vision", ""),
])
def test_endpoint_and_model_are_configurable_with_optional_auth(monkeypatch, base, model, key):
    monkeypatch.setenv("VISION_BASE_URL", base)
    monkeypatch.setenv("VISION_MODEL", model)
    monkeypatch.setenv("VISION_API_KEY", key)
    def handle(request):
        assert str(request.url) == base.rstrip("/") + "/chat/completions"
        assert request.headers.get("authorization") == (f"Bearer {key}" if key else None)
        body = json.loads(request.content)
        assert body["model"] == model and body["stream"] is True
        assert "reasoning" not in body and "provider" not in body
        event = {"choices": [{"delta": {"content": '{"text":"Hej"}'}, "finish_reason": "stop"}]}
        return httpx.Response(200, text='data:' + json.dumps(event) + '\n\ndata: [DONE]\n\n')
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result, _ = vision_api.stream_json(client, vision_api.api_key(), vision_api.vision_request("Read", b"image"))
    assert result == {"text": "Hej"}


def test_request_extensions_and_ocr_override_are_opt_in(monkeypatch):
    monkeypatch.setenv("VISION_MAX_OUTPUT_TOKENS", "32768")
    monkeypatch.setenv("VISION_EXTRA_BODY", json.dumps({
        "provider": {"only": ["test-route"]}, "reasoning": {"enabled": True},
        "temperature": None, "stream_options": None}))
    monkeypatch.setenv("VISION_OCR_EXTRA_BODY", '{"reasoning":{"enabled":false}}')
    answer = vision_api.vision_request("Answer", b"a")
    ocr = vision_api.vision_request("Transcribe", b"b", ocr=True)
    assert answer["provider"] == ocr["provider"] == {"only": ["test-route"]}
    assert answer["reasoning"]["enabled"] is True
    assert ocr["reasoning"]["enabled"] is False
    assert answer["max_tokens"] == ocr["max_tokens"] == 32768
    assert "temperature" not in answer and "stream_options" not in answer


@pytest.mark.parametrize("format", ["json_schema", "json_object", "none"])
def test_response_format_can_match_endpoint_capabilities(monkeypatch, format):
    monkeypatch.setenv("VISION_RESPONSE_FORMAT", format)
    request = vision_api.vision_request("Read", b"a", schema={"name": "page", "schema": {"type": "object"}})
    if format == "none":
        assert "response_format" not in request
    else:
        assert request["response_format"]["type"] == format


@pytest.mark.parametrize("name,value", [
    ("VISION_BASE_URL", ""), ("VISION_BASE_URL", "https://user:password@host/v1"),
    ("VISION_BASE_URL", "https://host/v1?token=secret"), ("VISION_MODEL", ""),
    ("VISION_EXTRA_BODY", "[]"), ("VISION_EXTRA_BODY", "bad secret json"),
    ("VISION_EXTRA_BODY", '{"messages":[]}'), ("VISION_OCR_EXTRA_BODY", '{"model":"other"}'),
    ("VISION_MAX_OUTPUT_TOKENS", "0"), ("VISION_RESPONSE_FORMAT", "invalid"),
])
def test_invalid_configuration_fails_without_leaking_values(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=name) as exc:
        vision_api.vision_request("Read", b"a", ocr=True)
    assert "secret" not in str(exc.value) and "password@" not in str(exc.value)


@pytest.mark.parametrize("value", ["0", "9", "many"])
def test_concurrency_is_bounded(monkeypatch, value):
    monkeypatch.setenv("VISION_CONCURRENCY", value)
    with pytest.raises(RuntimeError, match="VISION_CONCURRENCY"):
        vision_api.concurrency()
