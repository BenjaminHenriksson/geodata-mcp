"""File ingestion (ingest_file) plus the ogr2ogr helpers shared with the WFS
connector: run-with-retry, download-with-cap, and the post-load finalization
(count, schema summary, catalog update, grants, provenance, address view).
"""

import logging
import os
import re
import subprocess
import urllib.parse

import httpx
from psycopg import sql
from psycopg.types.json import Json

import dbutil

log = logging.getLogger("worker.files")

OGR_TIMEOUT_S = 900  # 15 minutes
FILE_DOWNLOAD_CAP = 200 * 1024 * 1024  # 200 MB

ADDRESS_COL_RE = "(adress|namn|nr|nummer|bokstav)"
WS_SCHEMA_RE = re.compile(r"^ws_[a-f0-9]{8}$")


class OgrError(RuntimeError):
    pass


def run_ogr2ogr(args, timeout=OGR_TIMEOUT_S):
    """Run ogr2ogr (list argv), capture output, raise OgrError on failure."""
    log.info("ogr2ogr: %s", dbutil.redact(" ".join(args)))
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ogr2ogr timed out after {timeout} s") from exc
    if proc.returncode != 0:
        tail = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()[-2000:]
        raise OgrError(f"ogr2ogr exited {proc.returncode}: {dbutil.redact(tail)}")
    return proc


def run_ogr2ogr_with_retry(args, timeout=OGR_TIMEOUT_S):
    """On ogr2ogr failure, retry once with -skipfailures appended."""
    try:
        run_ogr2ogr(args, timeout=timeout)
    except OgrError as exc:
        log.warning("ogr2ogr failed, retrying with -skipfailures: %s", exc)
        run_ogr2ogr(list(args) + ["-skipfailures"], timeout=timeout)


def download(url: str, dest: str, max_bytes: int, timeout: float = 60.0) -> int:
    """Stream url to dest with a byte cap; returns bytes written."""
    total = 0
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_bytes(65536):
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError(
                        f"download exceeds {max_bytes} byte cap: {url}"
                    )
                fh.write(chunk)
    return total


def base_load_args(source: str, schema: str, table: str, layer_name=None):
    """Common ogr2ogr → PostgreSQL argv for a vector load into schema.table."""
    args = ["ogr2ogr", "-f", "PostgreSQL", dbutil.pg_ogr_dsn(), source]
    if layer_name:
        args.append(layer_name)
    args += [
        "-nln", f"{schema}.{table}",
        "-t_srs", "EPSG:3014",
        # GML curve geometries (MULTICURVE/MULTISURFACE) break ST_AsMVTGeom and most
        # GIS consumers — linearize before promoting to multi.
        "-nlt", "CONVERT_TO_LINEAR",
        "-nlt", "PROMOTE_TO_MULTI",
        "-lco", "GEOMETRY_NAME=geom",
        "-lco", "FID=fid",
        "-lco", "SPATIAL_INDEX=GIST",
        "-overwrite",
        "--config", "PG_USE_COPY", "YES",
    ]
    return args


def _create_address_view(cur, schema: str, table: str) -> None:
    """(Re)create app.address_points over an ingested address table: concat of
    up to 5 text-ish columns whose names look address-like, plus geom."""
    cur.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = %s AND table_name = %s
           AND data_type IN ('text', 'character varying', 'character')
           AND column_name ~ %s
         ORDER BY ordinal_position
         LIMIT 5
        """,
        (schema, table, ADDRESS_COL_RE),
    )
    cols = [row["column_name"] for row in cur.fetchall()]
    if not cols:
        log.warning(
            "table %s.%s contains 'adressplats' but has no address-like text "
            "columns; skipping app.address_points view", schema, table,
        )
        return
    # Swedish address layers typically carry the full street address in a single
    # 'adress' column plus postal columns; variants like adress2/adressplat repeat
    # the same string and would pollute trigram scores. Prefer the precise trio.
    preferred = [c for c in ("adress", "postnr", "postort") if c in cols]
    if "adress" in preferred:
        cols = preferred
    cur.execute("DROP VIEW IF EXISTS app.address_points")
    cur.execute(
        sql.SQL(
            "CREATE VIEW app.address_points AS "
            "SELECT concat_ws(' ', {cols}) AS addr, geom FROM {tbl}"
        ).format(
            cols=sql.SQL(", ").join(sql.Identifier(c) for c in cols),
            tbl=dbutil.qualify(schema, table),
        )
    )
    cur.execute("GRANT SELECT ON app.address_points TO agent_ro, agent_ws")
    log.info("created app.address_points over %s.%s (columns: %s)", schema, table, cols)


def repair_geometries(cur, schema: str, table: str) -> int:
    """Make invalid geometries valid, returning how many were repaired.

    Municipal planning polygons routinely arrive self-intersecting. PostGIS tolerates
    that for ST_Area, but ST_Intersects / ST_Union / ST_Intersection raise
    TopologyException — i.e. the ordinary spatial joins this system exists to run would
    fail on the detaljplan layers. Repairing at ingest is recorded in provenance and in
    the dataset's schema_summary, so a repaired layer is never silently presented as
    pristine source data.
    """
    tbl = dbutil.qualify(schema, table)
    cur.execute(sql.SQL("SELECT count(*) AS n FROM {} WHERE geom IS NOT NULL AND NOT ST_IsValid(geom)")
                .format(tbl))
    invalid = int(cur.fetchone()["n"])
    if not invalid:
        return 0

    # Keep only parts of the repaired result matching the layer's own dimension, so a
    # self-intersecting polygon cannot decay into stray lines/points.
    cur.execute(sql.SQL("SELECT GeometryType(geom) AS t FROM {} WHERE geom IS NOT NULL LIMIT 1")
                .format(tbl))
    row = cur.fetchone()
    gtype = (row["t"] or "").upper() if row else ""
    dim = 1 if "POINT" in gtype else 2 if "LINE" in gtype else 3

    cur.execute(
        sql.SQL("UPDATE {} SET geom = ST_Multi(ST_CollectionExtract(ST_MakeValid(geom), %s)) "
                "WHERE geom IS NOT NULL AND NOT ST_IsValid(geom)").format(tbl),
        (dim,),
    )
    cur.execute(sql.SQL("SELECT count(*) AS n FROM {} WHERE geom IS NOT NULL AND NOT ST_IsValid(geom)")
                .format(tbl))
    still_bad = int(cur.fetchone()["n"])
    log.warning("repaired %d invalid geometries in %s.%s (%d still invalid)",
                invalid - still_bad, schema, table, still_bad)
    return invalid - still_bad


def finalize_load(conn, job, schema: str, table: str, dataset_id=None, details=None) -> dict:
    """Post-ogr2ogr bookkeeping shared by ingest_wfs and ingest_file."""
    with conn.cursor() as cur:
        repaired = repair_geometries(cur, schema, table)
        dbutil.analyze(cur, schema, table)
        feature_count = dbutil.count_rows(cur, schema, table)
        summary = dbutil.table_schema_summary(cur, schema, table)
        if repaired:
            summary["geometries_repaired"] = repaired
            details = dict(details or {})
            details["geometries_repaired"] = repaired

        if dataset_id is not None:
            if schema == "ref":
                cur.execute(
                    """
                    UPDATE catalog.datasets
                       SET ref_table = %s, feature_count = %s,
                           schema_summary = %s, updated_at = now()
                     WHERE id = %s
                    """,
                    (f"ref.{table}", feature_count, Json(summary), dataset_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE catalog.datasets
                       SET feature_count = %s, schema_summary = %s, updated_at = now()
                     WHERE id = %s
                    """,
                    (feature_count, Json(summary), dataset_id),
                )

        dbutil.grant_select(cur, schema, table)
        if WS_SCHEMA_RE.match(schema):
            # ogr2ogr connects as geodata_app, so a workspace load would land as a table
            # the layer tool (agent_ws) cannot drop, rename or update. Hand it over.
            cur.execute(
                sql.SQL("ALTER TABLE {} OWNER TO agent_ws").format(
                    dbutil.qualify(schema, table))
            )
        dbutil.insert_provenance(
            cur,
            kind="load",
            object_ref=f"{schema}.{table}",
            workspace_id=job.get("workspace_id"),
            input_tables=[],
            job_id=job["id"],
            details=details or {},
        )
        if "adressplats" in table:
            _create_address_view(cur, schema, table)

    out = {"table": f"{schema}.{table}", "feature_count": feature_count}
    if repaired:
        out["geometries_repaired"] = repaired
    return out


def _safe_filename(url_or_path: str) -> str:
    name = os.path.basename(urllib.parse.urlsplit(url_or_path).path)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name or "download.dat"


def ingest_file(conn, job) -> dict:
    """Job handler: ingest a GDAL-readable file (local path or URL) into
    target_schema.table_name via ogr2ogr."""
    payload = job["payload"]
    src = payload.get("url_or_path") or payload.get("url") or payload.get("path")
    if not src:
        raise ValueError("ingest_file payload requires url_or_path")
    schema = dbutil.check_schema_name(payload["target_schema"])
    table = dbutil.check_table_name(payload["table_name"])

    tmp_path = None
    try:
        if src.lower().startswith(("http://", "https://")):
            tmp_path = f"/tmp/ingest_{job['id']}_{_safe_filename(src)}"
            size = download(src, tmp_path, FILE_DOWNLOAD_CAP)
            log.info("downloaded %s (%d bytes) -> %s", src, size, tmp_path)
            source_path = tmp_path
        else:
            if not os.path.exists(src):
                raise FileNotFoundError(f"file not found: {src}")
            source_path = src

        args = base_load_args(source_path, schema, table)
        run_ogr2ogr_with_retry(args)

        details = {
            "source_url": src,
            "external_id": None,
            "ogr_cmd": dbutil.redact(" ".join(args)),
        }
        return finalize_load(conn, job, schema, table, dataset_id=None, details=details)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
