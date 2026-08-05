"""Geodata MCP server — agent-facing control plane (FastMCP, streamable HTTP at /mcp).

Every /mcp request must carry `Authorization: Bearer <api key>` (401 otherwise, enforced
by BearerAuthMiddleware below). Identity is the API key; all state lives in the key's
durable, named workspaces (see sessions.py) — reconnects land in the same workspace.
"""

import sys
import threading
import time

import anyio.to_thread
import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import config
import export_ops
import layer_ops
import load_ops
import map_ops
import query_ops
import search_ops
import sessions
import workspace_ops

# Stateful streamable HTTP: the server issues mcp-session-id on initialize and clients
# echo it back — that keeps the *transport* session alive. It no longer carries any
# identity: workspaces are keyed on the API key, so a fresh transport session (every
# reconnect) still resolves to the same durable workspace.
mcp = FastMCP(
    "geodata",
    host="0.0.0.0",
    port=8000,
    streamable_http_path="/mcp",
)


class BearerAuthMiddleware:
    """Pure-ASGI bearer check for /mcp (Starlette's BaseHTTPMiddleware buffers
    responses, which does not mix well with SSE streams — so raw ASGI it is).

    Valid key digests are cached for CACHE_TTL_S so the hot path costs one dict hit;
    disabling a key therefore takes effect within the TTL, not instantly.
    """

    CACHE_TTL_S = 30.0

    def __init__(self, app):
        self.app = app
        self._cache: dict[str, float] = {}
        self._lock = threading.Lock()

    def _check(self, raw_key: str) -> bool:
        kh = sessions.hash_key(raw_key)
        now = time.monotonic()
        with self._lock:
            exp = self._cache.get(kh)
            if exp is not None and exp > now:
                return True
        ok = sessions.key_id_for_hash(kh) is not None
        if ok:
            with self._lock:
                self._cache[kh] = now + self.CACHE_TTL_S
        return ok

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/mcp"):
            return await self.app(scope, receive, send)
        auth = None
        for name, value in scope.get("headers") or []:
            if name == b"authorization":
                auth = value.decode("latin-1")
                break
        raw = sessions.bearer_token({"authorization": auth}) if auth else None
        ok = False
        if raw:
            ok = await anyio.to_thread.run_sync(self._check, raw)
        if not ok:
            response = JSONResponse(
                {"error": "unauthorized — send 'Authorization: Bearer <api key>'"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)


def _ws(ctx: Context | None) -> sessions.Workspace:
    """The caller's active workspace (raises sessions.AuthError)."""
    return sessions.resolve(ctx if ctx is not None else mcp.get_context())


def _auth_error(e: Exception) -> dict:
    return {"error": f"auth: {str(e).strip()}"}


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


@mcp.tool()
def search(query: str | None = None, id: str | None = None, kind: str | None = None,
           limit: int = 15, ctx: Context = None) -> dict:
    """Search the municipal geodata catalog (Sundsvall) — datasets, sources and documents.

    Hybrid search: fuzzy trigram matching plus semantic vector similarity over dataset
    titles/descriptions AND over PDF/document text chunks. Swedish works well
    ("strandskydd", "detaljplan", "buller").

    Args:
      query: free-text search. Returns {"datasets": [...], "chunks": [...], "embedding_used": bool}.
        Each dataset has {id, title, kind, description, ref_table, source_slug, score}.
        ref_table (e.g. 'ref.strandskydd') means the data is already ingested and queryable
        via the query tool; ref_table null means call load(op='ingest', dataset_id=...) first.
        chunks are matching document passages with document title + source_url.
      id: a dataset uuid — returns the full catalog row (schema summary, extent, feature
        count), its source (license, attribution, trust) and provenance history instead.
      kind: optional filter: 'vector' | 'raster_ref' | 'document' | 'table'.
      limit: max datasets returned (default 15).

    The whole catalog is also plain SQL: catalog.datasets and catalog.sources are readable
    through the query tool. Data model: schemas catalog/ref/doc/app plus your private
    workspace schema; geometry column 'geom', SRID 3014 (SWEREF 99 17 15, metres).
    """
    try:
        _ws(ctx)   # resolve the principal first: same auth path as every other tool
        if id:
            return search_ops.dataset_detail(str(id))
        if not query:
            return {"error": "provide a query string (or id for one dataset's details)"}
        lim = max(1, min(int(limit or 15), 50))
        return search_ops.hybrid_search(str(query), kind, lim)
    except sessions.AuthError as e:
        return _auth_error(e)
    except Exception as e:
        return {"error": f"search failed: {str(e).strip()}"}


@mcp.tool()
def load(op: str, kind: str | None = None, url: str | None = None, title: str | None = None,
         slug: str | None = None, license: str = "", notes: str = "",
         dataset_id: str | None = None, table_name: str | None = None, target: str = "ref",
         rows: list | None = None, source: str | None = None, crs: str | None = None,
         area: str | None = None, concepts: list | None = None,
         collection_a: str | None = None, collection_b: str | None = None,
         threshold: float | None = None, min_area_m2: float | None = None,
         method: str | None = None, job_id: int | None = None, ctx: Context = None) -> dict:
    """Register data sources and bring datasets into the database. Ops:

    - op='register' {kind, url, title, slug?, license?, notes?}: add a source to the catalog.
      kind: wfs|wms|wmts|ogcapi|file|pdf|text|stac|inline. WFS/WMS/WMTS/OGC-API/STAC sources
      are harvested automatically (a background job reads the capabilities/collections and
      fills catalog.datasets — poll it via the returned job_id). Returns {source_id, slug,
      job_id}. WMTS/STAC layers become raster_ref datasets: reference them on maps via
      'wms:<dataset id>' (WMTS renders in the MapLibre view), they are not ingestable.
    - op='ingest' {dataset_id, table_name?, target='ref'|'workspace'}: load a catalog dataset
      into PostGIS (vector via WFS/OGC-API/file → a table with geom SRID 3014; pdf/text
      documents → extracted text chunks in doc.*). target='workspace' puts the table in your
      private schema. Waits up to 8 s, then returns the job status either way; poll with
      op='status'.
    - op='inline' {rows, table_name, source, crs?}: synchronously insert rows you provide.
      rows = list of flat dicts; an optional 'wkt' key becomes the geometry (assumed
      EPSG:3014 unless crs='4326'), or 'lon'/'lat' keys (always WGS84). source is
      MANDATORY — say where the data comes from; it is recorded in provenance.
      Column types are inferred (text / double precision / bigint / boolean).
    - op='change_detect' {area, concepts, collection_a, collection_b, table_name,
      threshold?, min_area_m2?, method?}: compare two orthophoto vintages with SAM3
      concept segmentation and write where concepts appeared/disappeared/changed to your
      workspace. Results are change CANDIDATES for review, not conclusions — inspect them
      against the imagery before reporting anything. concepts: 1-6 free-text noun phrases,
      ENGLISH ONLY — the model's text grounding fails silently on Swedish (verified:
      'byggnad' finds nothing where 'building' scores 0.8+). Translate user terms first,
      e.g. byggnad→'building', småhus→'house', upplag→'storage yard', pool→'swimming pool',
      parkeringsplats→'parking lot'.
      collection_a/collection_b: STAC orthophoto collection ids — list them via the query
      tool: SELECT d.external_id FROM catalog.datasets d JOIN catalog.sources s
      ON s.id = d.source_id AND s.kind = 'stac' (e.g. external_id LIKE 'orto-t2-%').
      area: a layer ref ('ref.<t>' or '<your ws schema>.<t>' — its bounding box is used),
      'xmin,ymin,xmax,ymax' in EPSG:3014, or EPSG:3014 WKT; max 2 km² per run. Writes
      <ws>.<table_name> (concept, change_class 'appeared'|'disappeared'|'changed',
      confidence_a, confidence_b, iou, area_m2, vintage/datetime columns, geom) and
      <ws>.<table_name>_coverage (tile_id, status 'analyzed'|'missing_a'|'missing_b'|
      'error', gsd_m, geom). No candidate rows over an 'analyzed' tile means no change
      found there; any other coverage status means that tile was NOT analyzed — check
      coverage before reading absence as evidence. Runs minutes; poll with op='status'.
      Follow up: query the result table, map it over the ortofoto WMS
      ('wms:<dataset id>' basemap), refine concepts and re-run on subareas.
    - op='status' {job_id}: one job's row (status queued|running|done|error, result, error).
    - op='jobs' {}: the last 20 jobs.
    - op='embed' {}: (re)embed catalog + document chunks for semantic search (idempotent).

    Registering a pdf/file/text source also creates its dataset immediately and returns
    dataset_id — pass that straight to op='ingest' (text = a web page or plain-text URL,
    ingested into the searchable document corpus).

    After ingesting, explore with the query tool and visualize via layer + map.
    """
    try:
        w = _ws(ctx)
        if op == "register":
            return load_ops.register(w.id, kind or "", url, title or "", slug, license, notes)
        if op == "ingest":
            if not dataset_id:
                return {"error": "ingest needs dataset_id (find it with search)"}
            return load_ops.ingest(w.id, str(dataset_id), table_name, target)
        if op == "inline":
            return load_ops.inline(w.id, rows or [], table_name or "", source, crs)
        if op == "change_detect":
            return load_ops.change_detect(w.id, area, concepts, collection_a, collection_b,
                                          table_name, threshold, min_area_m2, method)
        if op == "status":
            if job_id is None:
                return {"error": "status needs job_id"}
            return load_ops.status(job_id)
        if op == "jobs":
            return load_ops.jobs()
        if op == "embed":
            return load_ops.embed(w.id)
        return {"error": "op must be one of register|ingest|inline|change_detect|status|jobs|embed"}
    except sessions.AuthError as e:
        return _auth_error(e)
    except Exception as e:
        return {"error": f"load failed: {str(e).strip()}"}


@mcp.tool()
def query(sql: str, limit: int = 500, ctx: Context = None) -> dict:
    """Run read-only SQL (PostGIS 3.5 + pgvector) — the analysis workhorse.

    One statement, starting with SELECT/WITH/EXPLAIN/SHOW/TABLE/VALUES; no semicolons.
    Runs as a read-only role with a 15 s timeout; results capped at min(limit, 1000) rows.
    Geometry values come back as WKT truncated to 400 chars — select specific measures
    (ST_Area, ST_Length, ST_X/ST_Y) rather than raw geometry when you can.

    Data model: shared reference layers in ref.* (geometry column 'geom', SRID 3014 —
    SWEREF 99 17 15, units metres), your private tables in your workspace schema (see
    workspace(op='current')), the catalog in catalog.datasets / catalog.sources, document
    text in doc.documents / doc.chunks.

    Examples:
      -- what is near a location? (metres, thanks to SRID 3014)
      SELECT s.omrade, ST_Area(s.geom) AS m2
        FROM ref.strandskydd s
        JOIN ref.forskolor f ON ST_DWithin(s.geom, f.geom, 250)

      -- geocode an address, then measure from it
      SELECT g.address, ST_X(g.geom), ST_Y(g.geom), g.score
        FROM app.geocode('Storgatan 1', 3) g
      -- (app.reverse_geocode(x, y) gives the nearest address within 500 m)

      -- inspect a table before using it
      SELECT column_name, data_type FROM information_schema.columns
       WHERE table_schema = 'ref' AND table_name = 'strandskydd'

    Every call is logged with the referenced tables and returns a query_id — cite it when
    quoting numbers. Returns {query_id, columns, rows, row_count, truncated,
    referenced_tables}. Writes are impossible here: derive new tables with the layer tool.
    """
    try:
        w = _ws(ctx)
        return query_ops.run_query(w.id, sql, limit)
    except sessions.AuthError as e:
        return _auth_error(e)
    except Exception as e:
        return {"error": f"query failed: {str(e).strip()}"}


@mcp.tool()
def workspace(op: str = "current", name: str | None = None, new_name: str | None = None,
              ctx: Context = None) -> dict:
    """Manage your durable workspaces (named containers for layers and maps).

    Workspaces belong to your API key and survive reconnects and restarts: coming back
    tomorrow with the same key puts you in the same active workspace, tables intact.
    Ops:

    - op='current' {}: the active workspace and its layers (default when op omitted).
    - op='list' {}: all your workspaces with layer/map counts and last-used times.
    - op='new' {name}: create a workspace and switch to it (name: lowercase, digits,
      '-'/'_', max 40 chars; e.g. 'flood-analysis'). If that name already exists you are
      switched to it instead, with its layers intact — the reply's 'created' flag says which
      happened. Max 20 workspaces per key.
    - op='delete' also deletes that workspace's map views (their layers are going away).
    - op='use' {name}: switch the active workspace — later layer/map/query calls run there.
    - op='rename' {name, new_name}: relabel a workspace (tables untouched).
    - op='delete' {name}: drop the workspace schema AND all its tables (irreversible;
      recorded in provenance).

    Use separate workspaces for separate analyses so layer names never collide and
    old work stays browsable — the human-facing manager UI lives at /workspaces.
    """
    try:
        w = _ws(ctx)
        if op in ("current", ""):
            return workspace_ops.current(w)
        if op == "list":
            return workspace_ops.list_workspaces(w.api_key_id)
        if op == "new":
            if not name:
                return {"error": "new needs a name"}
            return workspace_ops.create(w.api_key_id, name)
        if op == "use":
            if not name:
                return {"error": "use needs a name — see op='list'"}
            return workspace_ops.use(w.api_key_id, name)
        if op == "rename":
            if not name or not new_name:
                return {"error": "rename needs name and new_name"}
            return workspace_ops.rename(w.api_key_id, name, new_name)
        if op == "delete":
            if not name:
                return {"error": "delete needs a name"}
            return workspace_ops.delete(w.api_key_id, name)
        return {"error": "op must be one of current|list|new|use|rename|delete"}
    except sessions.AuthError as e:
        return _auth_error(e)
    except Exception as e:
        return {"error": f"workspace failed: {str(e).strip()}"}


@mcp.tool()
def layer(op: str, name: str | None = None, sql: str | None = None, notes: str = "",
          style: dict | None = None, key_column: str | None = None, values: dict | None = None,
          popup: list | None = None, label: str | None = None, visible: bool | None = None,
          new_name: str | None = None, ctx: Context = None) -> dict:
    """Create and manage tables in your active workspace — the ONLY write path.

    Every op is recorded in the append-only provenance ledger. Ops:

    - op='create' {name, sql, notes?, style?}: materialize a SELECT as a workspace table
      (CREATE TABLE AS). name: ^[a-z][a-z0-9_]{0,59}$. Geometry columns get a spatial
      index automatically. Returns {table, row_count, columns}. This is how analysis
      results become mappable/exportable: layer create → map upsert → share the URL.
    - op='update' {name, key_column, values}: per-row attribute writes,
      values = {key: {column: value, ...}, ...} matched on key_column (cast to text).
      Columns that don't exist yet are added with inferred types.
    - op='style' {name, style?, popup?, label?, visible?, notes?}: presentation hints only.
      style keys: fill, stroke, opacity, circle (px), width (hex colors, e.g. '#e31a1c').
      popup = attribute names shown on click.
    - op='rename' {name, new_name} / op='drop' {name}.
    - op='list' {}: your workspace tables (exact row counts) + shared ref.* layers
      (estimates), with their style metadata.

    Layer-then-map is how you show results to the user: create a layer from a SELECT,
    then map(op='upsert', layers=[...]) and give the user the returned URL. Layers live
    in the active workspace (workspace tool to switch).
    """
    try:
        w = _ws(ctx)
        if op == "create":
            if not name or not sql:
                return {"error": "create needs name and sql"}
            return layer_ops.create(w.id, name, sql, notes, style)
        if op == "update":
            if not name or not key_column or not values:
                return {"error": "update needs name, key_column and values"}
            return layer_ops.update(w.id, name, key_column, values)
        if op == "style":
            if not name:
                return {"error": "style needs name"}
            return layer_ops.style(w.id, name, style, popup, label, visible,
                                   notes if notes else None)
        if op == "rename":
            if not name or not new_name:
                return {"error": "rename needs name and new_name"}
            return layer_ops.rename(w.id, name, new_name)
        if op == "drop":
            if not name:
                return {"error": "drop needs name"}
            return layer_ops.drop(w.id, name)
        if op == "list":
            return layer_ops.list_layers(w.id)
        return {"error": "op must be one of create|update|style|rename|drop|list"}
    except sessions.AuthError as e:
        return _auth_error(e)
    except Exception as e:
        return {"error": f"layer failed: {str(e).strip()}"}


@mcp.tool()
def map(op: str = "upsert", view_id: str | None = None, title: str | None = None,
        layers: list | None = None, basemap: str = "positron",
        extent_3014: list | None = None, legend: bool = True, ctx: Context = None) -> dict:
    """Create or update an interactive web map and get a shareable URL.

    - op='upsert' {view_id?, title?, layers, basemap?, extent_3014?, legend?}: layers is a
      list of {ref, style?, popup?, label?, visible?} (or bare ref strings). ref is
      'ref.<table>', '<your ws schema>.<table>', or 'wms:<dataset_id>' for a catalog WMS
      layer. style keys: fill, stroke, opacity, circle, width. Omit extent_3014 and it is
      computed from the layers' data (EPSG:3014 [xmin,ymin,xmax,ymax]). Returns
      {view_id, url, version} — give the url to the user; open pages refresh themselves
      within seconds when you upsert the same view_id again.
    - op='get' {view_id}: the stored spec + version.
    - op='list' {}: the active workspace's views.

    basemap: 'positron' (default), 'osm', 'none', or 'wms:<dataset id>' for a catalog
    raster layer. The page renders with MapLibre by default; '?renderer=origo' on the same
    URL serves the Origo (OpenLayers) renderer. NOTE: Origo renders in EPSG:3014 and cannot
    align Web-Mercator tiles, so positron/osm show no backdrop there — pick a
    'wms:<dataset id>' basemap (search(kind='raster_ref')) if the map should have one in
    both renderers. Typical flow: layer(op='create', ...) with a styled
    result, then map(op='upsert', layers=[{'ref': 'ws_x.result',
    'style': {'fill': '#e31a1c', 'opacity': 0.5}, 'popup': ['name', 'area_m2']}]).
    """
    try:
        w = _ws(ctx)
        if op == "upsert":
            return map_ops.upsert(w.id, view_id, title, layers, basemap, extent_3014, legend)
        if op == "get":
            if not view_id:
                return {"error": "get needs view_id"}
            return map_ops.get(view_id)
        if op == "list":
            return map_ops.list_views(w.id)
        return {"error": "op must be one of upsert|get|list"}
    except sessions.AuthError as e:
        return _auth_error(e)
    except Exception as e:
        return {"error": f"map failed: {str(e).strip()}"}


@mcp.tool()
def export(layers: list, format: str = "gpkg", cite: bool = True, ctx: Context = None) -> dict:
    """Export layers to a standard GIS file and get a download URL (valid 24 h).

    layers: list of 'schema.table' refs — shared 'ref.<table>' layers and/or your own
    workspace tables. format: 'gpkg' (GeoPackage, default — opens directly in QGIS/ArcGIS),
    'geojson' (WGS84), 'csv' (geometry as WKT) or 'parquet'. cite=True (default) bundles a
    citation/provenance markdown sidecar built from the provenance ledger and source
    licenses — pass its URL along whenever the data leaves the system.

    Waits up to 30 s for the export job; if still running you get {job_id, status} — poll
    with load(op='status', job_id=...) and call export again when done.
    Returns {url, sidecar_url, format, expires_hours}.
    """
    try:
        w = _ws(ctx)
        return export_ops.run_export(w.id, layers, format, cite)
    except sessions.AuthError as e:
        return _auth_error(e)
    except Exception as e:
        return {"error": f"export failed: {str(e).strip()}"}


def main() -> None:
    if not config.GEODATA_API_KEYS:
        print("FATAL: GEODATA_API_KEYS is empty — set at least one key in .env "
              "(auth is not optional).", file=sys.stderr)
        raise SystemExit(1)
    sessions.bootstrap_env_keys(config.GEODATA_API_KEYS)
    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
