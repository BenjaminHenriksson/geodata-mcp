"""Live E2E for analyze(id='change_detect') with WMS orthophoto vintages.

Same flow as change_detect_test.py, but the vintages are the Lantmäteriet
orthophotos cascaded PUBLICLY by karta.sundsvall.se (source kind 'wms',
harvested by bootstrap_sundsvall.py) — no Lantmäteriet account needed. Runs a
real detection over a ~400x400 m box in central Sundsvall (Stenstan) between
the 2010 and 2023 vintages, checks the output tables, then maps the candidates
over the 2023 vintage itself.

Needs the stack up (docker compose up -d) AND the segmenter running on the
GPU host (services/segmenter/README.md), plus bootstrap_sundsvall.py done
(the sundsvall_wms source must be harvested).

Run from the repo root:
  uv run --with "mcp>=1.9" --with httpx python scripts/change_detect_wms_test.py
Aborts on the first failure; inference typically takes a few minutes.
"""

import asyncio
import os
import sys
import time

import httpx

from mcp_client import call, mcp_session, wait_job  # noqa: F401

SEGMENTER_URL = os.environ.get("SAM3_URL", "http://localhost:8200").rstrip("/")
COLLECTION_A = "Lantmateriet:Orto2010_wms"
COLLECTION_B = "Lantmateriet:Orto2023_wms"
TABLE = "chg_wms_1023"
# ~400x400 m around Stenstan, defined in 3006 and transformed to 3014 in-database.
AREA_SQL = ("SELECT ST_AsText(ST_Transform("
            "ST_MakeEnvelope(617800,6918800,618200,6919200,3006),3014))")

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""), flush=True)
    if not ok:
        print("aborting on first failure")
        sys.exit(1)


def preflight_segmenter():
    try:
        r = httpx.get(f"{SEGMENTER_URL}/healthz", timeout=5)
        body = r.json() if r.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        body = None
    if not body or not body.get("ok"):
        print(f"segmenter not reachable at {SEGMENTER_URL} — start it on the GPU host first "
              "(services/segmenter/README.md)")
        sys.exit(1)
    print(f"segmenter up: backend={body.get('backend')} "
          f"model_loaded={body.get('model_loaded')}", flush=True)


async def poll_job(s, job_id, timeout_s=1200, poll_s=10):
    """wait_job with progress output — model load + inference are slow."""
    t0 = time.monotonic()
    transport_errors = 0
    while time.monotonic() - t0 < timeout_s:
        try:
            st = await call(s, "analyze", op="status", job_id=job_id)
            transport_errors = 0
        except Exception as e:  # stream ended, timeout, reconnect
            transport_errors += 1
            if transport_errors > 5:
                raise RuntimeError(f"lost contact while waiting for job {job_id}: {e}") from e
            await asyncio.sleep(poll_s)
            continue
        job = st.get("job", st)
        status = job.get("status")
        elapsed = int(time.monotonic() - t0)
        print(f"  [{elapsed // 60:02d}:{elapsed % 60:02d}] job {job_id}: {status}", flush=True)
        if status in ("done", "error"):
            return job
        await asyncio.sleep(poll_s)
    raise TimeoutError(f"job {job_id} did not finish in {timeout_s}s")


def print_rows(label, out):
    print(f"  {label}:", flush=True)
    for row in out.get("rows") or []:
        print(f"    {row}", flush=True)


async def main():
    preflight_segmenter()
    async with mcp_session() as s:
        out = await call(s, "workspace", op="new", name="chgwmstest")
        ws = out.get("ws_schema")
        check("workspace chgwmstest active", out.get("active") and ws, str(out)[:140])

        # rerun safety: the op refuses name collisions, so clear leftovers (errors ignored)
        for t in (TABLE, f"{TABLE}_coverage"):
            await call(s, "layer", op="drop", name=t)

        # both WMS vintage layers must be in the catalog (bootstrap harvest)
        out = await call(s, "query", sql=f"""
            SELECT d.external_id FROM catalog.datasets d
              JOIN catalog.sources s ON s.id = d.source_id AND s.kind = 'wms'
             WHERE d.external_id IN ('{COLLECTION_A}', '{COLLECTION_B}')""")
        rows = out.get("rows") or []
        check("WMS vintage layers in catalog", len(rows) == 2,
              f"found {[r[0] for r in rows]} — run bootstrap_sundsvall.py first")

        out = await call(s, "query", sql=AREA_SQL)
        area_wkt = (out.get("rows") or [[None]])[0][0]
        check("test area WKT computed", area_wkt and area_wkt.startswith("POLYGON"),
              str(area_wkt)[:100])

        out = await call(s, "analyze", op="run", id="change_detect",
                         params={"area": area_wkt, "concepts": ["building"],
                                 "collection_a": COLLECTION_A, "collection_b": COLLECTION_B,
                                 "table_name": TABLE, "threshold": 0.4, "gsd": 0.25})
        job_id = out.get("job_id")
        check("change_detect (wms vintages) enqueued", bool(job_id), str(out)[:200])

        job = await poll_job(s, job_id)
        result = job.get("result") or {}
        check("change_detect job done", job.get("status") == "done",
              f"err={str(job.get('error'))[:300]}")
        print(f"  result: tiles_analyzed={result.get('tiles_analyzed')} "
              f"tiles_skipped={result.get('tiles_skipped')} "
              f"appeared={result.get('appeared')} disappeared={result.get('disappeared')} "
              f"changed={result.get('changed')} proc_gsd={result.get('proc_gsd')}", flush=True)
        if result.get("warning"):
            print(f"  warning: {result['warning']}", flush=True)
        check("proc_gsd is the requested WMS gsd", result.get("proc_gsd") == 0.25,
              str(result.get("proc_gsd")))
        check("some tiles analyzed", (result.get("tiles_analyzed") or 0) > 0,
              str(result.get("tiles_analyzed")))

        out = await call(s, "query", sql=f"""
            SELECT to_regclass('{ws}.{TABLE}') IS NOT NULL,
                   to_regclass('{ws}.{TABLE}_coverage') IS NOT NULL""")
        row = (out.get("rows") or [[False, False]])[0]
        check("result + coverage tables exist", row[0] and row[1], str(row))

        out = await call(s, "query", sql=f"""
            SELECT change_class, count(*) FROM {ws}.{TABLE}
             GROUP BY change_class ORDER BY change_class""")
        check("change_class counts queryable", "error" not in out, str(out)[:140])
        print_rows("change_class counts", out)

        out = await call(s, "query", sql=f"""
            SELECT concept, change_class, round(area_m2::numeric, 1),
                   round(confidence_a::numeric, 3), round(confidence_b::numeric, 3)
              FROM {ws}.{TABLE} ORDER BY area_m2 DESC LIMIT 3""")
        print_rows("sample candidates (concept, class, m2, conf_a, conf_b)", out)

        out = await call(s, "query", sql=f"""
            SELECT status, count(*) FROM {ws}.{TABLE}_coverage
             GROUP BY status ORDER BY status""")
        check("coverage status counts queryable",
              "error" not in out and out.get("rows"), str(out)[:140])
        print_rows("coverage status counts", out)

        # map the candidates over the 2023 vintage itself
        out = await call(s, "query", sql=f"""
            SELECT d.id::text FROM catalog.datasets d
              JOIN catalog.sources s ON s.id = d.source_id AND s.kind = 'wms'
             WHERE d.external_id = '{COLLECTION_B}' LIMIT 1""")
        rows = out.get("rows") or []
        ds_id = rows[0][0] if rows and rows[0] else None
        check(f"dataset {COLLECTION_B} in catalog", bool(ds_id), str(out)[:140])

        out = await call(s, "map", op="upsert", title="Förändringar 2010-2023 (WMS-vintage)",
                         layers=[
                             {"ref": f"wms:{ds_id}"},
                             {"ref": f"{ws}.{TABLE}",
                              "style": {"stroke": "#d62728", "fill": "#d62728",
                                        "opacity": 0.25}},
                         ])
        url = out.get("url")
        check("map upsert", out.get("view_id") and url, str(out)[:160])
        print(f"\n  MAP URL: {url}\n  ORIGO:   {url}?renderer=origo\n")

    print(f"ALL {len(CHECKS)} CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
