"""Security regression tests — each reproduces an attack confirmed by the review pass.

Run:  .venv/bin/python scripts/security_test.py
"""

import asyncio
import sys

import httpx

sys.path.insert(0, "scripts")
from mcp_client import call, mcp_session  # noqa: E402

MCP_URL = "http://localhost:8080/mcp"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


async def raw_call(session_id, tool, args):
    """Call a tool with a forged mcp-session-id, bypassing the client handshake."""
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "mcp-session-id": session_id,
               "MCP-Protocol-Version": "2025-06-18"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": args}}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(MCP_URL, json=body, headers=headers)
        return r.status_code, r.text[:400]


async def main():
    # ── victim session creates private state ────────────────────────────────
    async with mcp_session() as victim:
        out = await call(victim, "layer", op="create", name="victim_secret",
                         sql="SELECT 1 AS x", notes="private")
        victim_table = out.get("table")
        check("victim workspace created", bool(victim_table), str(victim_table))
        out = await call(victim, "map", op="upsert", title="Victim map",
                         layers=[{"ref": "ref.naturreservat"}])
        victim_view = out.get("view_id")
        check("victim view created", bool(victim_view), str(victim_view))

        # ── attack 1: dump session tokens through the query tool ────────────
        async with mcp_session() as attacker:
            out = await call(attacker, "query", sql="SELECT session_id FROM app.sessions")
            check("app.sessions not readable by agent_ro",
                  bool(out.get("error")), str(out.get("error"))[:90])

            # ── attack 2: map(op='get') leaking the owner's session id ──────
            out = await call(attacker, "map", op="get", view_id=victim_view)
            check("map get does not expose session_id",
                  "session_id" not in out, str(list(out))[:110])

            # ── attack 3: hijack another session's view ─────────────────────
            out = await call(attacker, "map", op="upsert", view_id=victim_view,
                             title="HIJACKED", layers=[{"ref": "ref.strandskydd"}])
            check("cross-session view upsert rejected",
                  bool(out.get("error")), str(out.get("error"))[:90])

            # ── attack 4: temp-table poisoning / read-only bypass ───────────
            out = await call(attacker, "query",
                             sql="SELECT 1 AS a INTO TEMP strandskydd")
            check("SELECT INTO TEMP rejected", bool(out.get("error")),
                  str(out.get("error"))[:90])
            out = await call(attacker, "query",
                             sql="SELECT current_setting('transaction_read_only') AS ro")
            check("query transaction is read-only",
                  out.get("rows") and out["rows"][0][0] == "on", str(out.get("rows")))

            # ── documented boundary, not a bug: workspaces are namespacing, not
            # tenancy. All agent SQL runs as one shared read-only role, so any session
            # can READ any workspace. What must hold is that it cannot WRITE one.
            out = await call(attacker, "query", sql=f"SELECT * FROM {victim_table}")
            check("cross-session read allowed (documented)", not out.get("error"),
                  "shared agent_ro role — see README 'Security boundaries'")
            out = await call(attacker, "layer", op="drop", name="victim_secret")
            check("cross-session workspace write rejected", bool(out.get("error")),
                  str(out.get("error"))[:90])

        # ── attack 6: replay a forged session id straight at the transport ──
        status, text = await raw_call("deadbeefdeadbeefdeadbeefdeadbeef", "layer",
                                      {"op": "drop", "name": "victim_secret"})
        check("forged session id rejected by transport", status >= 400,
              f"HTTP {status} {text[:80]}")

        # victim's state must still be intact
        out = await call(victim, "query", sql=f"SELECT count(*) FROM {victim_table}")
        check("victim table survived", out.get("rows") and out["rows"][0][0] == 1,
              str(out.get("rows")))
        out = await call(victim, "map", op="get", view_id=victim_view)
        check("victim view untouched", out.get("spec", {}).get("title") == "Victim map",
              str(out.get("spec", {}).get("title")))

        await call(victim, "layer", op="drop", name="victim_secret")

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} security checks passed")
    if failed:
        print("FAILED:", ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
