"""Shared Gemma transport for image analysis and document OCR."""
import base64
import json
import os

MODEL = "google/gemma-4-31b-it"
PROVIDER = "deepinfra/turbo"
URL = "https://openrouter.ai/api/v1/chat/completions"


def api_key():
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Gemma requires OPENROUTER_API_KEY in the worker environment")
    return key


def vision_request(prompt, png):
    return {
        "model": MODEL, "provider": {"only": [PROVIDER], "allow_fallbacks": False},
        "reasoning": {"enabled": True}, "temperature": 0.2, "max_tokens": 16384,
        "response_format": {"type": "json_object"}, "stream": True,
        "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
                "detail": "high"}},
        ]}],
    }


def stream_json(client, key, request):
    """Return JSON (None when incomplete/invalid) and usage; discard reasoning."""
    content, usage, finish = "", {}, None
    with client.stream("POST", URL, headers={"Authorization": f"Bearer {key}"},
                       json=request) as response:
        if response.status_code != 200:
            raise RuntimeError(f"Gemma/OpenRouter request failed (HTTP {response.status_code})")
        for line in response.iter_lines():
            if not line.startswith("data: ") or line[6:] == "[DONE]":
                continue
            try:
                data = json.loads(line[6:])
            except ValueError:
                raise RuntimeError("Gemma/OpenRouter returned an invalid event stream") from None
            if "error" in data:
                raise RuntimeError("Gemma/OpenRouter returned an inference error")
            if data.get("usage"):
                usage = data["usage"]
            for choice in data.get("choices", []):
                content += choice.get("delta", {}).get("content") or ""
                finish = choice.get("finish_reason") or finish
    try:
        result = json.loads(content) if finish == "stop" else None
    except ValueError:
        result = None
    return result, {k: usage.get(k, 0) or 0 for k in ("prompt_tokens", "completion_tokens", "cost")}
