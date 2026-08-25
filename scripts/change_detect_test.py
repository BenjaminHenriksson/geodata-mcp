"""Live E2E for analyze(id='change_detect') — SAM3 orthophoto change detection.

Runs a real detection over a ~400x400 m box in central Sundsvall (Stenstan)
between the 2021 and 2023 T2 orthophoto vintages, checks the output tables,
then maps the candidates over the årsvisa 2023 ortofoto WMS.

Needs the stack up (docker compose up -d) AND the segmenter running natively
on the host (services/segmenter/README.md). The worker reads the STAC COGs
with LANTMATERIET_CREDENTIALS from .env.

Run from the repo root:
  uv run --with "mcp>=1.9" --with httpx python scripts/change_detect_test.py
Aborts on the first failure; inference typically takes several minutes.
"""

import asyncio
import sys
import time

import httpx

from mcp_client import call, mcp_session, wait_job

SEGMENTER_URL = "http://localhost:8200"
ARSVISA_WMS_URL = ("https://maps.lantmateriet.se/ortofoto-ar/wms/v1.2"
                   "?request=GetCapabilities&version=1.1.1&service=WMS")
ARSVISA_2023_EXTERNAL_ID = "Ortofoto_farg_2023"
COLLECTION_A = "orto-t2-2021"
COLLECTION_B = "orto-t2-2023"
TABLE = "chg_2123"
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
        print(f"segmenter not reachable at {SEGMENTER_URL} — start it on the host first:\n"
              "  cd services/segmenter && uv run uvicorn app:app --host 0.0.0.0 --port 8200\n"
              "(see services/segmenter/README.md)")
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
        out = await call(s, "workspace", op="new", name="chgtest")
        ws = out.get("ws_schema")
        check("workspace chgtest active", out.get("active") and ws, str(out)[:140])

        # rerun safety: the op refuses name collisions, so clear leftovers (errors ignored)
        for t in (TABLE, f"{TABLE}_coverage"):
            await call(s, "layer", op="drop", name=t)

        out = await call(s, "query", sql=AREA_SQL)
        area_wkt = (out.get("rows") or [[None]])[0][0]
        check("test area WKT computed", area_wkt and area_wkt.startswith("POLYGON"),
              str(area_wkt)[:100])

        out = await call(s, "analyze", op="list")
        procs = {pr.get("id") for pr in out.get("processors") or []}
        check("analyze registry lists change_detect", "change_detect" in procs, str(out)[:160])
        out = await call(s, "analyze", op="describe", id="change_detect")
        schema = out.get("params_schema") or {}
        check("describe returns params schema", "concepts" in (schema.get("properties") or {}),
              str(out)[:160])

        # concepts in ENGLISH: SAM3's text grounding fails silently on Swedish
        # (byggnad finds nothing where 'building' scores 0.8+).
        out = await call(s, "analyze", op="run", id="change_detect",
                         params={"area": area_wkt,
                                 "concepts": ["building", "parking lot"],
                                 "collection_a": COLLECTION_A, "collection_b": COLLECTION_B,
                                 "table_name": TABLE, "threshold": 0.4})
        job_id = out.get("job_id")
        check("change_detect enqueued", bool(job_id), str(out)[:200])

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

        # årsvisa ortofoto WMS as visual backdrop — register is idempotent (re-harvests)
        out = await call(s, "load", op="register", kind="wms", url=ARSVISA_WMS_URL,
                         slug="lm_ortofoto_arsvisa", title="Årsvisa ortofoton (LM)")
        harvest_id = out.get("job_id")
        check("årsvisa WMS registered", bool(harvest_id), str(out)[:160])
        harvest = await wait_job(s, harvest_id, timeout_s=300)
        check("årsvisa WMS harvested", harvest.get("status") == "done",
              f"datasets={(harvest.get('result') or {}).get('datasets')} "
              f"err={str(harvest.get('error'))[:140]}")

        out = await call(s, "query", sql=f"""
            SELECT d.id::text FROM catalog.datasets d
              JOIN catalog.sources s ON s.id = d.source_id
             WHERE s.slug = 'lm_ortofoto_arsvisa'
               AND d.external_id = '{ARSVISA_2023_EXTERNAL_ID}' LIMIT 1""")
        rows = out.get("rows") or []
        ds_id = rows[0][0] if rows and rows[0] else None
        check(f"dataset {ARSVISA_2023_EXTERNAL_ID} in catalog", bool(ds_id), str(out)[:140])

        out = await call(s, "map", op="upsert", title="Förändringar 2021-2023",
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
