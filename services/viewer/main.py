"""Viewer service: map pages, MapLibre/Origo compilation, GeoJSON + MVT endpoints,
and the auth-gated workspace manager UI."""
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import compile_maplibre
import compile_origo
import dbq
import page
import viewer_auth

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

# Content-Security-Policy for the HTML pages. This is load-bearing, not hardening
# theatre: Origo renders layer titles via createContextualFragment and feature-info
# values via innerHTML, and feature values are substituted client-side from the
# GeoJSON, so no amount of server-side escaping can reach them. Without
# 'unsafe-inline' in script-src, neither an injected <script> nor an inline
# `onerror=` handler executes; our own inline scripts are allowed by per-response
# nonce. That matters because the map pages share an origin with the cookie-authed
# /workspaces manager.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'nonce-{nonce}'{eval}; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https:; "
    "connect-src 'self' https:; "
    "worker-src 'self' blob:; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
)


def _html(body: str, nonce: str, status_code: int = 200,
          allow_eval: bool = False) -> HTMLResponse:
    """HTML response with CSP.

    `allow_eval` is only for the MapLibre page: MapLibre compiles style expressions
    with `new Function` inside its blob worker, which CSP blocks silently (the map
    never finishes loading, with no error event). It is safe to grant there and
    withheld everywhere else — 'unsafe-inline' stays out in every case, so injected
    markup and inline handlers never execute, which is the vector that matters. The
    Origo page in particular keeps the strict policy, since Origo is the renderer
    that injects titles as raw HTML.
    """
    return HTMLResponse(body, status_code=status_code, headers={
        "Content-Security-Policy": _CSP.format(
            nonce=nonce, eval=" 'unsafe-eval'" if allow_eval else ""),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    })


def _nonce() -> str:
    return secrets.token_urlsafe(16)


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


# ── workspace manager UI ─────────────────────────────────────────────────────

def _manager_key_id(request: Request):
    """api_key_id from a valid manager cookie (re-checked against the DB), or None."""
    key_id = viewer_auth.parse_cookie(request.cookies.get(viewer_auth.COOKIE_NAME))
    if key_id is None:
        return None
    with dbq.get_pool().connection() as conn:
        return key_id if dbq.api_key_valid(conn, key_id) else None


def _require_manager_enabled():
    if not viewer_auth.enabled():
        raise HTTPException(status_code=503,
                            detail="workspace manager disabled — set VIEWER_SECRET in .env")


@app.get("/")
def index():
    return RedirectResponse("/workspaces", status_code=302)


@app.get("/login", response_class=HTMLResponse)
def login_form():
    _require_manager_enabled()
    n = _nonce()
    return _html(page.login_page(), n)


@app.post("/login", response_class=HTMLResponse)
def login(key: str = Form("")):
    _require_manager_enabled()
    key = (key or "").strip()
    key_id = None
    if key:
        with dbq.get_pool().connection() as conn:
            key_id = dbq.api_key_id_for_hash(conn, viewer_auth.hash_key(key))
    if key_id is None:
        return _html(page.login_page("Unknown or disabled API key."), _nonce(), status_code=401)
    resp = RedirectResponse("/workspaces", status_code=303)
    resp.set_cookie(viewer_auth.COOKIE_NAME, viewer_auth.make_cookie(key_id),
                    max_age=viewer_auth.COOKIE_TTL_S, httponly=True, samesite="lax")
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(viewer_auth.COOKIE_NAME)
    return resp


@app.get("/workspaces", response_class=HTMLResponse)
def workspaces(request: Request):
    _require_manager_enabled()
    key_id = _manager_key_id(request)
    if key_id is None:
        return RedirectResponse("/login", status_code=302)
    with dbq.get_pool().connection() as conn:
        rows = dbq.workspaces_for_key(conn, key_id)
    return _html(page.workspaces_page(rows, viewer_auth.csrf_token(key_id)), _nonce())


@app.post("/workspaces/action", response_class=HTMLResponse)
def workspaces_action(request: Request, action: str = Form(""),
                      workspace_id: str = Form(""), new_name: str = Form(""),
                      csrf: str = Form("")):
    _require_manager_enabled()
    key_id = _manager_key_id(request)
    if key_id is None:
        return RedirectResponse("/login", status_code=302)
    if not viewer_auth.csrf_ok(key_id, csrf):
        raise HTTPException(status_code=403, detail="bad csrf token")

    error = None
    with dbq.get_pool().connection() as conn:
        row = dbq.workspace_owned(conn, key_id, workspace_id)
        if row is None:
            error = "unknown workspace"
        elif action == "activate":
            dbq.activate_workspace(conn, key_id, row[0])
        elif action == "rename":
            error = dbq.rename_workspace(conn, key_id, row[0], (new_name or "").strip())
        elif action == "delete":
            dbq.delete_workspace(conn, key_id, row[0], row[2])
        else:
            error = "unknown action"
        if error:
            rows = dbq.workspaces_for_key(conn, key_id)
            return _html(page.workspaces_page(rows, viewer_auth.csrf_token(key_id), error=error),
                         _nonce(), status_code=400)
    return RedirectResponse("/workspaces", status_code=303)


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
    n = _nonce()
    if renderer == "origo":
        return _html(page.origo_page(view_id, n), n)
    return _html(page.maplibre_page(view_id, n), n, allow_eval=True)


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
