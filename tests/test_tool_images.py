import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import audit
import pytest
from mcp.server.fastmcp.tools.base import Tool
from tool_images import with_image_content


def test_perspective_image_survives_audit_and_mcp_serialization(monkeypatch):
    conn = MagicMock()
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = conn
    monkeypatch.setattr(audit.db, "app_pool", lambda: pool)
    workspace = SimpleNamespace(id="owned", name="demo", api_key_id="principal")
    jpeg = b"\xff\xd8fixture\xff\xd9"

    @with_image_content
    @audit.tool(lambda *_: workspace)
    def analyze(op: str, ctx=None, workspace_id=None) -> dict:
        return {"camera": {"heading": None}, "_perspective_image": jpeg}

    result = analyze("run")
    metadata = result.structuredContent
    assert metadata["camera"]["heading"] is None
    assert metadata["audit_call_id"]
    assert "_perspective_image" not in metadata
    assert json.loads(result.content[0].text) == metadata
    assert result.content[1].mimeType == "image/jpeg"
    assert base64.b64decode(result.content[1].data) == jpeg
    # Exercise the actual SDK conversion, including output schema validation.
    assert Tool.from_function(analyze).fn_metadata.convert_result(result) == result
    writes = conn.execute.call_args_list
    assert len(writes) == 2
    assert "success" in writes[1].args[1]
    assert jpeg not in writes[1].args[1]


@pytest.mark.parametrize("result", [{"processors": []}, {"error": "auth denied"},
                                    {"job_id": 9, "status": "queued"}])
def test_existing_analysis_responses_are_unchanged(result):
    @with_image_content
    def analyze():
        return result
    assert analyze() is result


def test_unauthorized_call_never_renders_or_attaches_an_image():
    def denied(*args):
        raise audit.sessions.AuthError("denied")

    @with_image_content
    @audit.tool(denied)
    def analyze(op: str, ctx=None, workspace_id=None) -> dict:
        pytest.fail("Unauthorized image rendering")

    assert analyze("run") == {"error": "auth: denied"}
