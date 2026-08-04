"""Live E2E for the ogcapi / wmts / stac / text connectors + the official-layer
ingestability sweep. Every endpoint comes from data_sources.xlsx (the pilot's
official source list) — no third-party demo services.

  wfs    → every WFS layer named in the list, karta.sundsvall.se + Trafikverket
  wmts   → karta.sundsvall.se GWC (same official GeoServer), rendered on a map
  text   → the official NGP detaljplan page (lantmateriet.se, public)
  stac   → api.lantmateriet.se/stac-bild/v1        (needs LANTMATERIET_CREDENTIALS)
  wms    → maps.lantmateriet.se topowebb WMS       (needs LANTMATERIET_CREDENTIALS)
  ogcapi → LM NGP detaljplan API — no API URL in the list; set GEODATA_NGP_URL
           (plus credentials) to exercise it, otherwise reported as skipped.

Run from the repo root with the stack up (docker compose up -d):
  uv run --with "mcp>=1.9" --with httpx python scripts/connector_test.py
Collects all failures instead of aborting on the first.
"""

import asyncio
import os
import sys

import httpx

from mcp_client import _ENV_FILE, call, mcp_session, wait_job

SUNDSVALL_WFS_SLUG = "sundsvall_wfs"
TRAFIKVERKET_WFS_SLUG = "trafikverket_wfs"
WMTS_URL = "https://karta.sundsvall.se/geoserver/gwc/service/wmts"
WMTS_MAP_LAYER = "Lantmateriet:topowebbkartan_nedtonad"
TEXT_URL = "https://www.lantmateriet.se/sv/nationella-geodataplattformen/datamangder/detaljplan/"
LM_STAC_URL = "https://api.lantmateriet.se/stac-bild/v1"
LM_TOPOWEBB_WMS_URL = "https://maps.lantmateriet.se/topowebb/wms/v1"
BASE = "http://localhost:8080"

# The WFS layers data_sources.xlsx names explicitly ("all of them should be
# ingestable"). td_xxxx_* rows are a placeholder family in the list; the two
# named td_/bal_ layers below are its named members.
OFFICIAL_WFS_LAYERS = [
    "SundsvallsKommun:FastighetGrans_linje",
    "RIGES:DetaljplanGallande_minusNGP_yta",
    "RIGES:AnvandningsBestammelser_minusNGP_yta",
    "RIGES:AdministrativaEgenskaper_minusNGP_yta",
    "RIGES:EgenskapsBestammelser_minusNGP_linje",
    "RIGES:NGP_Detaljplan_yta",
    "RIGES:NGP_Anvandningsbestammelse_yta",
    "RIGES:NGP_Egenskapsbestammelse_linje",
    "RIGES:NGP_Egenskapsbestammelse_yta",
    "RIGES:NGP_Administrativbestammelse_yta",
    "Lansstyrelsen:RiksintresseKulturmiljovard_yta",
    "SundsvallsKommun:KulturmiljoprogramSundsvall_yta",
    "Lansstyrelsen:Strandskydd_yta",
    "Naturvardsverket:Naturreservat_yta",
    "SundsvallsKommun:bal_byggnad_yta",
    "SundsvallsKommun:td_ovrigbyggnad_yta",
    "SundsvallsKommun:Adressplats_punkt",
    "Lansstyrelsen:Byggnadsminnen_punkt",
]

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""), flush=True)


def skip(name, why):
    print(f"SKIP  {name}  — {why}", flush=True)


def env_var(name):
    if os.environ.get(name):
        return os.environ[name].strip()
    if os.path.exists(_ENV_FILE):
        with open(_ENV_FILE) as f:
            for line in f:
                if line.strip().startswith(name + "="):
                    return line.strip().split("=", 1)[1]
    return ""


def has_lm_credentials():
    return ":" in env_var("LANTMATERIET_CREDENTIALS")


async def one_value(s, sql):
    out = await call(s, "query", sql=sql)
    rows = out.get("rows") or []
    return rows[0][0] if rows and rows[0] else None


async def register_and_harvest(s, kind, url, title):
    out = await call(s, "load", op="register", kind=kind, url=url, title=title)
    job_id = out.get("job_id")
    check(f"{kind}: register {url} enqueues harvest", bool(job_id), str(out)[:140])
    if not job_id:
        return None
    job = await wait_job(s, job_id, timeout_s=300)
    n = (job.get("result") or {}).get("datasets")
    check(f"{kind}: harvest {url}", job.get("status") == "done" and (n or 0) > 0,
          f"status={job.get('status')} datasets={n} err={str(job.get('error'))[:140]}")
    return job


async def ingest_dataset(s, ds_id, table_name, timeout_s=900):
    out = await call(s, "load", op="ingest", dataset_id=ds_id,
                     table_name=table_name, target="workspace")
    if not out.get("job_id"):
        return None
    return await wait_job(s, out["job_id"], timeout_s=timeout_s)


async def test_wmts(s):
    job = await register_and_harvest(s, "wmts", WMTS_URL, "Sundsvall GWC WMTS")
    if not job or job.get("status") != "done":
        return
    n = (job.get("result") or {}).get("datasets") or 0
    check("wmts: full Sundsvall layer set harvested", n >= 300, f"{n} layers")
    ds_id = await one_value(s, f"""
        SELECT d.id::text FROM catalog.datasets d
          JOIN catalog.sources s ON s.id = d.source_id
         WHERE s.kind = 'wmts' AND d.external_id = '{WMTS_MAP_LAYER}' LIMIT 1""")
    check("wmts: topowebb layer harvested with metadata", bool(ds_id))
    if not ds_id:
        return
    out = await call(s, "map", op="upsert", title="connector-test WMTS",
                     layers=[{"ref": f"wms:{ds_id}"}])
    view_id = out.get("view_id")
    check("wmts: map accepts the raster ref", bool(view_id), str(out)[:140])
    if not view_id:
        return
    async with httpx.AsyncClient() as http:
        style = (await http.get(f"{BASE}/v/{view_id}/style.json")).json()
        tiles = [t for src in style.get("sources", {}).values()
                 for t in src.get("tiles", [])]
        gettile = [t for t in tiles if "REQUEST=GetTile" in t]
        check("wmts: MapLibre style carries a GetTile template",
              any("TILEMATRIXSET=WebMercatorQuad" in t and "TILEMATRIX={z}" in t
                  for t in gettile), str((gettile or tiles)[:1])[:160])
        if gettile:
            probe = gettile[0].replace("{z}", "13").replace("{x}", "4489").replace("{y}", "2266")
            r = await http.get(probe, timeout=30)
            check("wmts: GetTile template resolves to a real tile",
                  r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"),
                  f"{r.status_code} {r.headers.get('content-type')}")
        origo = (await http.get(f"{BASE}/v/{view_id}/origo.json")).json()
        note = (origo.get("geodata") or {}).get("note") or ""
        origo_wmts = [l for l in origo.get("layers", []) if l.get("type") == "WMTS"]
        check("wmts: Origo skips the layer with a note",
              "WMTS" in note and not origo_wmts, note[:120])


async def test_text(s):
    out = await call(s, "load", op="register", kind="text", url=TEXT_URL,
                     title="NGP detaljplan (officiell sida)")
    ds_id = out.get("dataset_id")
    check("text: register returns an immediate dataset_id", bool(ds_id), str(out)[:140])
    if not ds_id:
        return
    out = await call(s, "load", op="ingest", dataset_id=ds_id)
    job = await wait_job(s, out["job_id"], timeout_s=300) if out.get("job_id") else None
    chunks = ((job or {}).get("result") or {}).get("chunks")
    check("text: official NGP page ingested into doc corpus",
          job and job.get("status") == "done" and (chunks or 0) > 0,
          f"chunks={chunks} chars={((job or {}).get('result') or {}).get('chars')}")
    out = await call(s, "search", query="detaljplan nationella geodataplattformen")
    hits = [c for c in out.get("chunks", []) if "lantmateriet.se" in (c.get("source_url") or "")]
    check("text: doc search finds the page's chunks", bool(hits), f"{len(hits)} chunk hits")


async def test_lm_stac(s):
    if not has_lm_credentials():
        skip("stac: LM stac-bild harvest", "LANTMATERIET_CREDENTIALS not set")
        return
    await register_and_harvest(s, "stac", LM_STAC_URL, "LM Ortofoto STAC")


async def test_lm_wms(s):
    if not has_lm_credentials():
        skip("wms: LM topowebb harvest", "LANTMATERIET_CREDENTIALS not set")
        return
    await register_and_harvest(s, "wms", LM_TOPOWEBB_WMS_URL, "LM Topografisk webbkarta")


async def test_ogcapi(s):
    ngp_url = env_var("GEODATA_NGP_URL")
    if not ngp_url:
        skip("ogcapi: NGP detaljplan harvest",
             "data_sources.xlsx gives no API URL; set GEODATA_NGP_URL (+ credentials)")
        return
    job = await register_and_harvest(s, "ogcapi", ngp_url, "LM NGP detaljplan")
    if not job or job.get("status") != "done":
        return
    ds_id = await one_value(s, f"""
        SELECT d.id::text FROM catalog.datasets d
          JOIN catalog.sources s ON s.id = d.source_id
         WHERE s.kind = 'ogcapi' AND s.url = '{ngp_url}'
         ORDER BY d.external_id LIMIT 1""")
    if ds_id:
        job = await ingest_dataset(s, ds_id, "ngp_ogcapi_probe")
        fc = ((job or {}).get("result") or {}).get("feature_count")
        check("ogcapi: NGP collection ingests via OAPIF driver",
              job and job.get("status") == "done" and fc is not None,
              f"feature_count={fc} err={str((job or {}).get('error'))[:140]}")


async def test_official_wfs_sweep(s):
    failures, missing = [], []
    for i, external_id in enumerate(OFFICIAL_WFS_LAYERS):
        ds_id = await one_value(s, f"""
            SELECT d.id::text FROM catalog.datasets d
              JOIN catalog.sources s ON s.id = d.source_id
             WHERE s.slug = '{SUNDSVALL_WFS_SLUG}' AND d.kind = 'vector'
               AND d.external_id = '{external_id}' LIMIT 1""")
        if not ds_id:
            missing.append(external_id)
            print(f"  sweep {external_id}: NOT IN CATALOG", flush=True)
            continue
        job = await ingest_dataset(s, ds_id, f"sweep_{i}")
        fc = ((job or {}).get("result") or {}).get("feature_count")
        ok = job and job.get("status") == "done" and fc is not None
        print(f"  sweep {external_id}: " +
              (f"ok, {fc} features" if ok else f"FAILED: {str((job or {}).get('error'))[:140]}"),
              flush=True)
        if not ok:
            failures.append(external_id)
    check("wfs sweep: every official layer is in the harvested catalog", not missing,
          f"missing: {missing}" if missing else f"{len(OFFICIAL_WFS_LAYERS)} present")
    check("wfs sweep: every official layer ingests", not failures,
          f"failed: {failures}" if failures else "all ingested")

    vaghallare = await one_value(s, f"""
        SELECT d.id::text FROM catalog.datasets d
          JOIN catalog.sources s ON s.id = d.source_id
         WHERE s.slug = '{TRAFIKVERKET_WFS_SLUG}' AND d.kind = 'vector'
           AND (d.external_id ILIKE '%vaghallare%' OR d.title ILIKE '%vaghallare%'
                OR d.external_id ILIKE '%väghållare%' OR d.title ILIKE '%väghållare%')
         LIMIT 1""")
    check("wfs sweep: Trafikverket väghållare in catalog", bool(vaghallare))
    if vaghallare:
        job = await ingest_dataset(s, vaghallare, "sweep_vaghallare")
        fc = ((job or {}).get("result") or {}).get("feature_count")
        check("wfs sweep: Trafikverket väghållare ingests",
              job and job.get("status") == "done" and fc is not None,
              f"feature_count={fc} err={str((job or {}).get('error'))[:140]}")


async def main():
    async with mcp_session() as s:
        out = await call(s, "workspace", op="new", name="connector_test")
        check("workspace ready", bool(out.get("ws_schema")), str(out)[:120])
        await test_wmts(s)
        await test_text(s)
        await test_lm_stac(s)
        await test_lm_wms(s)
        await test_ogcapi(s)
        await test_official_wfs_sweep(s)

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAILED:\n  " + "\n  ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
