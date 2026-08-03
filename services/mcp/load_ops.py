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
HARVEST_JOB = {"wfs": "harvest_wfs", "wms": "harvest_wms"}


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


def register(session_id: str, kind: str, url: str | None, title: str,
             slug: str | None, license: str, notes: str) -> dict:
    if kind not in SOURCE_KINDS:
        return {"error": f"kind must be one of {', '.join(SOURCE_KINDS)}"}
    if not title:
        return {"error": "title is required"}
    if kind in ("wfs", "wms", "wmts", "ogcapi", "stac") and not url:
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
                job_id = (db.enqueue_job(HARVEST_JOB[kind], {"source_id": existing[0]}, session_id)
                          if kind in HARVEST_JOB else None)
                return {"source_id": existing[0], "slug": existing[1], "job_id": job_id,
                        "note": "source already registered — re-harvesting to refresh its "
                                "datasets rather than creating a duplicate"}
        base = slugify(slug) if slug else slugify(title)
        final_slug = _unique_slug(conn, base)
        row = conn.execute(
            """INSERT INTO catalog.sources (slug, kind, url, title, description, license, added_by, trust)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'agent') RETURNING id""",
            (final_slug, kind, url, title, notes or "", license or "", session_id),
        ).fetchone()
        source_id = str(row[0])
    job_id = None
    dataset_id = None
    if kind in HARVEST_JOB:
        job_id = db.enqueue_job(HARVEST_JOB[kind], {"source_id": source_id}, session_id)
    elif kind in ("pdf", "file"):
        # No harvest step for single-artifact sources: the dataset row exists immediately
        # so op='ingest' can target it (contract §MCP server, load op register).
        if not url:
            return {"error": f"url is required for kind {kind!r}"}
        ds_kind = "document" if kind == "pdf" else "vector"
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


def ingest(session_id: str, dataset_id: str, table_name: str | None, target: str) -> dict:
    if target not in ("ref", "workspace"):
        return {"error": "target must be 'ref' or 'workspace'"}
    ds = _dataset_and_source(dataset_id)
    if ds is None:
        return {"error": f"no dataset with id {dataset_id!r}"}

    if ds["kind"] == "document" or ds["source_kind"] == "pdf":
        job_kind = "ingest_pdf"
        pdf_url = ds["external_id"] if str(ds["external_id"]).startswith("http") else ds["source_url"]
        payload = {"dataset_id": ds["id"], "title": ds["title"], "url": pdf_url}
    elif ds["source_kind"] == "file":
        job_kind = "ingest_file"
        payload = {"dataset_id": ds["id"], "path": ds["external_id"], "url": ds["source_url"]}
    elif ds["kind"] == "vector":
        job_kind = "ingest_wfs"
        payload = {"dataset_id": ds["id"]}
    else:
        return {"error": f"dataset kind {ds['kind']!r} (source kind {ds['source_kind']!r}) "
                         "is not ingestable — raster_ref layers are referenced on maps via 'wms:<id>'"}

    if job_kind != "ingest_pdf":
        if target == "workspace":
            target_schema = sessions.ensure_ws_schema(session_id)
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

    job_id = db.enqueue_job(job_kind, payload, session_id)
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


# ── inline rows ──────────────────────────────────────────────────────────────

_GEO_KEYS = ("wkt", "lon", "lat")


def inline(session_id: str, rows: list, table_name: str, source: str | None,
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

    ws = sessions.ensure_ws_schema(session_id)
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
                conn.execute("SELECT set_config('app.session_id', %s, true)", (session_id,))
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
    provenance.add("inline", session_id, object_ref=obj,
                   details={"source": source, "rows": len(rows), "crs_in": srid_in,
                            "columns": col_types, "geometry": has_geom})
    return {"table": obj, "row_count": len(rows),
            "columns": list(col_types) + (["geom"] if has_geom else [])}


def embed(session_id: str) -> dict:
    """Enqueue an embed_catalog job (idempotent: only missing/model-mismatched rows)."""
    job_id = db.enqueue_job("embed_catalog", {}, session_id)
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
