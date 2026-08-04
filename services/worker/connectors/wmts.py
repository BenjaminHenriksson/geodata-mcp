"""WMTS harvesting: GetCapabilities → catalog.datasets (kind 'raster_ref') with
per-layer tile-matrix metadata in schema_summary, so the viewer compilers can
build GetTile URL templates (or decline when no compatible grid exists).

Only harvesting — WMTS is a raster cache, there is nothing to ingest. Rendering:
MapLibre needs a Web-Mercator matrix set (WebMercatorQuad/EPSG:3857/900913);
Origo would need a matrix set aligned index-for-index with the map's own
resolution ladder, which municipal caches rarely are, so the Origo compiler
skips WMTS layers with a note (their WMS twins render there instead)."""

import logging

from connectors.wfs import (_children, _child_text, _fetch_capabilities,
                            _get_source, _iter_named, _parse_corners,
                            _upsert_dataset, _valid_bbox)

log = logging.getLogger("worker.wmts")

MAX_MATRIX_IDS = 30   # per matrix set, plenty for any real zoom ladder
MAX_MATRIX_SETS = 8   # per layer — dataset metadata, not a capabilities mirror


def _parse_matrix_sets(contents):
    """Contents' direct TileMatrixSet definitions → {name: {crs, matrix_ids}}.

    Scoped to direct children: <TileMatrixSet> also appears inside each Layer's
    TileMatrixSetLink as a text-only reference, which _iter_named would confuse
    with the definitions.
    """
    sets = {}
    for tms in _children(contents, "TileMatrixSet"):
        ident = _child_text(tms, "Identifier")
        if not ident:
            continue
        matrices = _children(tms, "TileMatrix")
        sets[ident] = {
            "crs": _child_text(tms, "SupportedCRS"),
            "matrix_ids": [_child_text(m, "Identifier") for m in matrices][:MAX_MATRIX_IDS],
        }
    return sets


def harvest_wmts(conn, job) -> dict:
    """Job handler: WMTS 1.0.0 GetCapabilities → one catalog dataset (kind
    'raster_ref') per Layer, tile-grid metadata in schema_summary."""
    source = _get_source(conn, job["payload"]["source_id"])
    if not source["url"]:
        raise ValueError(f"source {source['slug']} has no url")
    root = _fetch_capabilities(
        source["url"],
        {"service": "WMTS", "request": "GetCapabilities", "version": "1.0.0"},
    )

    contents = next(_iter_named(root, "Contents"), None)
    if contents is None:
        raise RuntimeError("WMTS capabilities has no Contents section")
    matrix_sets = _parse_matrix_sets(contents)

    count = 0
    seen = set()
    with conn.cursor() as cur:
        for layer in _children(contents, "Layer"):
            ident = _child_text(layer, "Identifier")
            if not ident or ident in seen:
                continue
            seen.add(ident)
            title = _child_text(layer, "Title") or ident
            abstract = _child_text(layer, "Abstract")

            formats = [(f.text or "").strip() for f in _children(layer, "Format")]
            formats = [f for f in formats if f]

            style = ""
            for st in _children(layer, "Style"):
                st_id = _child_text(st, "Identifier")
                if not style or (st.get("isDefault") or "").lower() == "true":
                    style = st_id

            linked = {}
            for link in _children(layer, "TileMatrixSetLink"):
                name = _child_text(link, "TileMatrixSet")
                if name in matrix_sets and len(linked) < MAX_MATRIX_SETS:
                    linked[name] = matrix_sets[name]

            bbox = None
            for bb in _children(layer, "WGS84BoundingBox"):
                bbox = _parse_corners(_child_text(bb, "LowerCorner"),
                                      _child_text(bb, "UpperCorner"))
                if bbox:
                    break
            bbox = _valid_bbox(bbox)

            summary = {"wmts": {
                "matrix_sets": linked,
                "formats": formats[:5],
                "default_style": style,
            }}
            _upsert_dataset(cur, source["id"], ident, "raster_ref", title, abstract,
                            [], "", bbox, schema_summary=summary)
            count += 1

    log.info("harvest_wmts %s: %d layers", source["slug"], count)
    return {"datasets": count}
