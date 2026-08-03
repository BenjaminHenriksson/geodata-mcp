"""Geodata MCP server — agent-facing control plane (FastMCP, streamable HTTP at /mcp)."""

from mcp.server.fastmcp import Context, FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

import export_ops
import layer_ops
import load_ops
import map_ops
import query_ops
import search_ops
import sessions

# Stateful streamable HTTP: the server issues mcp-session-id on initialize and clients echo
# it on every call — that header is what maps a session to its ws_* workspace schema.
# (stateless_http=True would collapse every client into one shared workspace.)
mcp = FastMCP(
    "geodata",
    host="0.0.0.0",
    port=8000,
    streamable_http_path="/mcp",
)


def _sid(ctx: Context | None) -> str:
    return sessions.session_id_from_context(ctx if ctx is not None else mcp.get_context())


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
        if id:
            return search_ops.dataset_detail(str(id))
        if not query:
            return {"error": "provide a query string (or id for one dataset's details)"}
        sessions.touch_session(_sid(ctx))
        lim = max(1, min(int(limit or 15), 50))
        return search_ops.hybrid_search(str(query), kind, lim)
    except Exception as e:
        return {"error": f"search failed: {str(e).strip()}"}


@mcp.tool()
def load(op: str, kind: str | None = None, url: str | None = None, title: str | None = None,
         slug: str | None = None, license: str = "", notes: str = "",
         dataset_id: str | None = None, table_name: str | None = None, target: str = "ref",
         rows: list | None = None, source: str | None = None, crs: str | None = None,
         job_id: int | None = None, ctx: Context = None) -> dict:
    """Register data sources and bring datasets into the database. Ops:

    - op='register' {kind, url, title, slug?, license?, notes?}: add a source to the catalog.
      kind: wfs|wms|wmts|ogcapi|file|pdf|text|stac|inline. WFS/WMS sources are harvested
      automatically (a background job reads GetCapabilities and fills catalog.datasets —
      poll it via the returned job_id). Returns {source_id, slug, job_id}.
    - op='ingest' {dataset_id, table_name?, target='ref'|'workspace'}: load a catalog dataset
      into PostGIS (vector via WFS/file → a table with geom SRID 3014; documents → extracted
      text chunks in doc.*). target='workspace' puts the table in your private schema.
      Waits up to 8 s, then returns the job status either way; poll with op='status'.
    - op='inline' {rows, table_name, source, crs?}: synchronously insert rows you provide.
      rows = list of flat dicts; an optional 'wkt' key becomes the geometry (assumed
      EPSG:3014 unless crs='4326'), or 'lon'/'lat' keys (always WGS84). source is
      MANDATORY — say where the data comes from; it is recorded in provenance.
      Column types are inferred (text / double precision / bigint / boolean).
    - op='status' {job_id}: one job's row (status queued|running|done|error, result, error).
    - op='jobs' {}: the last 20 jobs.
    - op='embed' {}: (re)embed catalog + document chunks for semantic search (idempotent).

    Registering a pdf/file source also creates its dataset immediately and returns
    dataset_id — pass that straight to op='ingest'.

    After ingesting, explore with the query tool and visualize via layer + map.
    """
    try:
        sid = _sid(ctx)
        sessions.touch_session(sid)
        if op == "register":
            return load_ops.register(sid, kind or "", url, title or "", slug, license, notes)
        if op == "ingest":
            if not dataset_id:
                return {"error": "ingest needs dataset_id (find it with search)"}
            return load_ops.ingest(sid, str(dataset_id), table_name, target)
        if op == "inline":
            return load_ops.inline(sid, rows or [], table_name or "", source, crs)
        if op == "status":
            if job_id is None:
                return {"error": "status needs job_id"}
            return load_ops.status(job_id)
        if op == "jobs":
            return load_ops.jobs()
        if op == "embed":
            return load_ops.embed(sid)
        return {"error": "op must be one of register|ingest|inline|status|jobs|embed"}
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
    SWEREF 99 17 15, units metres), your private tables in your ws_* schema, the catalog in
    catalog.datasets / catalog.sources, document text in doc.documents / doc.chunks.

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
        sid = _sid(ctx)
        sessions.touch_session(sid)
        return query_ops.run_query(sid, sql, limit)
    except Exception as e:
        return {"error": f"query failed: {str(e).strip()}"}


@mcp.tool()
def layer(op: str, name: str | None = None, sql: str | None = None, notes: str = "",
          style: dict | None = None, key_column: str | None = None, values: dict | None = None,
          popup: list | None = None, label: str | None = None, visible: bool | None = None,
          new_name: str | None = None, ctx: Context = None) -> dict:
    """Create and manage tables in your private workspace — the ONLY write path.

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
    then map(op='upsert', layers=[...]) and give the user the returned URL.
    """
    try:
        sid = _sid(ctx)
        sessions.touch_session(sid)
        if op == "create":
            if not name or not sql:
                return {"error": "create needs name and sql"}
            return layer_ops.create(sid, name, sql, notes, style)
        if op == "update":
            if not name or not key_column or not values:
                return {"error": "update needs name, key_column and values"}
            return layer_ops.update(sid, name, key_column, values)
        if op == "style":
            if not name:
                return {"error": "style needs name"}
            return layer_ops.style(sid, name, style, popup, label, visible,
                                   notes if notes else None)
        if op == "rename":
            if not name or not new_name:
                return {"error": "rename needs name and new_name"}
            return layer_ops.rename(sid, name, new_name)
        if op == "drop":
            if not name:
                return {"error": "drop needs name"}
            return layer_ops.drop(sid, name)
        if op == "list":
            return layer_ops.list_layers(sid)
        return {"error": "op must be one of create|update|style|rename|drop|list"}
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
    - op='list' {}: your session's views.

    basemap: 'positron' (default), 'osm', or 'none'. Typical flow: layer(op='create', ...)
    with a styled result, then map(op='upsert', layers=[{'ref': 'ws_x.result',
    'style': {'fill': '#e31a1c', 'opacity': 0.5}, 'popup': ['name', 'area_m2']}]).
    """
    try:
        sid = _sid(ctx)
        sessions.touch_session(sid)
        if op == "upsert":
            return map_ops.upsert(sid, view_id, title, layers, basemap, extent_3014, legend)
        if op == "get":
            if not view_id:
                return {"error": "get needs view_id"}
            return map_ops.get(view_id)
        if op == "list":
            return map_ops.list_views(sid)
        return {"error": "op must be one of upsert|get|list"}
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
        return export_ops.run_export(_sid(ctx), layers, format, cite)
    except Exception as e:
        return {"error": f"export failed: {str(e).strip()}"}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
