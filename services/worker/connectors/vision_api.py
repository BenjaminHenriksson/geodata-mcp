"""Configurable OpenAI-compatible transport for image analysis and document OCR."""
import base64
import json
import os
from urllib.parse import urlsplit


def model_info():
    base = os.environ.get("VISION_BASE_URL", "").strip().rstrip("/")
    model = os.environ.get("VISION_MODEL", "").strip()
    parsed = urlsplit(base)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise RuntimeError("VISION_BASE_URL must be an HTTP(S) API base URL without credentials, query or fragment")
    if not model:
        raise RuntimeError("VISION_MODEL must identify an image-capable model")
    return {"name": model, "base_url": base}


def _integer(name, default, maximum=None):
    try:
        value = int(os.environ.get(name, str(default)))
        if value < 1 or (maximum is not None and value > maximum):
            raise ValueError
        return value
    except ValueError:
        limit = f" from 1 to {maximum}" if maximum else " greater than zero"
        raise RuntimeError(f"{name} must be an integer{limit}") from None


def concurrency():
    return _integer("VISION_CONCURRENCY", 4, 8)


def max_output_tokens():
    return _integer("VISION_MAX_OUTPUT_TOKENS", 16384)


def api_key():
    model_info()
    # Local compatible endpoints may deliberately have no authentication.
    return os.environ.get("VISION_API_KEY", "").strip()


def _extra(name):
    try:
        value = json.loads(os.environ.get(name) or "{}")
        if not isinstance(value, dict) or {"model", "messages", "stream"} & value.keys():
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise RuntimeError(f"{name} must be a JSON object without model, messages or stream overrides") from None


def completion_request(content, *, schema=None, ocr=False):
    request = {
        "model": model_info()["name"], "temperature": 0.2,
        "max_tokens": max_output_tokens(), "stream": True,
        "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": content}],
    }
    format = os.environ.get("VISION_RESPONSE_FORMAT", "json_schema")
    if format == "json_schema" and schema is not None:
        request["response_format"] = {"type": "json_schema", "json_schema": schema}
    elif format in ("json_schema", "json_object"):
        request["response_format"] = {"type": "json_object"}
    elif format != "none":
        raise RuntimeError("VISION_RESPONSE_FORMAT must be json_schema, json_object or none")
    # Nonstandard routing/reasoning fields are opt-in configuration. OCR overrides
    # are separate so transcription need not use the same reasoning as analysis.
    extra = _extra("VISION_EXTRA_BODY")
    if ocr:
        extra.update(_extra("VISION_OCR_EXTRA_BODY"))
    for name, value in extra.items():
        if value is None:
            request.pop(name, None)
        else:
            request[name] = value
    return request


def vision_request(prompt, png, *, schema=None, ocr=False):
    return completion_request([
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
            "detail": "high"}},
    ], schema=schema, ocr=ocr)


def stream_json(client, key, request):
    """Return JSON (None when incomplete/invalid) and usage; discard reasoning."""
    content, usage, finish = "", {}, None
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    url = model_info()["base_url"] + "/chat/completions"
    with client.stream("POST", url, headers=headers, json=request) as response:
        if response.status_code != 200:
            raise RuntimeError(f"Vision endpoint request failed (HTTP {response.status_code})")
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            event = line[5:].strip()
            if event == "[DONE]":
                break
            try:
                data = json.loads(event)
                if not isinstance(data, dict):
                    raise ValueError
                if "error" in data:
                    raise RuntimeError("Vision endpoint returned an inference error")
                if data.get("usage"):
                    if not isinstance(data["usage"], dict):
                        raise ValueError
                    usage = data["usage"]
                for choice in data.get("choices", []):
                    content += choice.get("delta", {}).get("content") or ""
                    finish = choice.get("finish_reason") or finish
            except (ValueError, TypeError, AttributeError):
                raise RuntimeError("Vision endpoint returned an invalid event stream") from None
    try:
        result = json.loads(content) if finish == "stop" else None
    except ValueError:
        result = None
    return result, {k: usage.get(k, 0) or 0 for k in ("prompt_tokens", "completion_tokens", "cost")}
