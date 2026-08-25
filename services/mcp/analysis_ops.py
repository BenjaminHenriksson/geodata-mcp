"""Implementation of the analyze tool: a registry of long-running analysis
processors (list / describe / run / status / cancel).

The tool declaration stays constant-size no matter how many processors exist:
each processor is a REGISTRY entry carrying its title, one-line summary, full
prose guidance and a JSON Schema for its params. Agents discover with
op='list', pull the schema with op='describe' (paying its context cost only
when about to use it), start with op='run' and follow the job with
op='status'. Schema, guidance and validator for a processor live side by side
in this module so they cannot drift apart (the docstring-vs-code drift that
bit the old load(op='change_detect') English-only concepts note).

Processors write their results as LAYERS in the caller's workspace — the
boundary contract with the rest of the surface: read with query, style with
layer, show with map. Jobs ride the same app.jobs queue as ingest/harvest;
kinds are unchanged (the worker is untouched by the tool-surface move).
"""

from psycopg import sql as pgsql

import db
import geometry
import sessions
import sqlguard

# ── shared: area argument resolution ─────────────────────────────────────────

CHANGE_AREA_CAP_KM2 = 2.0
_AREA_FORMS = ("a layer ref like 'ref.byggnader' or '<ws schema>.sites', "
               "'xmin,ymin,xmax,ymax' in EPSG:3014, or WKT in EPSG:3014")


def _resolve_area_wkt(conn, area: str) -> tuple[str | None, str | None]:
    """Resolve the area argument to EPSG:3014 WKT. Returns (wkt, error).

    On the WKT-parse failure path the connection's transaction is aborted — callers
    must return the error without issuing further statements on this connection.
    """
    m = sqlguard.LAYER_REF_RE.match(area)
    if m:
        schema, table = m.group(1), m.group(2)
        exists = conn.execute("SELECT to_regclass(%s)", (area,)).fetchone()
        if exists is None or exists[0] is None:
            return None, f"table {area!r} does not exist — check layer(op='list')"
        geom_col = conn.execute(
            """SELECT column_name FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s AND udt_name = 'geometry'
                ORDER BY ordinal_position LIMIT 1""",
            (schema, table),
        ).fetchone()
        if not geom_col:
            return None, f"table {area!r} has no geometry column"
        row = conn.execute(
            pgsql.SQL("SELECT ST_AsText(ST_Envelope(ST_Extent({}))) FROM {}").format(
                pgsql.Identifier(geom_col[0]),
                sqlguard.qualified(schema, table))
        ).fetchone()
        if row is None or row[0] is None:
            return None, f"table {area!r} is empty — no extent to analyze"
        return row[0], None
    parts = [p.strip() for p in area.split(",")]
    if len(parts) == 4:
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            nums = None
        if nums is not None:
            row = conn.execute(
                "SELECT ST_AsText(ST_MakeEnvelope(%s, %s, %s, %s, 3014))", nums
            ).fetchone()
            return row[0], None
    try:
        row = conn.execute("SELECT ST_AsText(ST_GeomFromText(%s, 3014))", (area,)).fetchone()
        return row[0], None
    except Exception:
        return None, f"could not parse area {area!r} — pass {_AREA_FORMS}"


# ── processor: change_detect ─────────────────────────────────────────────────

def _run_change_detect(workspace_id: str, area: str | None, concepts: list | None,
                       collection_a: str | None, collection_b: str | None,
                       table_name: str | None, threshold: float | None,
                       min_area_m2: float | None, method: str | None,
                       gsd: float | None = None) -> dict:
    """Validate and enqueue a SAM3 orthophoto change-detection job (worker does the rest)."""
    if not isinstance(concepts, list) or not 1 <= len(concepts) <= 6:
        return {"error": "concepts must be a list of 1-6 ENGLISH noun phrases, "
                         "e.g. ['building', 'swimming pool', 'storage yard']"}
    clean_concepts = [str(c).strip() for c in concepts]
    if any(not 1 <= len(c) <= 80 for c in clean_concepts):
        return {"error": "each concept must be 1-80 characters after trimming"}
    if not collection_a or not collection_b:
        return {"error": "collection_a and collection_b are required — query catalog.datasets "
                         "(source kind 'stac' or 'wms') to list orthophoto collections/layers"}
    if collection_a == collection_b:
        return {"error": "collection_a and collection_b must be different vintages"}
    if not area or not isinstance(area, str):
        return {"error": f"area is required — pass {_AREA_FORMS}"}
    if not table_name:
        return {"error": "table_name is required — results land in <ws>.<table_name> "
                         "and <ws>.<table_name>_coverage"}
    if not sqlguard.LAYER_NAME_RE.match(table_name):
        return {"error": f"table_name {table_name!r} invalid — use ^[a-z][a-z0-9_]{{0,59}}$"}
    if len(table_name) > 54:
        # the '_coverage' companion must also fit Postgres's 63-char identifier limit
        return {"error": "table_name too long — max 54 chars so <table_name>_coverage "
                         "stays a valid identifier"}
    try:
        thr = 0.5 if threshold is None else float(threshold)
    except (TypeError, ValueError):
        return {"error": "threshold must be a number between 0.05 and 0.95"}
    if not 0.05 <= thr <= 0.95:
        return {"error": f"threshold {thr} out of bounds — use 0.05..0.95 (default 0.5)"}
    min_area = None
    if min_area_m2 is not None:
        try:
            min_area = float(min_area_m2)
        except (TypeError, ValueError):
            return {"error": "min_area_m2 must be a positive number"}
        if min_area <= 0:
            return {"error": "min_area_m2 must be a positive number"}
    method = method or "mask_compare"
    if method != "mask_compare":
        return {"error": f"method {method!r} not implemented — 'mask_compare' is the only "
                         "method today (raster_diff and dsm_diff are documented future methods)"}
    proc_gsd = None
    if gsd is not None:
        try:
            proc_gsd = float(gsd)
        except (TypeError, ValueError):
            return {"error": "gsd must be a number (metres per pixel)"}
        if not 0.05 <= proc_gsd <= 2.0:
            return {"error": f"gsd {proc_gsd} out of bounds — use 0.05..2.0 m/px "
                             "(only applies to WMS vintages; STAC items carry their own)"}

    ws = sessions.ws_schema_for(workspace_id)
    with db.app_pool().connection() as conn:
        for cid in (collection_a, collection_b):
            row = conn.execute(
                """SELECT s.kind FROM catalog.datasets d
                     JOIN catalog.sources s ON s.id = d.source_id
                          AND s.kind IN ('stac', 'wms')
                    WHERE d.external_id = %s""",
                (cid,),
            ).fetchone()
            if row is None:
                return {"error": f"unknown collection {cid!r} — query catalog.datasets "
                                 "(source kind 'stac' for STAC collections, 'wms' for "
                                 "orthophoto vintage layers)"}
        for t in (table_name, f"{table_name}_coverage"):
            exists = conn.execute("SELECT to_regclass(%s)", (f"{ws}.{t}",)).fetchone()
            if exists and exists[0] is not None:
                return {"error": f"{ws}.{t} already exists — drop it via layer(op='drop') "
                                 "or pick another table_name"}
        area_wkt, err = _resolve_area_wkt(conn, area)
        if err:
            return {"error": err}
        row = conn.execute(
            "SELECT ST_IsEmpty(g), ST_Dimension(g), ST_Area(g) / 1e6"
            " FROM (SELECT ST_GeomFromText(%s, 3014) AS g) t", (area_wkt,)
        ).fetchone()
        if row[0]:
            return {"error": f"area is empty — pass {_AREA_FORMS}"}
        if row[1] < 2:
            return {"error": f"area must be an areal geometry (polygon) — pass {_AREA_FORMS}"}
        area_km2 = float(row[2])
        if area_km2 > CHANGE_AREA_CAP_KM2:
            return {"error": f"area is {area_km2:.2f} km² — cap is {CHANGE_AREA_CAP_KM2:g} km² "
                             "per run; tile larger studies into multiple calls"}

    target_schema = sessions.ensure_ws_schema(workspace_id)
    payload = {"area_wkt_3014": area_wkt, "table_name": table_name,
               "target_schema": target_schema, "concepts": clean_concepts,
               "collection_a": collection_a, "collection_b": collection_b,
               "threshold": thr, "min_area_m2": min_area, "method": method,
               "gsd": proc_gsd}
    job_id = db.enqueue_job("change_detect", payload, workspace_id)
    job = db.wait_for_job(job_id, timeout_s=8.0)
    reply = {"job_id": job_id, "kind": "change_detect",
             "status": job["status"] if job else "queued"}
    if job:
        if job.get("result"):
            reply["result"] = job["result"]
        if job.get("error"):
            reply["error"] = job["error"]
    if reply["status"] in ("queued", "running"):
        reply["note"] = ("SAM3 inference typically runs minutes (the first call also loads "
                         "the model) — poll with analyze(op='status', job_id=...)")
    return geometry.jsonable_row(reply)


_CHANGE_DETECT_SCHEMA = {
    "type": "object",
    "required": ["area", "concepts", "collection_a", "collection_b", "table_name"],
    "additionalProperties": False,
    "properties": {
        "area": {
            "type": "string",
            "description": "Analysis area: a layer ref ('ref.<t>' or '<ws schema>.<t>' — "
                           "its bounding box is used), 'xmin,ymin,xmax,ymax' in EPSG:3014, "
                           "or EPSG:3014 WKT polygon. Max 2 km² per run.",
        },
        "concepts": {
            "type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 80},
            "minItems": 1, "maxItems": 6,
            "description": "1-6 free-text noun phrases, ENGLISH ONLY — the model's text "
                           "grounding fails silently on Swedish (verified: 'byggnad' finds "
                           "nothing where 'building' scores 0.8+). Translate first: "
                           "byggnad→'building', småhus→'house', upplag→'storage yard', "
                           "pool→'swimming pool', parkeringsplats→'parking lot'.",
        },
        "collection_a": {
            "type": "string",
            "description": "Earlier imagery vintage: a STAC orthophoto collection id "
                           "(catalog source kind 'stac', e.g. 'orto-t2-2021') or a WMS "
                           "orthophoto vintage layer (source kind 'wms', e.g. "
                           "'Lantmateriet:Orto2010_wms').",
        },
        "collection_b": {
            "type": "string",
            "description": "Later imagery vintage; same forms as collection_a. STAC and "
                           "WMS vintages can be mixed in one run.",
        },
        "table_name": {
            "type": "string", "pattern": "^[a-z][a-z0-9_]{0,53}$",
            "description": "Result layer name — writes <ws>.<table_name> and "
                           "<ws>.<table_name>_coverage (max 54 chars).",
        },
        "threshold": {
            "type": "number", "minimum": 0.05, "maximum": 0.95, "default": 0.5,
            "description": "Segmentation confidence threshold.",
        },
        "min_area_m2": {
            "type": "number", "exclusiveMinimum": 0,
            "description": "Drop candidates smaller than this; default 15·(gsd/0.16)² m².",
        },
        "method": {
            "type": "string", "enum": ["mask_compare"], "default": "mask_compare",
            "description": "Comparison method (raster_diff / dsm_diff are future work).",
        },
        "gsd": {
            "type": "number", "minimum": 0.05, "maximum": 2.0,
            "description": "Processing resolution in m/px for WMS vintages (default 0.25); "
                           "STAC items carry their own.",
        },
    },
}

_CHANGE_DETECT_GUIDE = """\
Compare two orthophoto vintages with SAM3 concept segmentation and write where
concepts appeared / disappeared / changed to your workspace. Results are change
CANDIDATES for review, not conclusions — inspect them against the imagery
before reporting anything.

List available vintages via the query tool:
  SELECT d.external_id, s.kind FROM catalog.datasets d
    JOIN catalog.sources s ON s.id = d.source_id AND s.kind IN ('stac','wms')
   WHERE d.external_id ~* 'orto'
The Sundsvall GeoServer's Lantmäteriet cascades (e.g.
'Lantmateriet:HistoriskaOrtofoton1975_wms') are free — no Lantmäteriet account
needed. WMS vintages have no capture-date metadata — the vintage year comes
from the layer name, so cross-season false positives cannot be warned about.

Output layers:
  <ws>.<table_name>           candidate polygons: concept, change_class
                              ('appeared'|'disappeared'|'changed'), confidence_a,
                              confidence_b, iou, area_m2, vintage/datetime
                              columns, geom (EPSG:3014)
  <ws>.<table_name>_coverage  per-tile analysis status: 'analyzed'|'missing_a'|
                              'missing_b'|'error', gsd_m, geom

Reading absence correctly: no candidate rows over an 'analyzed' tile means no
change found there; any OTHER coverage status means the tile was NOT analyzed —
check coverage before treating absence as evidence.

Runs minutes (the first call also loads the model); analyze(op='run') returns a
job_id — poll with analyze(op='status'). Follow up: query the result table, map
it over the orthophoto WMS ('wms:<dataset id>' as basemap — the map tool
auto-attaches the before/after vintages and the viewer's compare inspector),
refine concepts, re-run on subareas."""


def _run_change_detect_params(workspace_id: str, params: dict) -> dict:
    return _run_change_detect(
        workspace_id,
        area=params.get("area"),
        concepts=params.get("concepts"),
        collection_a=params.get("collection_a"),
        collection_b=params.get("collection_b"),
        table_name=params.get("table_name"),
        threshold=params.get("threshold"),
        min_area_m2=params.get("min_area_m2"),
        method=params.get("method"),
        gsd=params.get("gsd"),
    )


# ── the registry ─────────────────────────────────────────────────────────────
# One entry per processor: constant tool surface, growth happens here.

REGISTRY = {
    "change_detect": {
        "title": "Orthophoto change detection (SAM3)",
        "summary": "Where did concepts appear/disappear/change between two imagery "
                   "vintages? Writes candidate + coverage layers.",
        "guide": _CHANGE_DETECT_GUIDE,
        "schema": _CHANGE_DETECT_SCHEMA,
        "run": _run_change_detect_params,
    },
}


# ── ops ──────────────────────────────────────────────────────────────────────

def list_processors() -> dict:
    return {"processors": [
        {"id": pid, "title": spec["title"], "summary": spec["summary"]}
        for pid, spec in REGISTRY.items()
    ], "note": "analyze(op='describe', id=...) returns a processor's full guidance "
               "and the JSON Schema of its params"}


def describe(processor_id: str | None) -> dict:
    spec = REGISTRY.get(processor_id or "")
    if spec is None:
        return {"error": f"unknown processor {processor_id!r} — "
                         f"known: {', '.join(sorted(REGISTRY))}"}
    return {"id": processor_id, "title": spec["title"], "summary": spec["summary"],
            "guide": spec["guide"], "params_schema": spec["schema"]}


def run(workspace_id: str, processor_id: str | None, params: dict | None) -> dict:
    spec = REGISTRY.get(processor_id or "")
    if spec is None:
        return {"error": f"unknown processor {processor_id!r} — "
                         f"known: {', '.join(sorted(REGISTRY))}; "
                         "discover with analyze(op='list')"}
    if not isinstance(params, dict):
        return {"error": "params must be an object — analyze(op='describe', "
                         f"id='{processor_id}') returns its schema"}
    unknown = set(params) - set(spec["schema"]["properties"])
    if unknown:
        return {"error": f"unknown param(s) {sorted(unknown)} for {processor_id!r} — "
                         f"schema allows {sorted(spec['schema']['properties'])}"}
    missing = [k for k in spec["schema"].get("required", ()) if params.get(k) is None]
    if missing:
        return {"error": f"missing required param(s) {missing} — analyze(op='describe', "
                         f"id='{processor_id}') explains each"}
    return spec["run"](workspace_id, params)


def status(job_id, timeout_s: float | None = None) -> dict:
    try:
        jid = int(job_id)
    except (TypeError, ValueError):
        return {"error": "job_id must be an integer"}
    wait = min(max(float(timeout_s or 0), 0.0), 25.0)
    job = db.wait_for_job(jid, timeout_s=wait) if wait > 0 else db.get_job(jid)
    if job is None:
        return {"error": f"no job with id {job_id}"}
    out = geometry.jsonable_row(job)
    if job["status"] == "done" and isinstance(job.get("result"), dict):
        tbl = job["result"].get("table")
        if tbl:
            out["note"] = (f"result layers ready: {tbl} (+ coverage) — read with the "
                           "query tool, style with layer, show with map")
    return out


def cancel(job_id) -> dict:
    try:
        jid = int(job_id)
    except (TypeError, ValueError):
        return {"error": "job_id must be an integer"}
    with db.app_pool().connection() as conn:
        row = conn.execute(
            """UPDATE app.jobs
                  SET status = 'cancelled', error = 'cancelled before start',
                      finished_at = now()
                WHERE id = %s AND status = 'queued'
                RETURNING id""",
            (jid,),
        ).fetchone()
        if row:
            return {"job_id": jid, "status": "cancelled"}
        cur = conn.execute("SELECT status FROM app.jobs WHERE id = %s", (jid,)).fetchone()
    if cur is None:
        return {"error": f"no job with id {job_id}"}
    st = cur[0]
    if st == "running":
        return {"error": f"job {jid} is already running — cooperative interruption is "
                         "not implemented; it will finish or error on its own"}
    return {"job_id": jid, "status": st,
            "note": "only queued jobs can be cancelled; this one already finished"}
