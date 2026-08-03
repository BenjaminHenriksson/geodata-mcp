"""Database access for the viewer service: connection pool + layer helpers.

All identifiers interpolated into SQL are validated against strict regexes and
quoted with psycopg.sql.Identifier; every value goes through bind parameters.
"""
import hashlib
import os
import re
import threading
import uuid

from psycopg import sql
from psycopg_pool import ConnectionPool

IDENT_RE = re.compile(r"^[a-z0-9_]{1,63}$")
WS_SCHEMA_RE = re.compile(r"^ws_[a-f0-9]{8}$")
LAYER_REF_RE = re.compile(r"^(ref|ws_[a-f0-9]{8})\.([a-z0-9_]{1,63})$")
VIEW_ID_RE = re.compile(r"^v_[a-f0-9]{24}$")

_pool = None
_pool_lock = threading.Lock()


def get_pool():
    """Lazily create (and open) the shared connection pool."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                dsn = os.environ.get("DATABASE_URL_APP")
                if not dsn:
                    raise RuntimeError("DATABASE_URL_APP environment variable is not set")
                pool = ConnectionPool(conninfo=dsn, min_size=1, max_size=8,
                                      name="viewer", open=False,
                                      check=ConnectionPool.check_connection)
                pool.open(wait=False)
                _pool = pool
    return _pool


def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def split_layer_ref(ref):
    """'schema.table' -> (schema, table) if it is a valid ref/ws layer ref, else None."""
    if not isinstance(ref, str):
        return None
    m = LAYER_REF_RE.match(ref)
    if not m:
        return None
    schema, table = m.group(1), m.group(2)
    if schema != "ref" and not WS_SCHEMA_RE.match(schema):
        return None
    if not IDENT_RE.match(table) or (schema != "ref" and not IDENT_RE.match(schema)):
        return None
    return schema, table


def get_view(conn, view_id):
    row = conn.execute(
        "SELECT view_id, session_id, title, spec, version "
        "FROM app.map_views WHERE view_id = %s", (view_id,)).fetchone()
    if row is None:
        return None
    return {"view_id": row[0], "session_id": row[1], "title": row[2],
            "spec": row[3] or {}, "version": row[4]}


def columns(conn, schema, table):
    """[(column_name, udt_name), ...] in ordinal order, or None if the table is absent."""
    rows = conn.execute(
        "SELECT column_name, udt_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
        (schema, table)).fetchall()
    if not rows:
        return None
    return [(r[0], r[1]) for r in rows]


def non_geom_columns(cols):
    return [n for n, u in cols if u not in ("geometry", "geography")]


def has_geom(cols):
    return any(n == "geom" and u in ("geometry", "geography") for n, u in cols)


def layer_meta(conn, schema, table):
    row = conn.execute(
        "SELECT style, popup, label, visible FROM app.layer_meta "
        "WHERE schema_name = %s AND table_name = %s", (schema, table)).fetchone()
    if row is None:
        return {"style": {}, "popup": [], "label": "", "visible": True}
    return {"style": row[0] if isinstance(row[0], dict) else {},
            "popup": list(row[1] or []),
            "label": row[2] or "",
            "visible": bool(row[3])}


def layer_meta_fingerprint(conn, spec):
    """Short digest of the app.layer_meta rows this view's style depends on.

    Part of the ETag so that styling changes made through `layer(op='style')` invalidate
    an open page's cached style, not just changes to the view spec itself.
    """
    refs = []
    for entry in (spec or {}).get("layers") or []:
        if isinstance(entry, dict) and isinstance(entry.get("ref"), str):
            parsed = split_layer_ref(entry["ref"])
            if parsed:
                refs.append(parsed)
    if not refs:
        return "0"
    rows = conn.execute(
        "SELECT schema_name, table_name, style::text, popup::text, label, visible "
        "  FROM app.layer_meta "
        " WHERE (schema_name, table_name) IN (SELECT unnest(%s::text[]), unnest(%s::text[])) "
        " ORDER BY schema_name, table_name",
        ([s for s, _ in refs], [t for _, t in refs]),
    ).fetchall()
    return hashlib.md5(repr(rows).encode("utf-8")).hexdigest()[:8]


def geometry_class(conn, schema, table):
    """'polygon' | 'line' | 'point' for schema.table's geom column."""
    row = conn.execute(
        "SELECT type FROM public.geometry_columns "
        "WHERE f_table_schema = %s AND f_table_name = %s AND f_geometry_column = 'geom'",
        (schema, table)).fetchone()
    gtype = (row[0] or "").upper() if row else ""
    if gtype in ("", "GEOMETRY"):
        q = sql.SQL("SELECT GeometryType({g}) FROM {s}.{t} WHERE {g} IS NOT NULL LIMIT 1").format(
            g=sql.Identifier("geom"), s=sql.Identifier(schema), t=sql.Identifier(table))
        row = conn.execute(q).fetchone()
        gtype = (row[0] or "").upper() if row and row[0] else ""
    if "POLYGON" in gtype or "SURFACE" in gtype:
        return "polygon"
    if "LINE" in gtype or "CURVE" in gtype:
        return "line"
    if "POINT" in gtype:
        return "point"
    return "polygon"


def feature_count(conn, schema, table):
    """Prefer catalog.datasets.feature_count; else pg_class.reltuples estimate; None = unknown."""
    ref = f"{schema}.{table}"
    row = conn.execute(
        "SELECT feature_count FROM catalog.datasets "
        "WHERE ref_table = %s AND feature_count IS NOT NULL "
        "ORDER BY updated_at DESC LIMIT 1", (ref,)).fetchone()
    if row is not None:
        return int(row[0])
    row = conn.execute(
        "SELECT c.reltuples FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = %s AND c.relname = %s "
        "AND c.relkind IN ('r', 'p', 'm', 'v', 'f')", (schema, table)).fetchone()
    if row is None:
        return None
    est = float(row[0])
    if est <= 0:  # -1 = never vacuumed/analyzed; 0 indistinguishable from unknown
        return None
    return int(est)


def wms_dataset(conn, dataset_id):
    """catalog raster_ref dataset joined with its source, or None."""
    try:
        u = uuid.UUID(str(dataset_id))
    except (ValueError, AttributeError, TypeError):
        return None
    row = conn.execute(
        "SELECT d.external_id, d.title, s.url, s.attribution "
        "FROM catalog.datasets d JOIN catalog.sources s ON s.id = d.source_id "
        "WHERE d.id = %s AND d.kind = 'raster_ref'", (str(u),)).fetchone()
    if row is None:
        return None
    return {"external_id": row[0], "title": row[1], "url": row[2], "attribution": row[3] or ""}


def layer_extent_3014(conn, schema, table):
    """[xmin, ymin, xmax, ymax] of the layer in EPSG:3014, or None when empty."""
    q = sql.SQL(
        "SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) "
        "FROM (SELECT ST_Extent({g}) AS e FROM {s}.{t}) q").format(
        g=sql.Identifier("geom"), s=sql.Identifier(schema), t=sql.Identifier(table))
    row = conn.execute(q).fetchone()
    if row is None or row[0] is None:
        return None
    return [float(row[0]), float(row[1]), float(row[2]), float(row[3])]


def _valid_extent(ext):
    return (isinstance(ext, (list, tuple)) and len(ext) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in ext))


def view_extent_3014(conn, spec):
    """Spec extent_3014 when present, else union ST_Extent over the vector layers."""
    ext = (spec or {}).get("extent_3014")
    if _valid_extent(ext):
        return [float(v) for v in ext]
    boxes = []
    for entry in (spec or {}).get("layers") or []:
        if not isinstance(entry, dict):
            continue
        parsed = split_layer_ref(entry.get("ref"))
        if parsed is None:
            continue
        schema, table = parsed
        cols = columns(conn, schema, table)
        if cols is None or not has_geom(cols):
            continue
        box = layer_extent_3014(conn, schema, table)
        if box is not None:
            boxes.append(box)
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def transform_extent_to_4326(conn, ext):
    row = conn.execute(
        "SELECT ST_XMin(g), ST_YMin(g), ST_XMax(g), ST_YMax(g) "
        "FROM (SELECT ST_Transform(ST_MakeEnvelope(%s, %s, %s, %s, 3014), 4326) AS g) q",
        (ext[0], ext[1], ext[2], ext[3])).fetchone()
    return [float(row[0]), float(row[1]), float(row[2]), float(row[3])]


def geojson_feature_collection(conn, schema, table, props, crs, limit, simplify):
    """FeatureCollection JSON text for schema.table; props = non-geometry column names."""
    conn.execute("SET LOCAL statement_timeout = '15s'")
    items = [sql.SQL("t.{}").format(sql.Identifier(c)) for c in props]
    geom_expr = sql.SQL("t.{}").format(sql.Identifier("geom"))
    if simplify:
        geom_expr = sql.SQL("ST_SimplifyPreserveTopology({}, 1.0)").format(geom_expr)
    if crs == 4326:
        geom_expr = sql.SQL("ST_Transform({}, 4326)").format(geom_expr)
    items.append(sql.SQL("{} AS geom").format(geom_expr))
    query = sql.SQL(
        "WITH src AS (SELECT {items} FROM {s}.{t} t WHERE t.{g} IS NOT NULL LIMIT {lim}) "
        "SELECT json_build_object('type', 'FeatureCollection', 'features', "
        "COALESCE(json_agg(ST_AsGeoJSON(src.*)::json), '[]'::json))::text FROM src").format(
        items=sql.SQL(", ").join(items),
        s=sql.Identifier(schema), t=sql.Identifier(table),
        g=sql.Identifier("geom"), lim=sql.Placeholder())
    row = conn.execute(query, (limit,)).fetchone()
    return row[0]


def mvt_tile(conn, schema, table, props, z, x, y):
    """MVT tile bytes for schema.table (layer name = table), or None/empty when no data."""
    conn.execute("SET LOCAL statement_timeout = '15s'")
    items = [sql.SQL(
        "ST_AsMVTGeom(ST_Transform(t.{g}, 3857), ST_TileEnvelope(%(z)s, %(x)s, %(y)s), "
        "4096, 64, true) AS geom").format(g=sql.Identifier("geom"))]
    items.extend(sql.SQL("t.{}").format(sql.Identifier(c)) for c in props)
    query = sql.SQL(
        "WITH mvtgeom AS (SELECT {items} FROM {s}.{t} t "
        "WHERE t.{g} && ST_Transform(ST_TileEnvelope(%(z)s, %(x)s, %(y)s), 3014)) "
        "SELECT ST_AsMVT(mvtgeom.*, %(name)s, 4096, 'geom') FROM mvtgeom "
        "WHERE geom IS NOT NULL").format(
        items=sql.SQL(", ").join(items),
        s=sql.Identifier(schema), t=sql.Identifier(table), g=sql.Identifier("geom"))
    row = conn.execute(query, {"z": z, "x": x, "y": y, "name": table}).fetchone()
    if row is None or row[0] is None:
        return None
    return bytes(row[0])
