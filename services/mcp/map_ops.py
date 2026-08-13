"""Implementation of the map tool: renderer-agnostic map views in app.map_views."""

import re
import secrets

from psycopg import sql as pgsql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

import config
import db
import geometry
import sessions
import sqlguard

VIEW_ID_RE = re.compile(r"^v_[a-f0-9]{24}$")
WMS_REF_RE = re.compile(
    r"^wms:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)
BASEMAPS = ("positron", "osm", "none")
STYLE_KEYS = ("fill", "stroke", "opacity", "circle", "width")
# The architecture's own map-spec example uses `color` for point fill and `radius`
# for size; accept them rather than dropping them silently.
STYLE_ALIASES = {"color": "fill", "radius": "circle", "line-width": "width",
                 "fill-opacity": "opacity"}


def _normalize_layers(layers, ws: str) -> tuple[list[dict] | None, str | None, list[str]]:
    """Returns (clean_layers, error, warnings). Warnings surface silently-ignored input."""
    warnings: list[str] = []
    if not isinstance(layers, list) or not layers:
        return None, "layers must be a non-empty list of {'ref': 'schema.table', ...} entries", warnings
    out = []
    with db.app_pool().connection() as conn:
        for entry in layers:
            if isinstance(entry, str):
                entry = {"ref": entry}
            if not isinstance(entry, dict) or "ref" not in entry:
                return None, "each layer entry needs a 'ref' (e.g. 'ref.strandskydd' or 'ws_x.mylayer')", warnings
            ref = str(entry["ref"])
            wms = WMS_REF_RE.match(ref)
            if wms:
                row = conn.execute(
                    "SELECT 1 FROM catalog.datasets WHERE id = %s AND kind = 'raster_ref'",
                    (wms.group(1),),
                ).fetchone()
                if row is None:
                    return None, f"{ref!r} does not match a catalog raster_ref dataset", warnings
            else:
                m = sqlguard.LAYER_REF_RE.match(ref)
                if not m:
                    return None, (f"invalid layer ref {ref!r} — use 'ref.<table>', "
                                  f"'<your ws schema>.<table>' or 'wms:<dataset uuid>'"), warnings
                schema = m.group(1)
                if schema.startswith("ws_") and schema != ws:
                    return None, (f"{ref!r} is another workspace — your active workspace is {ws}. "
                              f"Switch with workspace(op='use') or use {ws}.<table>"), warnings
                exists = conn.execute("SELECT to_regclass(%s)", (ref,)).fetchone()
                if exists is None or exists[0] is None:
                    return None, f"table {ref!r} does not exist — check layer(op='list')", warnings
            clean = {"ref": ref}
            if isinstance(entry.get("style"), dict):
                style = dict(entry["style"])
                for alias, canonical in STYLE_ALIASES.items():
                    if alias in style and canonical not in style:
                        style[canonical] = style.pop(alias)
                clean["style"] = {k: style[k] for k in STYLE_KEYS if k in style}
                dropped = sorted(set(style) - set(STYLE_KEYS))
                if dropped:
                    warnings.append(f"{ref}: ignored unsupported style keys {dropped} "
                                    f"(supported: {', '.join(STYLE_KEYS)})")
            if isinstance(entry.get("popup"), list):
                clean["popup"] = [str(p) for p in entry["popup"]]
            if entry.get("label") is not None:
                clean["label"] = str(entry["label"])
            clean["visible"] = bool(entry.get("visible", True))
            out.append(clean)
    return out, None, warnings


_YEAR_RE = re.compile(r"(\d{4})")


def _is_change_layer(conn, schema: str, table: str) -> bool:
    """A change_detect output is any table carrying the diff signature columns.
    Detected by shape (not provenance) so a renamed/derived copy still qualifies."""
    row = conn.execute(
        """SELECT count(*) FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
              AND column_name IN ('vintage_a', 'vintage_b', 'change_class')""",
        (schema, table),
    ).fetchone()
    return row is not None and int(row[0]) == 3


def _vintage_dataset(conn, external_id: str):
    """(dataset_uuid, title) for a WMS/raster vintage external_id, or None when it is
    not a mappable raster layer (e.g. a STAC vintage, or one never harvested)."""
    if not external_id:
        return None
    return conn.execute(
        "SELECT id::text, title FROM catalog.datasets "
        " WHERE external_id = %s AND kind = 'raster_ref' LIMIT 1",
        (external_id,),
    ).fetchone()


def _augment_change_layers(clean_layers: list[dict]) -> tuple[list[dict], dict | None, list[str]]:
    """Inject the before/after orthophoto vintages behind any change-detection layer,
    so the imagery a diff was computed from is inspectable in the viewer without the
    user hunting dataset UUIDs. Returns (layers, compare, warnings). The imagery is
    added hidden (the viewer's inspector reveals it); `compare` drives that inspector."""
    warnings: list[str] = []
    compare: dict | None = None
    have = {l["ref"] for l in clean_layers}
    out: list[dict] = []
    with db.app_pool().connection() as conn:
        for entry in clean_layers:
            ref = entry["ref"]
            if ref.startswith("wms:") or "." not in ref:
                out.append(entry)
                continue
            schema, table = ref.split(".", 1)
            if not _is_change_layer(conn, schema, table):
                out.append(entry)
                continue
            row = conn.execute(
                pgsql.SQL("SELECT vintage_a, vintage_b FROM {} LIMIT 1").format(
                    sqlguard.qualified(schema, table))
            ).fetchone()
            va, vb = (row or (None, None))
            sides: dict[str, dict] = {}
            for side, ext in (("before", va), ("after", vb)):
                ds = _vintage_dataset(conn, ext)
                if ds is None:
                    if ext:
                        warnings.append(
                            f"{ref}: {side} vintage {ext!r} is not a mappable raster "
                            "layer — its imagery cannot be shown")
                    continue
                wref = f"wms:{ds[0]}"
                year = _YEAR_RE.search(ext)
                yr = year.group(1) if year else None
                label = f"Ortofoto {yr}" if yr else (ds[1] or ext)
                sides[side] = {"ref": wref, "label": label, "year": yr}
                if wref not in have:
                    # Hidden by default; placed before the change layer so the diff
                    # polygons paint on top of the imagery in both renderers.
                    out.append({"ref": wref, "label": label, "visible": False,
                                "role": f"orthophoto-{side}"})
                    have.add(wref)
            out.append({**entry, "role": "change"})
            if compare is None and sides:
                compare = {"changes_ref": ref, **sides}
    return out, compare, warnings


def _auto_extent(layer_refs: list[str]) -> list[float] | None:
    """Union of ST_Extent over the vector layers, expanded 5%."""
    boxes = []
    with db.app_pool().connection() as conn:
        for ref in layer_refs:
            schema, table = ref.split(".", 1)
            has_geom = conn.execute(
                """SELECT 1 FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s AND udt_name = 'geometry'
                    LIMIT 1""",
                (schema, table),
            ).fetchone()
            if not has_geom:
                continue
            row = conn.execute(
                pgsql.SQL(
                    "SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) "
                    "FROM (SELECT ST_Extent(geom) AS e FROM {}) s WHERE e IS NOT NULL"
                ).format(sqlguard.qualified(schema, table))
            ).fetchone()
            if row:
                boxes.append([float(v) for v in row])
    if not boxes:
        return None
    xmin = min(b[0] for b in boxes)
    ymin = min(b[1] for b in boxes)
    xmax = max(b[2] for b in boxes)
    ymax = max(b[3] for b in boxes)
    dx = max(xmax - xmin, 1.0) * 0.025
    dy = max(ymax - ymin, 1.0) * 0.025
    return [round(xmin - dx, 2), round(ymin - dy, 2), round(xmax + dx, 2), round(ymax + dy, 2)]


def upsert(workspace_id: str, view_id: str | None, title: str | None, layers,
           basemap: str, extent_3014, legend: bool) -> dict:
    if basemap not in BASEMAPS and not WMS_REF_RE.match(str(basemap)):
        return {"error": f"basemap must be one of {BASEMAPS} or 'wms:<dataset uuid>'"}
    ws = sessions.ws_schema_for(workspace_id)
    clean_layers, err, warnings = _normalize_layers(layers, ws)
    if err:
        return {"error": err}
    clean_layers, compare, aug_warnings = _augment_change_layers(clean_layers)
    warnings = warnings + aug_warnings

    if extent_3014 is not None:
        try:
            extent = [float(v) for v in extent_3014]
            if len(extent) != 4 or extent[0] >= extent[2] or extent[1] >= extent[3]:
                raise ValueError
        except (TypeError, ValueError):
            return {"error": "extent_3014 must be [xmin, ymin, xmax, ymax] in EPSG:3014 metres"}
    else:
        vector_refs = [l["ref"] for l in clean_layers if not l["ref"].startswith("wms:")]
        extent = _auto_extent(vector_refs)

    if view_id is not None and not VIEW_ID_RE.match(str(view_id)):
        return {"error": "view_id must look like 'v_' + 24 hex characters (or be omitted)"}
    vid = view_id or ("v_" + secrets.token_hex(12))

    spec = {
        "view_id": vid,
        "title": title or "",
        "extent_3014": extent,
        "basemap": basemap,
        "layers": clean_layers,
        "legend": bool(legend),
    }
    if compare:
        spec["compare"] = compare
    with db.app_pool().connection() as conn:
        # The DO UPDATE is owner-scoped: a view_id belonging to another workspace must not be
        # silently rewritten under the user watching it (view ids travel as shareable URLs).
        row = conn.execute(
            """INSERT INTO app.map_views (view_id, workspace_id, title, spec)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (view_id) DO UPDATE SET
                 spec = EXCLUDED.spec,
                 title = EXCLUDED.title,
                 version = app.map_views.version + 1,
                 updated_at = now()
               WHERE app.map_views.workspace_id IS NOT DISTINCT FROM EXCLUDED.workspace_id
               RETURNING version""",
            (vid, workspace_id, title or "", Jsonb(spec)),
        ).fetchone()
        if row is None:
            return {"error": f"map view {vid!r} belongs to another workspace — "
                             "omit view_id to create your own view"}
        version = int(row[0])
    out = {"view_id": vid, "url": f"{config.PUBLIC_BASE_URL}/v/{vid}", "version": version,
           "extent_3014": extent}
    if warnings:
        out["warnings"] = warnings
    return out


def get(view_id: str) -> dict:
    if not view_id or not VIEW_ID_RE.match(str(view_id)):
        return {"error": "view_id must look like 'v_' + 24 hex characters"}
    with db.app_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT view_id, title, spec, version, created_at, updated_at
                     FROM app.map_views WHERE view_id = %s""",
                (view_id,),
            )
            row = cur.fetchone()
    if row is None:
        return {"error": f"no map view {view_id!r}"}
    out = geometry.jsonable_row(row)
    out["url"] = f"{config.PUBLIC_BASE_URL}/v/{view_id}"
    return out


def list_views(workspace_id: str) -> dict:
    with db.app_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT view_id, title, version, created_at, updated_at
                     FROM app.map_views WHERE workspace_id = %s
                    ORDER BY updated_at DESC LIMIT 50""",
                (workspace_id,),
            )
            rows = cur.fetchall()
    views = []
    for r in rows:
        v = geometry.jsonable_row(r)
        v["url"] = f"{config.PUBLIC_BASE_URL}/v/{r['view_id']}"
        views.append(v)
    return {"views": views}
