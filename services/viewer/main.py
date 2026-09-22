"""Viewer service: map pages, MapLibre/Origo compilation, GeoJSON + MVT endpoints,
and the auth-gated workspace manager UI."""
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import UUID

import api_docs as api
import architecture_page
import compile_maplibre
import compile_origo
import dashboard_data
import dashboard_page
import dbq
import httpx
import imagery_routes
import obs
import page
import service_admin
import viewer_auth
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from geodata_common import netauth

# Centralised observability (#81): structured JSON logs to stdout. Additive and
# stdlib-only, so it never affects request handling. Configured at import time
# so every uvicorn worker logs in the same format.
obs.init_logging(service="viewer")
log = logging.getLogger("viewer.main")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

DATA_DEFAULT_LIMIT = 20000
DATA_MAX_LIMIT = 50000
SIMPLIFY_THRESHOLD = 5000


@asynccontextmanager
async def lifespan(_app):
    dbq.get_pool()
    log.info("viewer started", extra={"event": "startup"})
    yield
    dbq.close_pool()


app = FastAPI(
    title="Geodata MCP – Viewer & Data API", version="0.1.0", lifespan=lifespan,
    description=("Map pages, styles, GeoJSON, vector tiles, WMS proxy and workspace management. "
                 "Map/data endpoints use unguessable capability URLs; the requested layer must "
                 "belong to the named view. The workspace manager uses a signed HttpOnly cookie "
                 "and CSRF-protected actions. The separate MCP service at `/mcp` accepts a bearer "
                 "API key or OAuth token. Bundled assets are served under `/static/`."),
    license_info={"name": "AGPL-3.0-only", "url": "https://www.gnu.org/licenses/agpl-3.0.html"},
)
app.mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False), name="static")
# Prometheus scrape target (#81). ASGI sub-app; degrades to a plain-text 200 if
# prometheus_client is unavailable, so mounting never breaks startup.
app.mount("/metrics", obs.metrics_app(), name="metrics")

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
        # Identify the public site to the basemap provider without disclosing
        # capability-bearing view paths or query parameters.
        "Referrer-Policy": "strict-origin",
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


def _require_imagery_view(view_id):
    with dbq.get_pool().connection() as conn:
        _load_view(conn, view_id)


imagery_routes.register(app, _require_imagery_view)


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


@app.get("/healthz", tags=["health"], summary="Liveness probe", responses={
    200: api.content("Service is up.", schema={"type": "object", "required": ["ok"],
                     "properties": {"ok": {"type": "boolean"}}}, example={"ok": True}),
})
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


@app.get("/", tags=["manager"], summary="Redirect to the dashboard",
         response_class=RedirectResponse, status_code=302)
def index():
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/login", tags=["manager"], summary="API-key sign-in form",
         response_class=HTMLResponse, responses=api.MANAGER_DISABLED)
def login_form():
    _require_manager_enabled()
    n = _nonce()
    return _html(page.login_page(), n)


@app.post("/login", tags=["manager"], summary="Sign in", response_class=RedirectResponse,
          status_code=303, responses={**api.MANAGER_DISABLED,
              303: {"description": "Set signed gdw_auth cookie and redirect to /dashboard."},
              401: api.content("Unknown or disabled API key; login page.", "text/html", {"type": "string"})})
def login(key: str = Form("", description="API key used as the MCP bearer token; never stored in the cookie.")):
    _require_manager_enabled()
    key = (key or "").strip()
    key_id = None
    if key:
        with dbq.get_pool().connection() as conn:
            key_id = dbq.api_key_id_for_hash(conn, viewer_auth.hash_key(key))
    if key_id is None:
        return _html(page.login_page("Okänd eller inaktiverad API-nyckel."), _nonce(), status_code=401)
    resp = RedirectResponse("/dashboard", status_code=303)
    resp.set_cookie(viewer_auth.COOKIE_NAME, viewer_auth.make_cookie(key_id),
                    max_age=viewer_auth.COOKIE_TTL_S, httponly=True, samesite="lax")
    return resp


@app.post("/logout", tags=["manager"], summary="Clear the session cookie and redirect to /login",
          response_class=RedirectResponse, status_code=303)
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(viewer_auth.COOKIE_NAME)
    return resp


@app.get("/workspaces", tags=["manager"], summary="Workspace manager", response_class=HTMLResponse,
         dependencies=api.MANAGER_AUTH, responses={**api.MANAGER_DISABLED, **api.REDIRECT_LOGIN})
def workspaces(request: Request):
    _require_manager_enabled()
    key_id = _manager_key_id(request)
    if key_id is None:
        return RedirectResponse("/login", status_code=302)
    with dbq.get_pool().connection() as conn:
        rows = dbq.workspaces_for_key(conn, key_id)
        principal = dashboard_data.principal(conn, key_id)
    return _dashboard_response(page.workspaces_page(rows, viewer_auth.csrf_token(key_id), principal=principal))


@app.post("/workspaces/action", tags=["manager"], summary="Activate, rename or delete an owned workspace",
          dependencies=api.MANAGER_AUTH, response_class=RedirectResponse, status_code=303,
          responses={**api.MANAGER_DISABLED, **api.REDIRECT_LOGIN,
                     **api.errors({"403": "Bad CSRF token."}),
                     303: {"description": "Action applied; redirect to /workspaces."},
                     400: api.content("Invalid workspace, action or name; manager page.", "text/html", {"type": "string"})})
def workspaces_action(request: Request, action: str = Form("", description="activate, rename or delete"),
                      workspace_id: str = Form("", description="Owned workspace UUID"),
                      new_name: str = Form("", description="Required for rename: ^[a-z0-9][a-z0-9_-]{0,39}$"),
                      csrf: str = Form("", description="Per-principal CSRF token from the manager form")):
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
            error = "Arbetsytan finns inte."
        elif action == "activate":
            dbq.activate_workspace(conn, key_id, row[0])
        elif action == "rename":
            error = dbq.rename_workspace(conn, key_id, row[0], (new_name or "").strip())
        elif action == "delete":
            dbq.delete_workspace(conn, key_id, row[0], row[2])
        else:
            error = "Okänd åtgärd."
        if error:
            rows = dbq.workspaces_for_key(conn, key_id)
            return _html(page.workspaces_page(rows, viewer_auth.csrf_token(key_id), error=error,
                                                principal=dashboard_data.principal(conn, key_id)),
                         _nonce(), status_code=400)
    return RedirectResponse("/workspaces", status_code=303)


# Dashboard pages use the same signed cookie as the workspace manager. Admin status
# is read from the database on every request, never accepted from the browser.
def _dashboard_principal(request):
    _require_manager_enabled()
    key_id = _manager_key_id(request)
    if key_id is None:
        return None
    with dbq.get_pool().connection() as conn:
        return dashboard_data.principal(conn, key_id)


def _dashboard_response(body):
    response = _html(body, _nonce())
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.get("/architecture", response_class=HTMLResponse, tags=["manager"],
         summary="Interactive system architecture", dependencies=api.MANAGER_AUTH,
         responses={**api.MANAGER_DISABLED, **api.REDIRECT_LOGIN})
def architecture(request: Request, lang: str = Query("sv", pattern="^(sv|en)$")):
    principal = _dashboard_principal(request)
    if principal is None:
        return RedirectResponse("/login", status_code=302)
    return _dashboard_response(architecture_page.render(
        principal, viewer_auth.csrf_token(principal["id"]), lang=lang))


@app.get("/dashboard", response_class=HTMLResponse, tags=["manager"],
         summary="Workspace overview", dependencies=api.MANAGER_AUTH,
         responses={**api.MANAGER_DISABLED, **api.REDIRECT_LOGIN})
def dashboard(request: Request, page_number: int = Query(1, alias="page", ge=1, le=100000)):
    principal = _dashboard_principal(request)
    if principal is None:
        return RedirectResponse("/login", status_code=302)
    with dbq.get_pool().connection() as conn:
        data = dashboard_data.overview(conn, principal["id"], page=page_number)
    return _dashboard_response(dashboard_page.overview_page(
        data, principal, viewer_auth.csrf_token(principal["id"]), current=page_number))


@app.get("/admin", response_class=HTMLResponse, tags=["admin"],
         summary="Administrator overview", dependencies=api.MANAGER_AUTH,
         responses={**api.errors({"403": "Administrator access required."}), **api.MANAGER_DISABLED, **api.REDIRECT_LOGIN})
def admin_dashboard(request: Request, page_number: int = Query(1, alias="page", ge=1, le=100000)):
    principal = _dashboard_principal(request)
    if principal is None:
        return RedirectResponse("/login", status_code=302)
    if not principal["is_admin"]:
        raise HTTPException(status_code=403, detail="administrator access required")
    with dbq.get_pool().connection() as conn:
        data = dashboard_data.overview(conn, principal["id"], admin=True, page=page_number)
    return _dashboard_response(dashboard_page.overview_page(
        data, principal, viewer_auth.csrf_token(principal["id"]), admin=True, current=page_number))


@app.get("/admin/audit", response_class=HTMLResponse, tags=["admin"],
         summary="SQL and MCP audit history", dependencies=api.MANAGER_AUTH,
         responses={**api.errors({"403": "Administrator access required."}), **api.MANAGER_DISABLED, **api.REDIRECT_LOGIN})
def admin_audit(request: Request, kind: str = Query("", pattern="^(|mcp|sql)$"),
                status: str = Query("", pattern="^(|success|error|incomplete)$"),
                page_number: int = Query(1, alias="page", ge=1, le=100000)):
    principal = _dashboard_principal(request)
    if principal is None:
        return RedirectResponse("/login", status_code=302)
    if not principal["is_admin"]:
        raise HTTPException(status_code=403, detail="administrator access required")
    with dbq.get_pool().connection() as conn:
        data = dashboard_data.audit_events(conn, kind=kind, status=status, page=page_number)
    return _dashboard_response(dashboard_page.admin_audit_page(
        data, principal, viewer_auth.csrf_token(principal["id"]),
        kind=kind, status=status, current=page_number))


@app.get("/admin/services", response_class=HTMLResponse, tags=["admin"],
         summary="Service health and maintenance history", dependencies=api.MANAGER_AUTH,
         responses={**api.errors({"403": "Administrator access required."}), **api.MANAGER_DISABLED, **api.REDIRECT_LOGIN})
def admin_services(request: Request, accepted: bool = Query(False)):
    principal = _dashboard_principal(request)
    if principal is None:
        return RedirectResponse("/login", status_code=302)
    if not principal["is_admin"]:
        raise HTTPException(status_code=403, detail="administrator access required")
    data, error = None, None
    try:
        data = service_admin.request("GET", "/status")
    except service_admin.Unavailable as exc:
        error = str(exc)
    return _dashboard_response(service_admin.service_page(
        data, principal, viewer_auth.csrf_token(principal["id"]), error=error, accepted=accepted))


@app.post("/admin/services/action", response_class=RedirectResponse, status_code=303,
          tags=["admin"], summary="Request an allowed service start or restart", dependencies=api.MANAGER_AUTH,
          responses={**api.REDIRECT_LOGIN, **api.errors({"400": "Invalid request ID or action.",
                     "403": "Administrator access required or bad CSRF token."}),
                     503: api.content("Controller unavailable.", "text/html", {"type": "string"})})
def admin_service_action(request: Request, service: str = Form(""), action: str = Form(""),
                         request_id: str = Form(""), csrf: str = Form("")):
    principal = _dashboard_principal(request)
    if principal is None:
        return RedirectResponse("/login", status_code=302)
    if not principal["is_admin"]:
        raise HTTPException(status_code=403, detail="administrator access required")
    if not viewer_auth.csrf_ok(principal["id"], csrf):
        raise HTTPException(status_code=403, detail="bad csrf token")
    try:
        UUID(request_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid request id") from None
    if action not in ("start", "restart"):
        raise HTTPException(status_code=400, detail="unsupported maintenance action")
    try:
        service_admin.request("POST", "/actions", {
            "id": request_id, "service": service, "action": action,
            "actor_id": principal["id"], "actor": (principal["name"] or principal["id"])[:200]})
    except service_admin.Unavailable as exc:
        response = _dashboard_response(service_admin.service_page(
            None, principal, viewer_auth.csrf_token(principal["id"]), error=str(exc)))
        response.status_code = 503
        return response
    return RedirectResponse("/admin/services?accepted=true", status_code=303)


@app.get("/workspaces/{workspace_id}", response_class=HTMLResponse, tags=["manager"],
         summary="Workspace layers, maps and audit history", dependencies=api.MANAGER_AUTH,
         responses={**api.errors({"404": "Unknown or unowned workspace."}), **api.MANAGER_DISABLED, **api.REDIRECT_LOGIN})
def workspace_dashboard(workspace_id: str, request: Request,
                        kind: str = Query("", pattern="^(|mcp|sql)$"),
                        status: str = Query("", pattern="^(|success|error|incomplete)$"),
                        page_number: int = Query(1, alias="page", ge=1, le=100000)):
    principal = _dashboard_principal(request)
    if principal is None:
        return RedirectResponse("/login", status_code=302)
    with dbq.get_pool().connection() as conn:
        workspace = dashboard_data.workspace(conn, principal["id"], workspace_id,
                                             admin=principal["is_admin"])
        if workspace is None:
            raise HTTPException(status_code=404, detail="unknown workspace")
        data = dashboard_data.audit_events(conn, workspace_id=workspace["id"],
                                           kind=kind, status=status, page=page_number)
    return _dashboard_response(dashboard_page.workspace_page(
        workspace, data, principal, viewer_auth.csrf_token(principal["id"]),
        kind=kind, status=status, current=page_number))


@app.get("/v/{view_id}/style.json", tags=["map"], summary="MapLibre style document",
         responses={**api.UNKNOWN_VIEW,
                    200: {**api.content("MapLibre GL style, version 8."), "headers": api.ETAG},
                    304: {"description": "ETag matched; not modified.", "headers": api.ETAG}},
         openapi_extra={"parameters": [{"name": "If-None-Match", "in": "header", "required": False,
                                       "schema": {"type": "string"}, "description": "Previously returned ETag."}]})
def style_json(view_id: api.ViewId, request: Request):
    """Conditional GET fingerprints the view, layer metadata and compiler for near-live updates."""
    with dbq.get_pool().connection() as conn:
        view = _load_view(conn, view_id)
        # The compiled style depends on app.layer_meta as well as the view row, so the
        # fingerprint must cover both — otherwise `layer(op='style')` changes never reach
        # an open page, which polls with If-None-Match and would keep getting 304.
        etag = (f'W/"{view["version"]}-{dbq.layer_meta_fingerprint(conn, view["spec"])}'
                f'-{compile_maplibre.CODE_VERSION}"')
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers={"ETag": etag})
        style = compile_maplibre.compile_style(conn, view)
    return JSONResponse(style, headers={"ETag": etag, "Cache-Control": "no-cache"})


@app.get("/v/{view_id}/origo.json", tags=["map"], summary="Origo/OpenLayers configuration",
         responses={**api.UNKNOWN_VIEW, 200: api.content("Renderer-agnostic view compiled to Origo configuration.")})
def origo_json(view_id: api.ViewId):
    with dbq.get_pool().connection() as conn:
        view = _load_view(conn, view_id)
        config = compile_origo.compile_origo(conn, view)
    return JSONResponse(config, headers={"Cache-Control": "no-cache"})


@app.get("/v/{view_id}", tags=["map"], summary="Interactive map page", response_class=HTMLResponse,
         responses={**api.UNKNOWN_VIEW, **api.errors({"400": "renderer must be 'maplibre' or 'origo'."})})
def view_page(view_id: api.ViewId, request: Request, renderer: str = Query(default="maplibre", description="maplibre or origo; other values return 400")):
    if renderer not in ("maplibre", "origo"):
        raise HTTPException(status_code=400, detail="renderer must be 'maplibre' or 'origo'")
    key_id = _manager_key_id(request)
    with dbq.get_pool().connection() as conn:
        view = _load_view(conn, view_id)
        principal = dashboard_data.principal(conn, key_id) if key_id else None
    n = _nonce()
    render = page.origo_page if renderer == "origo" else page.maplibre_page
    body = render(view_id, n, title=view["title"], principal=principal,
                  csrf=viewer_auth.csrf_token(key_id) if key_id else "")
    response = _html(body, n, allow_eval=renderer == "maplibre")
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.get("/data/{layer}.geojson", tags=["data"], summary="Layer as GeoJSON", response_class=Response,
         responses={**api.DATA_ERRORS, 200: api.content("GeoJSON FeatureCollection.", "application/geo+json",
                    api.GEOJSON, {"type": "FeatureCollection", "features": []})})
def data_geojson(layer: api.LayerRef,
                 view: str | None = Query(default=None, description=api.VIEW_QUERY, examples=[api.VIEW_EXAMPLE]),
                 crs: int = Query(default=4326, description="4326 (WGS84) or 3014 (SWEREF 99 17 15); other values return 400"),
                 limit: int = Query(default=DATA_DEFAULT_LIMIT, description="Max features per page, clamped to 1..50000"),
                 offset: int | None = Query(default=None, ge=0, description="Offset for ordered GeoJSON pages; stop when fewer than limit features are returned."),
                 properties: str | None = Query(default=None, max_length=20000, description="JSON array of property names to return; absent returns all, unknown names are ignored.")):
    """Capability-scoped GeoJSON; large geometries are simplified and a 15 s query timeout applies."""
    if crs not in (4326, 3014):
        raise HTTPException(status_code=400, detail="crs must be 4326 or 3014")
    limit = max(1, min(limit, DATA_MAX_LIMIT))
    selected = None
    if properties is not None:
        try:
            selected = json.loads(properties)
        except ValueError:
            raise HTTPException(status_code=400, detail="properties must be a JSON array of strings") from None
        if not isinstance(selected, list) or any(not isinstance(p, str) for p in selected):
            raise HTTPException(status_code=400, detail="properties must be a JSON array of strings")
    with dbq.get_pool().connection() as conn:
        schema, table, props = _checked_layer(conn, layer, view)
        order_by = "fid" if "fid" in props else "id" if "id" in props else None
        if selected is not None:
            requested = set(selected)
            props = [p for p in props if p in requested]
        count = dbq.feature_count(conn, schema, table)
        simplify = count is not None and count > SIMPLIFY_THRESHOLD
        paging = {"offset": offset, "order_by": order_by} if offset is not None else {}
        body = dbq.geojson_feature_collection(conn, schema, table, props,
                                              crs, limit, simplify, **paging)
    return Response(content=body, media_type="application/geo+json",
                    headers={"Cache-Control": "no-store"})


@app.get("/tiles/{layer}/{z}/{x}/{y}.mvt", tags=["data"], summary="Mapbox Vector Tile", response_class=Response,
         responses={**api.DATA_ERRORS, 200: api.content("MVT tile.", "application/vnd.mapbox-vector-tile", api.BINARY),
                    204: {"description": "Empty tile; no features in range."}})
def tiles_mvt(layer: api.LayerRef, z: int, x: int, y: int,
              view: str | None = Query(default=None, description=api.VIEW_QUERY, examples=[api.VIEW_EXAMPLE])):
    """Capability-scoped tile. Zoom must be 0..22 and x/y must be in 0..2^z-1; otherwise 400."""
    if z < 0 or z > 22 or x < 0 or y < 0 or x >= 2 ** z or y >= 2 ** z:
        raise HTTPException(status_code=400, detail="tile coordinates out of range")
    with dbq.get_pool().connection() as conn:
        schema, table, props = _checked_layer(conn, layer, view)
        tile = dbq.mvt_tile(conn, schema, table, props, z, x, y)
    if not tile:
        return Response(status_code=204)
    return Response(content=tile, media_type="application/vnd.mapbox-vector-tile",
                    headers={"Cache-Control": "no-store"})


@app.get("/wmsref/{dataset_id}", tags=["proxy"], summary="Authenticated WMS GetMap proxy", response_class=Response,
         responses={**api.errors({"400": "Missing view.", "403": "Layer excluded from view or request is not GetMap.",
                                    "404": "Unknown dataset."}),
                    502: {"description": "Upstream request failed (JSON) or returned non-200 (plain text).",
                          "content": {"application/json": {"schema": {"type": "object"}},
                                      "text/plain": {"schema": {"type": "string"}}}},
                    200: {"description": "Upstream image; content type is passed through.",
                          "content": {kind: {"schema": api.BINARY} for kind in ("image/png", "image/jpeg")}}},
         openapi_extra={"parameters": [
             {"name": "REQUEST", "in": "query", "required": True,
              "description": "GetMap only; parameter names and value are case-insensitive.",
              "schema": {"type": "string", "examples": ["GetMap"]}},
             *[{"name": name, "in": "query", "required": False, "schema": {"type": "string"},
                "description": "Forwarded to the upstream WMS."}
               for name in ("LAYERS", "BBOX", "WIDTH", "HEIGHT", "CRS", "FORMAT")]]})
def wmsref(dataset_id: str, request: Request,
           view: str | None = Query(default=None, description=api.VIEW_QUERY, examples=[api.VIEW_EXAMPLE])):
    """GetMap proxy for authenticated WMS raster_refs. The browser cannot hold the
    Basic-auth credential, so the compilers point map sources here and the viewer
    injects it server-side. Capability-checked like /data and /tiles: the dataset's
    wms: ref must be part of the named view. WMS GetMap parameters (SERVICE, VERSION,
    LAYERS, STYLES, CRS/SRS, BBOX, WIDTH, HEIGHT, FORMAT, TRANSPARENT, etc.) are forwarded
    unchanged; only `view` is removed. Non-GetMap requests return 403."""
    if not view:
        raise HTTPException(status_code=400, detail="view query parameter is required")
    with dbq.get_pool().connection() as conn:
        v = _load_view(conn, view, status=403)
        refs = {e.get("ref") for e in (v["spec"] or {}).get("layers") or []
                if isinstance(e, dict)}
        if f"wms:{dataset_id}" not in refs:
            raise HTTPException(status_code=403, detail="layer is not part of this view")
        ds = dbq.wms_dataset(conn, dataset_id)
    if ds is None or not ds["url"]:
        raise HTTPException(status_code=404, detail="unknown dataset")

    kvp = [(k, val) for k, val in request.query_params.multi_items()
           if k.lower() != "view"]
    req_vals = [val for k, val in kvp if k.upper() == "REQUEST"]
    if not req_vals or any(val.lower() != "getmap" for val in req_vals):
        raise HTTPException(status_code=403, detail="only GetMap is proxied")

    # Upstream = the catalog url with the client KVP merged in; the source url's
    # own params survive unless the client sends the same key (case-insensitive).
    parts = urlsplit(ds["url"])
    taken = {k.upper() for k, _ in kvp}
    merged = [(k, val) for k, val in parse_qsl(parts.query, keep_blank_values=True)
              if k.upper() not in taken] + kvp
    upstream = urlunsplit((parts.scheme, parts.netloc, parts.path,
                           urlencode(merged, quote_via=quote), ""))
    try:
        resp = httpx.get(upstream, auth=netauth.basic_auth_for(upstream), timeout=30.0)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="upstream WMS request failed")
    if resp.status_code != 200:
        return Response(content=f"upstream WMS returned {resp.status_code}",
                        status_code=502, media_type="text/plain")
    return Response(content=resp.content,
                    media_type=resp.headers.get("content-type", "application/octet-stream"),
                    headers={"Cache-Control": "private, max-age=3600"})
