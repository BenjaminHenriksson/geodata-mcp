"""STAC API harvesting: /collections → catalog.datasets (kind 'raster_ref').

Catalog visibility only: nothing renders STAC assets yet (pgSTAC/TiTiler are
deferred by decision), but harvested collections are searchable and their
asset/temporal metadata rides in schema_summary for when that lands. Global
collections keep a NULL extent_3014 (the shared bbox guard treats >10° spans
as unusable for the Sundsvall-local extent column); the raw bbox is preserved
in schema_summary."""

import logging

from connectors.ogcapi import _get_json
from connectors.wfs import _get_source, _upsert_dataset, _valid_bbox

log = logging.getLogger("worker.stac")

MAX_PAGES = 10
MAX_COLLECTIONS = 500
MAX_ASSETS = 20


def _stac_bbox(collection: dict):
    boxes = ((collection.get("extent") or {}).get("spatial") or {}).get("bbox") or []
    if not boxes or not isinstance(boxes[0], (list, tuple)):
        return None
    b = boxes[0]
    if len(b) >= 6:
        return (b[0], b[1], b[3], b[4])
    if len(b) >= 4:
        return (b[0], b[1], b[2], b[3])
    return None


def harvest_stac(conn, job) -> dict:
    """Job handler: STAC /collections → one catalog dataset (kind 'raster_ref')
    per collection, upserted on (source_id, external_id)."""
    source = _get_source(conn, job["payload"]["source_id"])
    if not source["url"]:
        raise ValueError(f"source {source['slug']} has no url")

    collections = []
    next_url = source["url"].rstrip("/") + "/collections"
    params = None
    for _ in range(MAX_PAGES):
        data = _get_json(next_url, params)
        collections.extend(data.get("collections") or [])
        if len(collections) >= MAX_COLLECTIONS:
            log.warning("harvest_stac %s: stopping at %d collections",
                        source["slug"], MAX_COLLECTIONS)
            break
        next_link = next((ln for ln in data.get("links") or []
                          if isinstance(ln, dict) and ln.get("rel") == "next"), None)
        if not next_link or not next_link.get("href"):
            break
        next_url, params = next_link["href"], None

    count = 0
    seen = set()
    with conn.cursor() as cur:
        for coll in collections[:MAX_COLLECTIONS]:
            if not isinstance(coll, dict):
                continue
            cid = coll.get("id")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            title = coll.get("title") or cid
            description = coll.get("description") or ""
            keywords = [k for k in coll.get("keywords") or [] if isinstance(k, str)]

            raw_bbox = _stac_bbox(coll)
            temporal = ((coll.get("extent") or {}).get("temporal") or {}).get("interval") or []
            assets = coll.get("item_assets") or {}
            summary = {"stac": {
                "license": coll.get("license") or "",
                "temporal": temporal[0] if temporal else None,
                "assets": sorted(assets)[:MAX_ASSETS] if isinstance(assets, dict) else [],
                "bbox_4326": list(raw_bbox) if raw_bbox else None,
            }}
            _upsert_dataset(cur, source["id"], cid, "raster_ref", title, description,
                            keywords, "", _valid_bbox(raw_bbox), schema_summary=summary)
            count += 1

    log.info("harvest_stac %s: %d collections", source["slug"], count)
    return {"datasets": count}
