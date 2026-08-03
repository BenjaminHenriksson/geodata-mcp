"""Thin MCP streamable-HTTP client helper for scripts and E2E tests."""

import asyncio
import json
from contextlib import asynccontextmanager

from mcp import ClientSession

try:  # SDK ≥1.n renamed the helper
    from mcp.client.streamable_http import streamable_http_client as streamablehttp_client
except ImportError:
    from mcp.client.streamable_http import streamablehttp_client

MCP_URL = "http://localhost:8080/mcp"


@asynccontextmanager
async def mcp_session(url: str = MCP_URL):
    async with streamablehttp_client(url) as streams:
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


async def wait_job(session, job_id, timeout_s=900, poll_s=3):
    """Poll load(op=status) until the job finishes.

    Long ingests outlive an idle SSE stream, so a transient transport error during the
    wait is retried rather than aborting a job that is still running server-side.
    """
    transport_errors = 0
    for _ in range(int(timeout_s / poll_s)):
        try:
            st = await call(session, "load", op="status", job_id=job_id)
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
