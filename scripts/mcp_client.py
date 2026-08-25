"""Thin MCP streamable-HTTP client helper for scripts and E2E tests.

Auth: every request carries `Authorization: Bearer <api key>`. Keys come from
$GEODATA_API_KEY / $GEODATA_API_KEYS, falling back to the repo's .env
(scripts are documented to run from the repo root).
"""

import asyncio
import json
import os
from contextlib import asynccontextmanager

from mcp import ClientSession

try:  # SDK ≥2 renamed the helper and moved header config to an http_client arg
    from mcp.client.streamable_http import streamable_http_client as _transport
    _NEW_SDK = True
except ImportError:
    from mcp.client.streamable_http import streamablehttp_client as _transport
    _NEW_SDK = False

MCP_URL = "http://localhost:8080/mcp"
_ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def api_keys() -> list[str]:
    """Raw API keys, in order: $GEODATA_API_KEY, $GEODATA_API_KEYS, then .env."""
    if os.environ.get("GEODATA_API_KEY"):
        return [os.environ["GEODATA_API_KEY"].strip()]
    raw = os.environ.get("GEODATA_API_KEYS", "")
    if not raw and os.path.exists(_ENV_FILE):
        with open(_ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEODATA_API_KEYS="):
                    raw = line.split("=", 1)[1]
                    break
    return [k.strip() for k in raw.split(",") if k.strip()]


def default_api_key() -> str | None:
    keys = api_keys()
    return keys[0] if keys else None


@asynccontextmanager
async def mcp_session(url: str = MCP_URL, api_key: str | None = None):
    key = api_key if api_key is not None else default_api_key()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    if _NEW_SDK:
        from mcp.shared._httpx_utils import create_mcp_http_client
        async with create_mcp_http_client(headers=headers) as http:
            async with _transport(url, http_client=http) as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
    else:
        async with _transport(url, headers=headers) as streams:
            read, write = streams[0], streams[1]  # 2-tuple in new SDK, 3-tuple in old
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def unwrap(result):
    """CallToolResult -> python object (tools return JSON dicts)."""
    if getattr(result, "structuredContent", None):
        sc = result.structuredContent
        return sc.get("result", sc) if isinstance(sc, dict) else sc
    parts = []
    for c in result.content or []:
        if getattr(c, "text", None) is not None:
            parts.append(c.text)
    text = "\n".join(parts)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text


async def call(session, tool, **args):
    res = await session.call_tool(tool, args)
    out = unwrap(res)
    if isinstance(out, dict) and out.get("error"):
        print(f"  !! {tool} error: {out['error']}")
    return out


async def wait_job(session, job_id, timeout_s=900, poll_s=3, tool="load"):
    """Poll <tool>(op=status) until the job finishes (tool: "load" or "analyze").

    Long ingests outlive an idle SSE stream, so a transient transport error during the
    wait is retried rather than aborting a job that is still running server-side.
    """
    transport_errors = 0
    for _ in range(int(timeout_s / poll_s)):
        try:
            st = await call(session, tool, op="status", job_id=job_id)
            transport_errors = 0
        except Exception as e:  # stream ended, timeout, reconnect
            transport_errors += 1
            if transport_errors > 5:
                raise RuntimeError(f"lost contact while waiting for job {job_id}: {e}") from e
            await asyncio.sleep(poll_s)
            continue
        job = st.get("job", st)
        status = job.get("status")
        if status in ("done", "error"):
            return job
        await asyncio.sleep(poll_s)
    raise TimeoutError(f"job {job_id} did not finish in {timeout_s}s")
