"""Bootstrap the Sundsvall pilot data through the MCP load tool.

Registers the sources from data_sources.xlsx that are openly reachable, harvests their
capabilities into the catalog, ingests the pilot layers into ref.*, ingests the example
plan PDFs, and finally triggers catalog embedding. Loaders are the migration tool
(architecture §10.1) — this script only calls MCP tools, never SQL.

Run:  .venv/bin/python scripts/bootstrap_sundsvall.py
"""

import asyncio
import sys

sys.path.insert(0, "scripts")
from mcp_client import call, mcp_session, wait_job  # noqa: E402

SUNDSVALL_WFS = "https://karta.sundsvall.se/geoserver/ows"
SUNDSVALL_WMS = "https://karta.sundsvall.se/geoserver/ows"
TRAFIKVERKET_WFS = "https://isyroad.isy.se/maps/sundsvall/wfs"

# (source_slug, WFS typename, ref table name)
PILOT_LAYERS = [
    ("sundsvall_wfs", "SundsvallsKommun:Adressplats_punkt", "adressplats"),
    ("sundsvall_wfs", "SundsvallsKommun:FastighetGrans_linje", "fastighetsgrans"),
    ("sundsvall_wfs", "SundsvallsKommun:bal_byggnad_yta", "byggnader"),
    ("sundsvall_wfs", "SundsvallsKommun:td_ovrigbyggnad_yta", "ovriga_byggnader"),
    ("sundsvall_wfs", "Lansstyrelsen:Strandskydd_yta", "strandskydd"),
    ("sundsvall_wfs", "Naturvardsverket:Naturreservat_yta", "naturreservat"),
    ("sundsvall_wfs", "Lansstyrelsen:RiksintresseKulturmiljovard_yta", "riksintresse_kulturmiljo"),
    ("sundsvall_wfs", "SundsvallsKommun:KulturmiljoprogramSundsvall_yta", "kulturmiljoprogram"),
    ("sundsvall_wfs", "Lansstyrelsen:Byggnadsminnen_punkt", "byggnadsminnen"),
    ("sundsvall_wfs", "RIGES:DetaljplanGallande_yta", "detaljplan_gallande"),
    ("sundsvall_wfs", "RIGES:AnvandningsBestammelser_yta", "anvandningsbestammelser"),
    ("trafikverket_wfs", "sundsvall:Vaghallare_SUNDSVALL", "vaghallare"),
]

PDFS = [
    ("https://karta.sundsvall.se/Detaljplan/SkannadHandling/2281K-DP-294.pdf",
     "Detaljplan 2281K-DP-294 — planhandling"),
    ("https://karta.sundsvall.se/Detaljplan/SkannadKarta/2281K-DP-294.pdf",
     "Detaljplan 2281K-DP-294 — plankarta"),
]


async def register(session, **kw):
    out = await call(session, "load", op="register", **kw)
    print(f"registered {kw.get('slug', kw['title'])}: source={out.get('source_id')} job={out.get('job_id')}")
    if out.get("job_id"):
        job = await wait_job(session, out["job_id"])
        print(f"  harvest: {job.get('status')} {job.get('result') or job.get('error')}")
    return out


async def main():
    async with mcp_session() as session:
        print("== registering sources ==")
        await register(session, kind="wfs", url=SUNDSVALL_WFS, slug="sundsvall_wfs",
                       title="Sundsvalls kommun GeoServer WFS",
                       license="Öppna data (Sundsvalls kommun m.fl.)",
                       notes="Municipal GeoServer: kommun + länsstyrelsen + RIGES detaljplaner m.m.")
        await register(session, kind="wms", url=SUNDSVALL_WMS, slug="sundsvall_wms",
                       title="Sundsvalls kommun GeoServer WMS",
                       license="Öppna data (Sundsvalls kommun m.fl.)",
                       notes="Raster reference layers incl. ortofoton (Lantmateriet:Ortofoto50_25cm).")
        await register(session, kind="wfs", url=TRAFIKVERKET_WFS, slug="trafikverket_wfs",
                       title="ISY Road Sundsvall (Trafikverket) WFS",
                       license="Öppna data (Trafikverket/ISY)")

        # Resolve by exact external_id via catalog SQL, not fuzzy search: the same
        # typename exists as both a WFS vector dataset and a WMS raster_ref twin, and
        # only the vector one is ingestable.
        print("== resolving dataset ids ==")
        ds_ids = {}
        for slug, typename, table in PILOT_LAYERS:
            out = await call(session, "query", sql=(
                "SELECT d.id::text FROM catalog.datasets d "
                "JOIN catalog.sources s ON s.id = d.source_id "
                f"WHERE d.external_id = '{typename}' AND d.kind = 'vector' "
                f"AND s.slug = '{slug}'"))
            rows = out.get("rows") or []
            if not rows:
                print(f"  !! no vector dataset for {typename}")
                continue
            ds_ids[typename] = (rows[0][0], table)
            print(f"  {typename} -> {rows[0][0]}")

        print("== ingesting pilot layers into ref ==")
        for typename, (ds_id, table) in ds_ids.items():
            out = await call(session, "load", op="ingest", dataset_id=ds_id,
                             table_name=table, target="ref")
            job_id = out.get("job_id")
            if job_id and out.get("status") not in ("done", "error"):
                job = await wait_job(session, job_id)
            else:
                job = out
            print(f"  {table}: {job.get('status')} {job.get('result') or job.get('error')}")

        print("== ingesting plan PDFs ==")
        for url, title in PDFS:
            reg = await call(session, "load", op="register", kind="pdf", url=url, title=title)
            ds_id = reg.get("dataset_id")
            out = await call(session, "load", op="ingest", dataset_id=ds_id) if ds_id else reg
            if out.get("job_id"):
                job = await wait_job(session, out["job_id"])
                print(f"  {title}: {job.get('status')} {job.get('result') or job.get('error')}")

        print("== embedding catalog ==")
        out = await call(session, "load", op="embed")
        if out.get("job_id"):
            job = await wait_job(session, out["job_id"], timeout_s=3600, poll_s=10)
            print(f"  embed: {job.get('status')} {job.get('result') or job.get('error')}")

        print("== done ==")


if __name__ == "__main__":
    asyncio.run(main())
