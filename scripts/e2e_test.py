"""End-to-end agent flow over MCP streamable HTTP.

workspace new → search → query (query_id logged) → layer create (provenance) →
layer update → reconnect (durability) → map upsert → map update (ETag polling
check) → export GPKG.

Run:  .venv/bin/python scripts/e2e_test.py
Exits non-zero on first hard failure; prints the map URL for browser inspection.
"""

import asyncio
import json
import sys

sys.path.insert(0, "scripts")
from mcp_client import call, mcp_session  # noqa: E402

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    if not ok:
        print("aborting on first failure")
        sys.exit(1)


async def main():
    async with mcp_session() as s:
        # 0. dedicated durable workspace for this test (idempotent across runs).
        # Assert the delete op explicitly rather than discarding it: a broken delete
        # would otherwise pass run 1 and fail run 2 with an unrelated error.
        existing = {w["name"] for w in (await call(s, "workspace", op="list"))["workspaces"]}
        out = await call(s, "workspace", op="delete", name="e2e")
        if "e2e" in existing:
            check("workspace delete removes an existing workspace",
                  out.get("deleted") == "e2e", str(out)[:120])
        else:
            check("workspace delete reports a clear error for an unknown name",
                  bool(out.get("error")) and "e2e" in str(out.get("error")), str(out)[:120])
        out = await call(s, "workspace", op="new", name="e2e")
        check("workspace create+activate", out.get("active") and out.get("ws_schema")
              and out.get("created") is True, str(out)[:140])
        # op='new' on an existing name must switch, not silently duplicate or wipe
        again = await call(s, "workspace", op="new", name="e2e")
        check("workspace new on existing name switches instead of duplicating",
              again.get("created") is False and again.get("ws_schema") == out.get("ws_schema"),
              str(again)[:140])
        out = await call(s, "workspace", op="list")
        names = {w.get("name"): w for w in out.get("workspaces", [])}
        check("workspace list shows e2e active", names.get("e2e", {}).get("is_active"),
              str(list(names))[:120])

        # 1. search
        out = await call(s, "search", query="strandskydd")
        ds = out.get("datasets", [])
        check("search finds strandskydd", any("strandskydd" in (d.get("title") or "").lower()
                                              or "strandskydd" in (d.get("external_id") or "").lower()
                                              for d in ds),
              f"{len(ds)} hits, embedding_used={out.get('embedding_used')}")

        # search by id -> full record
        first = next((d for d in ds if d.get("ref_table")), ds[0] if ds else None)
        if first:
            full = await call(s, "search", id=first["id"])
            check("search by id returns schema/provenance", "error" not in full, str(full)[:120])

        # 2. query: spatial SQL + geocode
        out = await call(s, "query", sql="SELECT count(*) AS n FROM ref.strandskydd")
        check("query counts ref.strandskydd", out.get("rows") and out["rows"][0][0] > 0, str(out.get("rows")))
        check("query returns query_id", bool(out.get("query_id")), out.get("query_id"))
        check("query tracks referenced tables",
              any("strandskydd" in t for t in (out.get("referenced_tables") or [])),
              str(out.get("referenced_tables")))

        out = await call(s, "query", sql="SELECT address, score FROM app.geocode('Storgatan 1') LIMIT 3")
        check("geocode via SQL works", "error" not in out and out.get("rows"), str(out)[:200])

        # write attempt through query must fail
        out = await call(s, "query", sql="DELETE FROM ref.strandskydd")
        check("query rejects writes", bool(out.get("error")), str(out.get("error"))[:100])

        # 3. layer create: buildings within 100 m of nature reserves (screening-style derivation)
        out = await call(s, "layer", op="create", name="hus_nara_naturreservat",
                        sql="""SELECT b.geom, b.fid AS bfid
                                 FROM ref.byggnader b
                                 JOIN ref.naturreservat nr ON ST_DWithin(b.geom, nr.geom, 100)
                                LIMIT 4000""",
                        notes="Buildings within 100 m of a nature reserve (demo derivation)")
        check("layer create", out.get("table") and out.get("row_count", 0) > 0,
              f"{out.get('table')} rows={out.get('row_count')}")
        ws_table = out.get("table")

        # provenance for the new layer
        out = await call(s, "query",
                        sql=f"SELECT kind, input_tables FROM app.provenance WHERE object_ref = '{ws_table}' ORDER BY id")
        kinds = [r[0] for r in out.get("rows", [])]
        check("provenance rows for layer", "layer_create" in kinds, str(out.get("rows"))[:200])

        # 4. layer update (write_attributes successor)
        out = await call(s, "query", sql=f"SELECT bfid FROM {ws_table} LIMIT 2")
        fids = [r[0] for r in out.get("rows", [])]
        out = await call(s, "layer", op="update", name="hus_nara_naturreservat",
                        key_column="bfid",
                        values={str(fids[0]): {"granskning": "prioriterad"},
                                str(fids[1]): {"granskning": "ok"}})
        check("layer update adds attribute", "error" not in out, str(out)[:150])

        # 5. durability: a brand-new connection (fresh mcp-session-id) must land in the
        # same workspace with the same tables — the point of API-key-keyed workspaces.
        async with mcp_session() as s2:
            out = await call(s2, "workspace", op="current")
            check("workspace survives reconnect", out.get("workspace") == "e2e",
                  f"current={out.get('workspace')} schema={out.get('ws_schema')}")
            out = await call(s2, "query", sql=f"SELECT count(*) FROM {ws_table}")
            check("layers survive reconnect", out.get("rows") and out["rows"][0][0] > 0,
                  str(out.get("rows")))

        # 6. map upsert
        out = await call(s, "map", op="upsert", title="Byggnader nära naturreservat",
                        layers=[
                            {"ref": "ref.strandskydd", "style": {"fill": "#1f78b4", "opacity": 0.3}},
                            {"ref": ws_table, "style": {"fill": "#e31a1c"},
                             "popup": ["bfid", "granskning"]},
                        ], legend=True)
        check("map upsert", out.get("view_id") and out.get("url"), out.get("url"))
        view_id, url = out["view_id"], out["url"]
        print(f"\n  MAP URL: {url}\n  ORIGO:   {url}?renderer=origo\n")

        # 7. map update (bump version — browser should pick up via ETag polling)
        out = await call(s, "map", op="upsert", view_id=view_id, title="Byggnader nära naturreservat (v2)",
                        layers=[
                            {"ref": "ref.strandskydd", "style": {"fill": "#1f78b4", "opacity": 0.3}},
                            {"ref": "ref.naturreservat", "style": {"fill": "#33a02c", "opacity": 0.35}},
                            {"ref": ws_table, "style": {"fill": "#e31a1c"},
                             "popup": ["bfid", "granskning"]},
                        ], legend=True)
        check("map version bump", out.get("version", 0) >= 2, f"version={out.get('version')}")

        # 8. export
        out = await call(s, "export", layers=[ws_table, "ref.strandskydd"], format="gpkg", cite=True)
        check("export returns signed url", bool(out.get("url")), str(out)[:200])
        print(json.dumps({"map_url": url, "view_id": view_id, "export": out}, indent=2))

    print(f"\nALL {len(CHECKS)} CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
