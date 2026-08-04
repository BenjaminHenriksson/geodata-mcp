"""Security regression tests — each reproduces an attack or asserts a boundary.

Needs TWO api keys in GEODATA_API_KEYS (victim = first, attacker = second) so the
cross-principal checks exercise real distinct principals.

Run:  .venv/bin/python scripts/security_test.py
"""

import asyncio
import sys

import httpx

sys.path.insert(0, "scripts")
from mcp_client import api_keys, call, mcp_session  # noqa: E402

MCP_URL = "http://localhost:8080/mcp"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


async def raw_post(tool, args, api_key=None, session_id=None):
    """POST a tool call straight at the transport, bypassing the client handshake."""
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": "2025-06-18"}
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"
    if session_id is not None:
        headers["mcp-session-id"] = session_id
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": args}}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(MCP_URL, json=body, headers=headers)
        return r.status_code, r.text[:400]


async def main():
    keys = api_keys()
    if len(keys) < 2:
        print("need two keys in GEODATA_API_KEYS (victim, attacker) — aborting")
        sys.exit(2)
    victim_key, attacker_key = keys[0], keys[1]

    # ── attack 0: the front door — no/garbage credentials must never reach tools ──
    status, text = await raw_post("query", {"sql": "SELECT 1"})
    check("unauthenticated request gets 401", status == 401, f"HTTP {status}")
    status, text = await raw_post("query", {"sql": "SELECT 1"}, api_key="not-a-real-key")
    check("bad api key gets 401", status == 401, f"HTTP {status}")

    # ── victim principal creates private state ──────────────────────────────
    async with mcp_session(api_key=victim_key) as victim:
        await call(victim, "layer", op="drop", name="victim_secret")  # idempotent re-runs
        out = await call(victim, "layer", op="create", name="victim_secret",
                         sql="SELECT 1 AS x", notes="private")
        victim_table = out.get("table")
        check("victim workspace created", bool(victim_table), str(victim_table))
        victim_ws = (victim_table or "").split(".")[0]
        out = await call(victim, "map", op="upsert", title="Victim map",
                         layers=[{"ref": "ref.naturreservat"}])
        victim_view = out.get("view_id")
        check("victim view created", bool(victim_view), str(victim_view))

        # ── attacker is a different, validly-authenticated principal ────────
        async with mcp_session(api_key=attacker_key) as attacker:
            # attack 1: enumerate credentials / other principals' bookkeeping
            out = await call(attacker, "query", sql="SELECT key_hash FROM app.api_keys")
            check("app.api_keys not readable by agent_ro",
                  bool(out.get("error")), str(out.get("error"))[:90])
            out = await call(attacker, "query", sql="SELECT ws_schema FROM app.workspaces")
            check("app.workspaces not readable by agent_ro",
                  bool(out.get("error")), str(out.get("error"))[:90])
            out = await call(attacker, "workspace", op="list")
            schemas = {w.get("ws_schema") for w in out.get("workspaces", [])}
            # Non-vacuous: the attacker must SEE its own workspace and NOT the victim's.
            # (A broken list returning [] would otherwise pass this check.)
            own = await call(attacker, "workspace", op="current")
            check("workspace list hides other principals' workspaces",
                  bool(out.get("workspaces")) and own.get("ws_schema") in schemas
                  and victim_ws not in schemas,
                  f"attacker sees {schemas}, victim has {victim_ws}")

            # attack 2: map(op='get') leaking the owner id
            out = await call(attacker, "map", op="get", view_id=victim_view)
            check("map get does not expose workspace_id",
                  "workspace_id" not in out, str(list(out))[:110])

            # attack 3: hijack another principal's view
            out = await call(attacker, "map", op="upsert", view_id=victim_view,
                             title="HIJACKED", layers=[{"ref": "ref.strandskydd"}])
            check("cross-principal view upsert rejected",
                  bool(out.get("error")), str(out.get("error"))[:90])

            # attack 4: temp-table poisoning / read-only bypass
            out = await call(attacker, "query",
                             sql="SELECT 1 AS a INTO TEMP strandskydd")
            check("SELECT INTO TEMP rejected", bool(out.get("error")),
                  str(out.get("error"))[:90])
            out = await call(attacker, "query",
                             sql="SELECT current_setting('transaction_read_only') AS ro")
            check("query transaction is read-only",
                  out.get("rows") and out["rows"][0][0] == "on", str(out.get("rows")))

            # ── documented boundary, not a bug: workspaces are namespacing, not
            # tenancy. All agent SQL runs as one shared read-only role, so any
            # authenticated principal can READ any workspace. What must hold is
            # that it cannot WRITE one.
            out = await call(attacker, "query", sql=f"SELECT * FROM {victim_table}")
            check("cross-principal read allowed (documented)", not out.get("error"),
                  "shared agent_ro role — see README 'Security boundaries'")
            out = await call(attacker, "layer", op="drop", name="victim_secret")
            check("cross-principal workspace write rejected", bool(out.get("error")),
                  str(out.get("error"))[:90])

        # attack 5: authenticated caller rides a forged transport session id.
        # Harmless by design (identity is the per-request bearer key, not the
        # transport session), but the transport must still reject the bogus id.
        status, text = await raw_post("layer", {"op": "drop", "name": "victim_secret"},
                                      api_key=attacker_key,
                                      session_id="deadbeefdeadbeefdeadbeefdeadbeef")
        check("forged mcp-session-id rejected by transport", status >= 400,
              f"HTTP {status} {text[:80]}")

        # ── attack 6: stored XSS through a layer label into the Origo page ──
        # Origo renders layer titles as raw HTML (createContextualFragment) on the same
        # origin as the cookie-authed /workspaces manager, so labels must arrive escaped
        # and the page must carry a CSP that blocks inline script/handlers.
        payload = "<img src=x onerror=alert(1)>"
        out = await call(victim, "map", op="upsert", title="xss probe",
                         layers=[{"ref": "ref.naturreservat", "label": payload,
                                  "popup": ["objektid"]}])
        xss_view = out.get("view_id")
        async with httpx.AsyncClient(timeout=30) as c:
            cfg = (await c.get(f"http://localhost:8080/v/{xss_view}/origo.json")).text
            page_res = await c.get(f"http://localhost:8080/v/{xss_view}?renderer=origo")
        check("origo config escapes agent-supplied layer labels",
              payload not in cfg and "&lt;img" in cfg, cfg[:0] or "escaped")
        csp = page_res.headers.get("content-security-policy", "")
        check("origo page sends a script-src CSP without unsafe-inline",
              "script-src" in csp and "unsafe-inline" not in csp.split("style-src")[0],
              csp[:110])

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
