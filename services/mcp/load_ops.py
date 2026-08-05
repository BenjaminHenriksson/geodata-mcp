"""Implementation of the load tool: register / ingest / inline / status / jobs."""

import re
import unicodedata

from psycopg import sql as pgsql
from psycopg.rows import dict_row

import db
import geometry
import provenance
import sessions
import sqlguard

SOURCE_KINDS = ("wfs", "wms", "wmts", "ogcapi", "file", "pdf", "text", "stac", "inline")
HARVEST_JOB = {"wfs": "harvest_wfs", "wms": "harvest_wms", "wmts": "harvest_wmts",
               "ogcapi": "harvest_ogcapi", "stac": "harvest_stac"}


def slugify(text: str, max_len: int = 55) -> str:
    s = unicodedata.normalize("NFKD", text.lower())
    s = s.replace("å", "a").replace("ä", "a").replace("ö", "o")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9_]+", "_", s).strip("_")
    s = re.sub(r"_+", "_", s)[:max_len].strip("_")
    return s or "source"


def _unique_slug(conn, base: str) -> str:
    slug, n = base, 1
    while True:
        row = conn.execute("SELECT 1 FROM catalog.sources WHERE slug = %s", (slug,)).fetchone()
        if row is None:
            return slug
        n += 1
        slug = f"{base}_{n}"


def register(workspace_id: str, kind: str, url: str | None, title: str,
             slug: str | None, license: str, notes: str) -> dict:
    if kind not in SOURCE_KINDS:
        return {"error": f"kind must be one of {', '.join(SOURCE_KINDS)}"}
    if not title:
        return {"error": "title is required"}
    if kind in ("wfs", "wms", "wmts", "ogcapi", "stac", "text") and not url:
        return {"error": f"url is required for kind {kind!r}"}
    with db.app_pool().connection() as conn:
        # Registering the same endpoint twice must not fork the catalog: a second
        # harvest would insert a parallel set of datasets (doubling search results and
        # embedding cost) that describe exactly the same layers.
        if url:
            existing = conn.execute(
                "SELECT id::text, slug FROM catalog.sources WHERE kind = %s AND url = %s",
                (kind, url),
            ).fetchone()
            if existing:
                job_id = (db.enqueue_job(HARVEST_JOB[kind], {"source_id": existing[0]}, workspace_id)
                          if kind in HARVEST_JOB else None)
                return {"source_id": existing[0], "slug": existing[1], "job_id": job_id,
                        "note": "source already registered — re-harvesting to refresh its "
                                "datasets rather than creating a duplicate"}
        base = slugify(slug) if slug else slugify(title)
        final_slug = _unique_slug(conn, base)
        row = conn.execute(
            """INSERT INTO catalog.sources (slug, kind, url, title, description, license, added_by, trust)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'agent') RETURNING id""",
            (final_slug, kind, url, title, notes or "", license or "", workspace_id),
        ).fetchone()
        source_id = str(row[0])
    job_id = None
    dataset_id = None
    if kind in HARVEST_JOB:
        job_id = db.enqueue_job(HARVEST_JOB[kind], {"source_id": source_id}, workspace_id)
    elif kind in ("pdf", "file", "text"):
        # No harvest step for single-artifact sources: the dataset row exists immediately
        # so op='ingest' can target it (contract §MCP server, load op register).
        if not url:
            return {"error": f"url is required for kind {kind!r}"}
        ds_kind = "vector" if kind == "file" else "document"
        with db.app_pool().connection() as conn:
            row = conn.execute(
                """INSERT INTO catalog.datasets (source_id, external_id, kind, title, description)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (source_id, external_id) DO UPDATE SET updated_at = now()
                   RETURNING id""",
                (source_id, url, ds_kind, title, notes or ""),
            ).fetchone()
            dataset_id = str(row[0])
    out = {"source_id": source_id, "slug": final_slug, "job_id": job_id,
           "note": "harvest job enqueued — poll with op='status'" if job_id else
                   "use op='ingest' to load the dataset"}
    if dataset_id:
        out["dataset_id"] = dataset_id
    return out


def _dataset_and_source(dataset_id: str) -> dict | None:
    with db.app_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT d.id::text AS id, d.kind, d.external_id, d.title, d.ref_table,
                          s.kind AS source_kind, s.url AS source_url
                     FROM catalog.datasets d JOIN catalog.sources s ON s.id = d.source_id
                    WHERE d.id = %s""",
                (dataset_id,),
            )
            return cur.fetchone()


def _ref_table_claim(qualified: str, dataset_id: str) -> str | None:
    """Describe what already occupies a target table, unless it is this dataset's own."""
    with db.app_pool().connection() as conn:
        row = conn.execute(
            """SELECT d.external_id FROM catalog.datasets d
                WHERE d.ref_table = %s AND d.id <> %s LIMIT 1""",
            (qualified, dataset_id),
        ).fetchone()
        if row:
            return f"dataset {row[0]!r}"
        row = conn.execute(
            """SELECT 1 FROM catalog.datasets WHERE ref_table = %s AND id = %s""",
            (qualified, dataset_id),
        ).fetchone()
        if row:
            return None  # re-ingesting the same dataset: overwrite is intended
        exists = conn.execute("SELECT to_regclass(%s) IS NOT NULL", (qualified,)).fetchone()[0]
        return "an existing table not registered to any dataset" if exists else None


def ingest(workspace_id: str, dataset_id: str, table_name: str | None, target: str) -> dict:
    if target not in ("ref", "workspace"):
        return {"error": "target must be 'ref' or 'workspace'"}
    ds = _dataset_and_source(dataset_id)
    if ds is None:
        return {"error": f"no dataset with id {dataset_id!r}"}

    if ds["kind"] == "document" or ds["source_kind"] == "pdf":
        job_kind = "ingest_text" if ds["source_kind"] == "text" else "ingest_pdf"
        doc_url = ds["external_id"] if str(ds["external_id"]).startswith("http") else ds["source_url"]
        payload = {"dataset_id": ds["id"], "title": ds["title"], "url": doc_url}
    elif ds["source_kind"] == "file":
        job_kind = "ingest_file"
        payload = {"dataset_id": ds["id"], "path": ds["external_id"], "url": ds["source_url"]}
    elif ds["kind"] == "vector":
        # The driver must match the source protocol: OGC API collections go through
        # GDAL's OAPIF driver, everything else through the WFS driver.
        job_kind = "ingest_ogcapi" if ds["source_kind"] == "ogcapi" else "ingest_wfs"
        payload = {"dataset_id": ds["id"]}
    else:
        return {"error": f"dataset kind {ds['kind']!r} (source kind {ds['source_kind']!r}) "
                         "is not ingestable — raster_ref layers are referenced on maps via 'wms:<id>'"}

    if job_kind != "ingest_pdf":
        if target == "workspace":
            target_schema = sessions.ensure_ws_schema(workspace_id)
        else:
            target_schema = "ref"
        tname = table_name or slugify(ds["external_id"].split(":")[-1] or ds["title"], 58)
        if not sqlguard.LAYER_NAME_RE.match(tname) and table_name is None:
            tname = "t_" + tname  # derived name starting with a digit etc.
        if not sqlguard.LAYER_NAME_RE.match(tname):
            return {"error": f"table_name {tname!r} invalid — use ^[a-z][a-z0-9_]{{0,59}}$"}
        # ogr2ogr runs with -overwrite, so an unnoticed name collision would silently
        # replace a different dataset's data while the catalog still points both rows at it.
        claim = _ref_table_claim(f"{target_schema}.{tname}", ds["id"])
        if claim:
            return {"error": f"{target_schema}.{tname} already holds {claim} — "
                             "pass an explicit table_name to load this dataset alongside it"}
        payload.update({"target_schema": target_schema, "table_name": tname})

    job_id = db.enqueue_job(job_kind, payload, workspace_id)
    job = db.wait_for_job(job_id, timeout_s=8.0)
    reply = {"job_id": job_id, "kind": job_kind, "status": job["status"] if job else "queued"}
    if job:
        if job.get("result"):
            reply["result"] = job["result"]
        if job.get("error"):
            reply["error"] = job["error"]
    if reply["status"] in ("queued", "running"):
        reply["note"] = "still running — poll with load(op='status', job_id=...)"
    return geometry.jsonable_row(reply)


# ── change detection ─────────────────────────────────────────────────────────

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


def change_detect(workspace_id: str, area: str | None, concepts: list | None,
                  collection_a: str | None, collection_b: str | None,
                  table_name: str | None, threshold: float | None,
                  min_area_m2: float | None, method: str | None) -> dict:
    """Validate and enqueue a SAM3 orthophoto change-detection job (worker does the rest)."""
    if not isinstance(concepts, list) or not 1 <= len(concepts) <= 6:
        return {"error": "concepts must be a list of 1-6 noun phrases, "
                         "e.g. ['byggnad', 'pool', 'upplag']"}
    clean_concepts = [str(c).strip() for c in concepts]
    if any(not 1 <= len(c) <= 80 for c in clean_concepts):
        return {"error": "each concept must be 1-80 characters after trimming"}
    if not collection_a or not collection_b:
        return {"error": "collection_a and collection_b are required — query catalog.datasets "
                         "(source kind 'stac') to list orthophoto collections"}
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

    ws = sessions.ws_schema_for(workspace_id)
    with db.app_pool().connection() as conn:
        for cid in (collection_a, collection_b):
            row = conn.execute(
                """SELECT 1 FROM catalog.datasets d
                     JOIN catalog.sources s ON s.id = d.source_id AND s.kind = 'stac'
                    WHERE d.external_id = %s""",
                (cid,),
            ).fetchone()
            if row is None:
                return {"error": f"unknown collection {cid!r} — query catalog.datasets "
                                 "(source kind 'stac') to list orthophoto collections"}
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
               "threshold": thr, "min_area_m2": min_area, "method": method}
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
                         "the model) — poll with load(op='status', job_id=...)")
    return geometry.jsonable_row(reply)


# ── inline rows ──────────────────────────────────────────────────────────────

_GEO_KEYS = ("wkt", "lon", "lat")


def inline(workspace_id: str, rows: list, table_name: str, source: str | None,
           crs: str | None = None) -> dict:
    if not source:
        return {"error": "source is mandatory for inline data — state where these rows come from"}
    if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
        return {"error": "rows must be a non-empty list of flat objects"}
    if not table_name or not sqlguard.LAYER_NAME_RE.match(table_name):
        return {"error": "table_name must match ^[a-z][a-z0-9_]{0,59}$"}
    srid_in = 4326 if str(crs or "") == "4326" else 3014

    attr_cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in _GEO_KEYS and k not in attr_cols:
                attr_cols.append(k)
    for c in attr_cols:
        if not sqlguard.IDENT_RE.match(c):
            return {"error": f"column name {c!r} invalid — use lowercase [a-z0-9_], max 63 chars"}
    has_wkt = any("wkt" in r for r in rows)
    has_lonlat = any("lon" in r and "lat" in r for r in rows)
    has_geom = has_wkt or has_lonlat
    if not attr_cols and not has_geom:
        return {"error": "rows contain no columns — provide attributes and/or 'wkt' or 'lon'/'lat'"}
    col_types = {c: geometry.infer_pg_type([r.get(c) for r in rows]) for c in attr_cols}

    ws = sessions.ensure_ws_schema(workspace_id)
    table = sqlguard.qualified(ws, table_name)

    col_defs = [
        pgsql.SQL("{} {}").format(pgsql.Identifier(c), pgsql.SQL(col_types[c]))
        for c in attr_cols
    ]
    if has_geom:
        col_defs.append(pgsql.SQL("geom geometry(Geometry, 3014)"))

    placeholders = [pgsql.Placeholder() for _ in attr_cols]
    if has_geom:
        if has_wkt:
            geom_expr = "ST_Transform(ST_GeomFromText(%s, {srid}), 3014)".format(srid=srid_in) \
                if srid_in != 3014 else "ST_GeomFromText(%s, 3014)"
        else:
            geom_expr = "ST_Transform(ST_SetSRID(ST_MakePoint(%s, %s), 4326), 3014)"
        insert_sql = pgsql.SQL("INSERT INTO {} ({}) VALUES ({}, " + geom_expr + ")").format(
            table,
            pgsql.SQL(", ").join([pgsql.Identifier(c) for c in attr_cols] + [pgsql.SQL("geom")]),
            pgsql.SQL(", ").join(placeholders),
        ) if attr_cols else pgsql.SQL("INSERT INTO {} (geom) VALUES (" + geom_expr + ")").format(table)
    else:
        insert_sql = pgsql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            table,
            pgsql.SQL(", ").join(pgsql.Identifier(c) for c in attr_cols),
            pgsql.SQL(", ").join(placeholders),
        )

    try:
        with db.ws_pool().connection() as conn:
            with conn.transaction():
                conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace_id,))
                conn.execute(pgsql.SQL("CREATE TABLE {} ({})").format(
                    table, pgsql.SQL(", ").join(col_defs)))
                for r in rows:
                    params = [geometry.coerce_for_type(r.get(c), col_types[c]) for c in attr_cols]
                    if has_geom:
                        if has_wkt:
                            params.append(r.get("wkt"))
                        else:
                            params.append(r.get("lon"))
                            params.append(r.get("lat"))
                    conn.execute(insert_sql, params)
                if has_geom:
                    conn.execute(pgsql.SQL("CREATE INDEX ON {} USING gist (geom)").format(table))
                conn.execute(pgsql.SQL("GRANT SELECT ON {} TO agent_ro").format(table))
    except Exception as e:
        return {"error": f"inline load failed: {str(e).strip()}",
                "hint": "check WKT validity / lon-lat order (lon first) and that the table does not already exist"}

    obj = f"{ws}.{table_name}"
    provenance.add("inline", workspace_id, object_ref=obj,
                   details={"source": source, "rows": len(rows), "crs_in": srid_in,
                            "columns": col_types, "geometry": has_geom})
    return {"table": obj, "row_count": len(rows),
            "columns": list(col_types) + (["geom"] if has_geom else [])}


def embed(workspace_id: str) -> dict:
    """Enqueue an embed_catalog job (idempotent: only missing/model-mismatched rows)."""
    job_id = db.enqueue_job("embed_catalog", {}, workspace_id)
    return {"job_id": job_id,
            "note": "embedding catalog + doc chunks with EmbeddingGemma — first run downloads "
                    "the model and can take minutes; poll with op='status'"}


def status(job_id) -> dict:
    try:
        job = db.get_job(int(job_id))
    except (TypeError, ValueError):
        return {"error": "job_id must be an integer"}
    if job is None:
        return {"error": f"no job with id {job_id}"}
    return geometry.jsonable_row(job)


def jobs() -> dict:
    return {"jobs": [geometry.jsonable_row(j) for j in db.recent_jobs(20)]}
