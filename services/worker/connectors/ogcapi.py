"""OGC API Features: harvest (/collections → catalog.datasets, kind 'vector')
and ingest (ogr2ogr's OAPIF driver → PostGIS, mirroring ingest_wfs)."""

import logging

import httpx

import dbutil
import netauth
from connectors import files
from connectors.wfs import _get_source, _upsert_dataset, _valid_bbox

log = logging.getLogger("worker.ogcapi")

HTTP_TIMEOUT = 60.0
MAX_PAGES = 20  # /collections pagination cap — a landing page should not enumerate forever


def _get_json(url: str, params=None) -> dict:
    resp = httpx.get(url, params=params, timeout=HTTP_TIMEOUT, follow_redirects=True,
                     headers={"Accept": "application/json"},
                     auth=netauth.basic_auth_for(url))
    resp.raise_for_status()
    try:
        return resp.json()
    except ValueError as exc:
        snippet = resp.text[:300].replace("\n", " ")
        raise RuntimeError(f"{url} did not return JSON ({exc}); starts: {snippet}") from exc


def _spatial_bbox(collection: dict):
    """extent.spatial.bbox[0] in 4326 lon/lat; tolerates the 6-value 3D form."""
    boxes = ((collection.get("extent") or {}).get("spatial") or {}).get("bbox") or []
    if not boxes or not isinstance(boxes[0], (list, tuple)):
        return None
    b = boxes[0]
    if len(b) >= 6:
        return (b[0], b[1], b[3], b[4])
    if len(b) >= 4:
        return (b[0], b[1], b[2], b[3])
    return None


def harvest_ogcapi(conn, job) -> dict:
    """Job handler: OGC API Features /collections → one catalog dataset (kind
    'vector') per collection, upserted on (source_id, external_id)."""
    source = _get_source(conn, job["payload"]["source_id"])
    if not source["url"]:
        raise ValueError(f"source {source['slug']} has no url")

    collections = []
    next_url = source["url"].rstrip("/") + "/collections"
    params = {"f": "json"}
    for _ in range(MAX_PAGES):
        data = _get_json(next_url, params)
        collections.extend(data.get("collections") or [])
        next_link = next((ln for ln in data.get("links") or []
                          if isinstance(ln, dict) and ln.get("rel") == "next"), None)
        if not next_link or not next_link.get("href"):
            break
        next_url, params = next_link["href"], None

    count = 0
    seen = set()
    with conn.cursor() as cur:
        for coll in collections:
            if not isinstance(coll, dict):
                continue
            cid = coll.get("id") or coll.get("name")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            title = coll.get("title") or cid
            description = coll.get("description") or ""
            keywords = [k for k in coll.get("keywords") or [] if isinstance(k, str)]
            crs = coll.get("storageCrs") or ""
            bbox = _valid_bbox(_spatial_bbox(coll))
            _upsert_dataset(cur, source["id"], cid, "vector", title, description,
                            keywords, crs, bbox)
            count += 1

    log.info("harvest_ogcapi %s: %d collections", source["slug"], count)
    return {"datasets": count}


def ingest_ogcapi(conn, job) -> dict:
    """Job handler: ogr2ogr the dataset's OGC API Features collection into
    target_schema.table_name (EPSG:3014, MULTI, geom/fid/gist)."""
    payload = job["payload"]
    schema = dbutil.check_schema_name(payload["target_schema"])
    table = dbutil.check_table_name(payload["table_name"])

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.id, d.external_id, s.url AS source_url
              FROM catalog.datasets d
              JOIN catalog.sources s ON s.id = d.source_id
             WHERE d.id = %s::uuid
            """,
            (str(payload["dataset_id"]),),
        )
        dataset = cur.fetchone()
    if dataset is None:
        raise ValueError(f"unknown dataset_id: {payload['dataset_id']}")
    if not dataset["source_url"]:
        raise ValueError("dataset's source has no url")

    oapif_source = "OAPIF:" + dataset["source_url"]
    args = files.base_load_args(oapif_source, schema, table,
                                layer_name=dataset["external_id"])
    args += ["--config", "OGR_OAPIF_PAGE_SIZE", "1000"]
    files.run_ogr2ogr_with_retry(args)

    details = {
        "source_url": dataset["source_url"],
        "external_id": dataset["external_id"],
        "ogr_cmd": dbutil.redact(" ".join(args)),
    }
    return files.finalize_load(conn, job, schema, table,
                               dataset_id=dataset["id"], details=details)
