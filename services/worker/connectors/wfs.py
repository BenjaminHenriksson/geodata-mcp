"""WFS/WMS harvesting (GetCapabilities → catalog.datasets) and WFS ingestion
(ogr2ogr → PostGIS). XML parsing is namespace-agnostic: elements are matched
on their local tag names only.
"""

import logging
import urllib.parse
import xml.etree.ElementTree as ET

import httpx

import dbutil
from connectors import files

log = logging.getLogger("worker.wfs")

HTTP_TIMEOUT = 60.0
MAX_BBOX_DEGREES = 10.0  # wider than this → treat as global/bogus, skip extent


# ── namespace-agnostic XML helpers ──────────────────────────────────────────

def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _children(el, name):
    return [c for c in el if _local(c.tag) == name]


def _child_text(el, name) -> str:
    kids = _children(el, name)
    return (kids[0].text or "").strip() if kids else ""


def _iter_named(root, name):
    for el in root.iter():
        if _local(el.tag) == name:
            yield el


# ── shared harvest plumbing ─────────────────────────────────────────────────

def _get_source(conn, source_id):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM catalog.sources WHERE id = %s::uuid", (str(source_id),))
        row = cur.fetchone()
    if row is None:
        raise ValueError(f"unknown source_id: {source_id}")
    return row


def _fetch_capabilities(url: str, params: dict) -> ET.Element:
    resp = httpx.get(url, params=params, timeout=HTTP_TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    try:
        return ET.fromstring(resp.content)
    except ET.ParseError as exc:
        snippet = resp.text[:300].replace("\n", " ")
        raise RuntimeError(f"GetCapabilities is not valid XML ({exc}); starts: {snippet}") from exc


def _valid_bbox(bbox):
    """bbox = (lon1, lat1, lon2, lat2) in 4326; None if unusable/global."""
    if bbox is None:
        return None
    lon1, lat1, lon2, lat2 = bbox
    if not (-180.0 <= lon1 <= 180.0 and -180.0 <= lon2 <= 180.0):
        return None
    if not (-90.0 <= lat1 <= 90.0 and -90.0 <= lat2 <= 90.0):
        return None
    if lon2 < lon1 or lat2 < lat1:
        return None
    if (lon2 - lon1) > MAX_BBOX_DEGREES or (lat2 - lat1) > MAX_BBOX_DEGREES:
        return None
    return bbox


def _upsert_dataset(cur, source_id, external_id, kind, title, description, keywords,
                    crs_native, bbox) -> None:
    common = (
        "ON CONFLICT (source_id, external_id) DO UPDATE "
        "SET title = EXCLUDED.title, description = EXCLUDED.description, "
        "keywords = EXCLUDED.keywords, crs_native = EXCLUDED.crs_native, "
        "updated_at = now()"
    )
    if bbox is not None:
        cur.execute(
            "INSERT INTO catalog.datasets (source_id, external_id, kind, title, "
            "description, keywords, crs_native, extent_3014) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, "
            "ST_Transform(ST_MakeEnvelope(%s, %s, %s, %s, 4326), 3014)) " + common,
            (source_id, external_id, kind, title, description, keywords, crs_native,
             bbox[0], bbox[1], bbox[2], bbox[3]),
        )
    else:
        cur.execute(
            "INSERT INTO catalog.datasets (source_id, external_id, kind, title, "
            "description, keywords, crs_native) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) " + common,
            (source_id, external_id, kind, title, description, keywords, crs_native),
        )


# ── harvest_wfs ─────────────────────────────────────────────────────────────

def _parse_corners(lower: str, upper: str):
    """ows corners are 'lon lat' strings → (lon1, lat1, lon2, lat2) or None."""
    try:
        lon1, lat1 = (float(v) for v in lower.split()[:2])
        lon2, lat2 = (float(v) for v in upper.split()[:2])
    except (ValueError, IndexError):
        return None
    return (lon1, lat1, lon2, lat2)


def harvest_wfs(conn, job) -> dict:
    """Job handler: WFS 2.0.0 GetCapabilities → one catalog dataset (kind
    'vector') per FeatureType, upserted on (source_id, external_id)."""
    source = _get_source(conn, job["payload"]["source_id"])
    if not source["url"]:
        raise ValueError(f"source {source['slug']} has no url")
    root = _fetch_capabilities(
        source["url"],
        {"service": "WFS", "request": "GetCapabilities", "version": "2.0.0"},
    )

    count = 0
    seen = set()
    with conn.cursor() as cur:
        for ft in _iter_named(root, "FeatureType"):
            name = _child_text(ft, "Name")
            if not name or name in seen:
                continue
            seen.add(name)
            title = _child_text(ft, "Title") or name
            abstract = _child_text(ft, "Abstract")
            keywords = []
            for block in _children(ft, "Keywords"):
                for kw in _children(block, "Keyword"):
                    text = (kw.text or "").strip()
                    if text:
                        keywords.append(text)
            crs = _child_text(ft, "DefaultCRS") or _child_text(ft, "DefaultSRS")

            bbox = None
            for bb in _children(ft, "WGS84BoundingBox"):
                bbox = _parse_corners(_child_text(bb, "LowerCorner"),
                                      _child_text(bb, "UpperCorner"))
                if bbox:
                    break
            bbox = _valid_bbox(bbox)

            _upsert_dataset(cur, source["id"], name, "vector", title, abstract,
                            keywords, crs, bbox)
            count += 1

    log.info("harvest_wfs %s: %d feature types", source["slug"], count)
    return {"datasets": count}


# ── harvest_wms ─────────────────────────────────────────────────────────────

def _wms_bbox(layer_el):
    for bb in _children(layer_el, "EX_GeographicBoundingBox"):
        try:
            west = float(_child_text(bb, "westBoundLongitude"))
            east = float(_child_text(bb, "eastBoundLongitude"))
            south = float(_child_text(bb, "southBoundLatitude"))
            north = float(_child_text(bb, "northBoundLatitude"))
        except ValueError:
            return None
        return (west, south, east, north)
    return None


def harvest_wms(conn, job) -> dict:
    """Job handler: WMS 1.3.0 GetCapabilities → one catalog dataset (kind
    'raster_ref') per *named* Layer."""
    source = _get_source(conn, job["payload"]["source_id"])
    if not source["url"]:
        raise ValueError(f"source {source['slug']} has no url")
    root = _fetch_capabilities(
        source["url"],
        {"service": "WMS", "request": "GetCapabilities", "version": "1.3.0"},
    )

    count = 0
    seen = set()
    with conn.cursor() as cur:
        for layer in _iter_named(root, "Layer"):
            name = _child_text(layer, "Name")
            if not name or name in seen:
                continue
            seen.add(name)
            title = _child_text(layer, "Title") or name
            abstract = _child_text(layer, "Abstract")
            keywords = []
            for block in _children(layer, "KeywordList"):
                for kw in _children(block, "Keyword"):
                    text = (kw.text or "").strip()
                    if text:
                        keywords.append(text)

            bbox = _valid_bbox(_wms_bbox(layer))
            _upsert_dataset(cur, source["id"], name, "raster_ref", title, abstract,
                            keywords, "", bbox)
            count += 1

    log.info("harvest_wms %s: %d named layers", source["slug"], count)
    return {"datasets": count}


# ── ingest_wfs ──────────────────────────────────────────────────────────────

def _strip_query(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def ingest_wfs(conn, job) -> dict:
    """Job handler: ogr2ogr the dataset's WFS feature type into
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

    wfs_source = "WFS:" + _strip_query(dataset["source_url"])
    args = files.base_load_args(wfs_source, schema, table,
                                layer_name=dataset["external_id"])
    args += [
        "--config", "OGR_WFS_PAGING_ALLOWED", "ON",
        "--config", "OGR_WFS_PAGE_SIZE", "1000",
    ]
    files.run_ogr2ogr_with_retry(args)

    details = {
        "source_url": dataset["source_url"],
        "external_id": dataset["external_id"],
        "ogr_cmd": dbutil.redact(" ".join(args)),
    }
    return files.finalize_load(conn, job, schema, table,
                               dataset_id=dataset["id"], details=details)
