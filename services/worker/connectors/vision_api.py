"""Configurable OpenAI-compatible transport for image analysis and document OCR."""
import base64
import json
import math
import os
import socket
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx


class EndpointError(RuntimeError):
    """Sanitized HTTP failure; callers can retry without retaining response bodies."""

    def __init__(self, status_code, retry_after=None):
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(f"Vision endpoint request failed (HTTP {status_code})")


class StreamDeadline(httpx.ReadTimeout):
    """Retryable failure with no upstream body, key, or partial output attached."""


def stream_deadline():
    try:
        value = float(os.environ.get("VISION_STREAM_DEADLINE_SECONDS", "600"))
        if not math.isfinite(value) or value <= 0:
            raise ValueError
        return value
    except ValueError:
        raise RuntimeError("VISION_STREAM_DEADLINE_SECONDS must be a finite positive number") from None


def _stream_lines(response, seconds):
    """Bound HTTP/1.x body time, including heartbeats and unterminated lines.

    HTTPX's documented network_stream extension exposes the standard socket in
    its synchronous HTTP/1.x backend (httpx0.28/httpcore1). Shutdown interrupts
    blocked socket reads; response.close alone does not reliably do so. Never
    shut down HTTP/2's shared socket. Custom transports/HTTP2 get byte-boundary
    deadline checks and their existing read timeout, not strict interruption.
    Connection, request-write and response-header timeouts remain client-owned.
    stream_json requests Connection:close so even natural EOF cannot return a
    socket to the pool while the deadline timer is firing.
    """
    deadline, expired = time.monotonic() + seconds, threading.Event()
    stream = response.extensions.get("network_stream")
    sock = None
    if stream is not None and response.extensions.get("http_version") in (b"HTTP/1.0", b"HTTP/1.1"):
        sock = stream.get_extra_info("socket")
        if not isinstance(sock, socket.socket):
            sock = None

    def expire():
        expired.set()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def check():
        if expired.is_set() or time.monotonic() >= deadline:
            raise StreamDeadline("Vision response exceeded its configured stream deadline")

    timer = threading.Timer(seconds, expire)
    timer.daemon = True
    timer.start()
    pending = bytearray()
    try:
        for chunk in response.iter_bytes():
            check()
            pending.extend(chunk)
            while True:
                endings = [i for i in (pending.find(b"\n"), pending.find(b"\r")) if i >= 0]
                if not endings:
                    break
                end = min(endings)
                yield pending[:end].decode("utf-8", errors="replace")
                del pending[:end + 1]
                check()
        check()
        if pending:
            yield pending.decode("utf-8", errors="replace")
    except (httpx.HTTPError, OSError):
        check()
        raise
    finally:
        # HTTPX can close on natural EOF before this finally block. These HTTP/1
        # requests prohibit connection reuse; joining also prevents stale timers.
        timer.cancel()
        timer.join()


def _retry_after(value):
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return None


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
    # HTTPX can release a connection at iterator EOF before our timer is joined.
    # A dedicated HTTP/1 connection avoids a deadline racing the next borrower.
    headers = {"Connection": "close"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    url = model_info()["base_url"] + "/chat/completions"
    seconds = stream_deadline()
    with client.stream("POST", url, headers=headers, json=request) as response:
        if response.status_code != 200:
            raise EndpointError(response.status_code, _retry_after(response.headers.get("Retry-After")))
        lines = _stream_lines(response, seconds)
        try:
            return _consume_events(lines)
        finally:
            lines.close()


def _consume_events(lines):
    content, usage, finish = "", {}, None
    for line in lines:
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
