"""Viewer service: map pages, MapLibre/Origo compilation, GeoJSON + MVT endpoints."""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import compile_maplibre
import compile_origo
import dbq
import page

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

DATA_DEFAULT_LIMIT = 20000
DATA_MAX_LIMIT = 50000
SIMPLIFY_THRESHOLD = 5000


@asynccontextmanager
async def lifespan(_app):
    dbq.get_pool()
    yield
    dbq.close_pool()


app = FastAPI(title="geodata viewer", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False), name="static")


def _etag_matches(if_none_match, etag):
    if not if_none_match:
        return False

    def norm(v):
        v = v.strip()
        return v[2:] if v.startswith("W/") else v

    target = norm(etag)
    for candidate in if_none_match.split(","):
        c = candidate.strip()
        if c == "*" or norm(c) == target:
            return True
    return False


def _load_view(conn, view_id, status=404):
    if not isinstance(view_id, str) or not dbq.VIEW_ID_RE.match(view_id):
        raise HTTPException(status_code=status, detail="unknown view")
    view = dbq.get_view(conn, view_id)
    if view is None:
        raise HTTPException(status_code=status, detail="unknown view")
    return view


def _vector_refs(spec):
    refs = set()
    for entry in (spec or {}).get("layers") or []:
        if isinstance(entry, dict):
            ref = entry.get("ref")
            if isinstance(ref, str) and not ref.startswith("wms:") \
                    and dbq.split_layer_ref(ref) is not None:
                refs.add(ref)
    return refs


def _checked_layer(conn, layer, view_id):
    """Capability check for /data and /tiles; returns (schema, table, non-geom columns)."""
    parsed = dbq.split_layer_ref(layer)
    if parsed is None:
        raise HTTPException(status_code=404, detail="unknown layer")
    if not view_id:
        raise HTTPException(status_code=400, detail="view query parameter is required")
    view = _load_view(conn, view_id, status=403)
    if layer not in _vector_refs(view["spec"]):
        raise HTTPException(status_code=403, detail="layer is not part of this view")
    schema, table = parsed
    cols = dbq.columns(conn, schema, table)
    if cols is None:
        raise HTTPException(status_code=404, detail="layer table not found")
    if not dbq.has_geom(cols):
        raise HTTPException(status_code=400, detail="layer has no geom column")
    return schema, table, dbq.non_geom_columns(cols)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/v/{view_id}/style.json")
def style_json(view_id: str, request: Request):
    with dbq.get_pool().connection() as conn:
        view = _load_view(conn, view_id)
        # The compiled style depends on app.layer_meta as well as the view row, so the
        # fingerprint must cover both — otherwise `layer(op='style')` changes never reach
        # an open page, which polls with If-None-Match and would keep getting 304.
        etag = f'W/"{view["version"]}-{dbq.layer_meta_fingerprint(conn, view["spec"])}"'
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers={"ETag": etag})
        style = compile_maplibre.compile_style(conn, view)
    return JSONResponse(style, headers={"ETag": etag, "Cache-Control": "no-cache"})


@app.get("/v/{view_id}/origo.json")
def origo_json(view_id: str):
    with dbq.get_pool().connection() as conn:
        view = _load_view(conn, view_id)
        config = compile_origo.compile_origo(conn, view)
    return JSONResponse(config, headers={"Cache-Control": "no-cache"})


@app.get("/v/{view_id}", response_class=HTMLResponse)
def view_page(view_id: str, renderer: str = Query(default="maplibre")):
    if renderer not in ("maplibre", "origo"):
        raise HTTPException(status_code=400, detail="renderer must be 'maplibre' or 'origo'")
    with dbq.get_pool().connection() as conn:
        _load_view(conn, view_id)
    if renderer == "origo":
        return HTMLResponse(page.origo_page(view_id))
    return HTMLResponse(page.maplibre_page(view_id))


@app.get("/data/{layer}.geojson")
def data_geojson(layer: str,
                 view: str | None = Query(default=None),
                 crs: int = Query(default=4326),
                 limit: int = Query(default=DATA_DEFAULT_LIMIT)):
    if crs not in (4326, 3014):
        raise HTTPException(status_code=400, detail="crs must be 4326 or 3014")
    limit = max(1, min(limit, DATA_MAX_LIMIT))
    with dbq.get_pool().connection() as conn:
        schema, table, props = _checked_layer(conn, layer, view)
        count = dbq.feature_count(conn, schema, table)
        simplify = count is not None and count > SIMPLIFY_THRESHOLD
        body = dbq.geojson_feature_collection(conn, schema, table, props,
                                              crs, limit, simplify)
    return Response(content=body, media_type="application/geo+json",
                    headers={"Cache-Control": "no-store"})


@app.get("/tiles/{layer}/{z}/{x}/{y}.mvt")
def tiles_mvt(layer: str, z: int, x: int, y: int,
              view: str | None = Query(default=None)):
    if z < 0 or z > 22 or x < 0 or y < 0 or x >= 2 ** z or y >= 2 ** z:
        raise HTTPException(status_code=400, detail="tile coordinates out of range")
    with dbq.get_pool().connection() as conn:
        schema, table, props = _checked_layer(conn, layer, view)
        tile = dbq.mvt_tile(conn, schema, table, props, z, x, y)
    if not tile:
        return Response(status_code=204)
    return Response(content=tile, media_type="application/vnd.mapbox-vector-tile",
                    headers={"Cache-Control": "no-store"})
