import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from connectors import vision_api


@pytest.fixture
def streaming_server(monkeypatch):
    stop = threading.Event()
    connections = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def do_GET(self):
            connections.append(self.client_address)
            time.sleep(.25)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            mode = body["mode"]
            connections.append(self.client_address)
            assert self.headers.get("Connection", "").lower() == "close"
            event = {"choices": [{"delta": {"content": '{"changes": []}'}, "finish_reason": "stop"}]}
            payload = ("data: " + json.dumps(event)
                       + ("\n\ndata: [DONE]\n\n" if mode == "normal" else "")).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            if mode in ("normal", "eof"):
                self.send_header("Content-Length", str(len(payload)))
            else:
                self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.flush()
            if mode in ("normal", "eof"):
                self.wfile.write(payload)
                self.wfile.flush()
                return
            try:
                while not stop.wait(.01):
                    if mode != "silent":
                        self.wfile.write(b": heartbeat\n\n" if mode == "heartbeat" else b"x")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setenv("VISION_BASE_URL", url)
    monkeypatch.setenv("VISION_MODEL", "test")
    monkeypatch.setenv("VISION_STREAM_DEADLINE_SECONDS", ".08")
    yield url, connections
    stop.set()
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize("mode", ["heartbeat", "partial", "silent"])
def test_deadline_interrupts_real_http1_stream_and_not_another_request(streaming_server, mode):
    url, _ = streaming_server
    with httpx.Client(timeout=5, trust_env=False) as client, ThreadPoolExecutor(2) as pool:
        unrelated = pool.submit(client.get, url + "/ok")
        started = time.monotonic()
        with pytest.raises(vision_api.StreamDeadline, match="stream deadline"):
            vision_api.stream_json(client, "fixture-secret", {"mode": mode})
        assert time.monotonic() - started < .8  # well before the5s idle timeout
        assert unrelated.result().text == "ok"
        assert client.get(url + "/ok").status_code == 200


@pytest.mark.parametrize("mode", ["normal", "eof"])
def test_completion_never_reuses_a_socket_subject_to_a_deadline_timer(streaming_server, mode):
    url, connections = streaming_server
    with httpx.Client(timeout=5, trust_env=False) as client:
        result, _ = vision_api.stream_json(client, "", {"mode": mode})
        assert result == {"changes": []}
        assert client.get(url + "/ok").text == "ok"
        assert len(set(connections)) == 2


def test_deadline_never_shuts_down_http2_shared_socket(monkeypatch):
    class Stream:
        def get_extra_info(self, _):
            pytest.fail("HTTP2 socket must never be accessed")
    response = httpx.Response(200, text="data: [DONE]\n", extensions={
        "http_version": b"HTTP/2", "network_stream": Stream()})
    assert list(vision_api._stream_lines(response, 1)) == ["data: [DONE]"]


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "bad"])
def test_invalid_deadline_configuration(monkeypatch, value):
    monkeypatch.setenv("VISION_STREAM_DEADLINE_SECONDS", value)
    with pytest.raises(RuntimeError, match="finite positive"):
        vision_api.stream_deadline()
