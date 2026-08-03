"""Export job: ogr2ogr layers out of PostGIS into gpkg/geojson/csv/parquet,
zip multi-file results, upload to MinIO, and build a provenance/citation
sidecar. Also owns the MinIO client + bucket bootstrap used at startup.
"""

import datetime
import logging
import os
import shutil
import time
import uuid
import zipfile

from minio import Minio
from minio.error import S3Error
from psycopg.types.json import Json

import dbutil
from connectors import files

log = logging.getLogger("worker.exporter")

S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://minio:9000")
S3_BUCKET = os.environ.get("S3_BUCKET", "exports")
MINIO_ROOT_USER = os.environ.get("MINIO_ROOT_USER", "geodata")
MINIO_ROOT_PASSWORD = os.environ.get("MINIO_ROOT_PASSWORD", "geodata_dev_minio")

FORMATS = {
    "gpkg": ("GPKG", "gpkg"),
    "geojson": ("GeoJSON", "geojson"),
    "csv": ("CSV", "csv"),
    "parquet": ("Parquet", "parquet"),
}


# ── MinIO ───────────────────────────────────────────────────────────────────

def minio_client() -> Minio:
    endpoint = S3_ENDPOINT.split("://", 1)[-1]
    return Minio(
        endpoint,
        access_key=MINIO_ROOT_USER,
        secret_key=MINIO_ROOT_PASSWORD,
        secure=False,
    )


def ensure_bucket(client=None) -> None:
    client = client or minio_client()
    try:
        if not client.bucket_exists(S3_BUCKET):
            client.make_bucket(S3_BUCKET)
            log.info("created MinIO bucket %s", S3_BUCKET)
    except S3Error as exc:
        # Tolerate creation races.
        if exc.code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise


def startup_ensure_bucket(max_wait: float = 60.0) -> None:
    """Best-effort bucket bootstrap at startup (MinIO may still be waking up).
    Export jobs re-check the bucket, so failure here is non-fatal."""
    deadline = time.monotonic() + max_wait
    while True:
        try:
            ensure_bucket()
            log.info("MinIO bucket %s ready", S3_BUCKET)
            return
        except Exception as exc:
            if time.monotonic() > deadline:
                log.warning("could not ensure MinIO bucket %s: %s", S3_BUCKET, exc)
                return
            time.sleep(2)


# ── citation sidecar ────────────────────────────────────────────────────────

def _provenance_rows(cur, object_ref):
    cur.execute(
        """
        SELECT kind, ts, sql_text, input_tables, job_id, details
          FROM app.provenance
         WHERE object_ref = %s AND kind IN ('load', 'layer_create', 'inline')
         ORDER BY ts DESC
        """,
        (object_ref,),
    )
    return cur.fetchall()


def _catalog_info(cur, ref_table):
    cur.execute(
        """
        SELECT d.title AS dataset_title, s.title AS source_title,
               s.url, s.license, s.attribution
          FROM catalog.datasets d
          JOIN catalog.sources s ON s.id = d.source_id
         WHERE d.ref_table = %s
         ORDER BY d.updated_at DESC
         LIMIT 1
        """,
        (ref_table,),
    )
    return cur.fetchone()


def _cite_source_line(cur, ref_table) -> str:
    info = _catalog_info(cur, ref_table)
    if info is None:
        return f"- `{ref_table}` — no catalog entry found"
    bits = [f"- `{ref_table}` — **{info['dataset_title']}** ({info['source_title']})"]
    if info["url"]:
        bits.append(f"  - URL: {info['url']}")
    if info["license"]:
        bits.append(f"  - License: {info['license']}")
    if info["attribution"]:
        bits.append(f"  - Attribution: {info['attribution']}")
    return "\n".join(bits)


def build_citation(conn, layers, fmt: str, exp_id: str) -> str:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"# Export citation — {exp_id}",
        "",
        f"- Generated: {now}",
        f"- Format: {fmt}",
        f"- Layers: {', '.join(layers)}",
        "",
    ]
    with conn.cursor() as cur:
        for layer in layers:
            lines.append(f"## {layer}")
            rows = _provenance_rows(cur, layer)
            ref_inputs = set()
            if layer.startswith("ref."):
                ref_inputs.add(layer)

            if not rows:
                lines.append("_No provenance recorded for this layer._")
            for row in rows:
                head = f"- **{row['kind']}** at {row['ts'].isoformat(timespec='seconds')}"
                if row["job_id"] is not None:
                    head += f" (job {row['job_id']})"
                lines.append(head)
                if row["sql_text"]:
                    lines.append("  Created by:")
                    lines.append("  ```sql")
                    for sql_line in row["sql_text"].splitlines():
                        lines.append(f"  {sql_line}")
                    lines.append("  ```")
                inputs = row["input_tables"] or []
                if inputs:
                    lines.append(f"  - Input tables: {', '.join(inputs)}")
                details = row["details"] or {}
                if details.get("source_url"):
                    lines.append(f"  - Source URL: {details['source_url']}")
                if details.get("external_id"):
                    lines.append(f"  - External id: {details['external_id']}")

                # Collect ref inputs; walk non-ref inputs one level deep so
                # derived layers still cite their upstream ref tables.
                for table in inputs:
                    if table.startswith("ref."):
                        ref_inputs.add(table)
                    else:
                        for upstream in _provenance_rows(cur, table):
                            for up_table in upstream["input_tables"] or []:
                                if up_table.startswith("ref."):
                                    ref_inputs.add(up_table)

            if ref_inputs:
                lines.append("")
                lines.append("### Source datasets")
                for ref_table in sorted(ref_inputs):
                    lines.append(_cite_source_line(cur, ref_table))
            lines.append("")
    return "\n".join(lines)


# ── export job ──────────────────────────────────────────────────────────────

def _unique_name(table: str, schema: str, used: set) -> str:
    name = table if table not in used else f"{schema}_{table}"
    suffix = 1
    while name in used:
        suffix += 1
        name = f"{schema}_{table}_{suffix}"
    used.add(name)
    return name


def export(conn, job) -> dict:
    """Job handler: {layers, format, cite, session_id} → MinIO artifact
    (+ optional citation sidecar) under exports/<exp_id>/."""
    payload = job["payload"]
    layers = payload.get("layers") or []
    fmt = (payload.get("format") or "gpkg").lower()
    cite = bool(payload.get("cite", True))
    session_id = job.get("session_id") or payload.get("session_id")

    if fmt not in FORMATS:
        raise ValueError(f"unsupported format: {fmt} (use gpkg|geojson|csv|parquet)")
    if not layers:
        raise ValueError("export requires a non-empty layers list")

    parsed = []
    for ref in layers:
        match = dbutil.LAYER_REF_RE.match(ref or "")
        if not match:
            raise ValueError(
                f"invalid layer ref: {ref!r} (must match ^(ref|ws_<8hex>).<table>$)"
            )
        parsed.append((ref, match.group(1), match.group(2)))

    driver, ext = FORMATS[fmt]
    exp_id = uuid.uuid4().hex
    workdir = f"/tmp/export_{exp_id}"
    os.makedirs(workdir, exist_ok=True)
    pg_dsn = dbutil.pg_ogr_dsn()

    try:
        out_files = []
        used_names = set()
        if fmt == "gpkg":
            out_path = os.path.join(workdir, "export.gpkg")
            for index, (ref, schema, table) in enumerate(parsed):
                layer_name = _unique_name(table, schema, used_names)
                args = ["ogr2ogr"]
                args += ["-f", "GPKG"] if index == 0 else ["-update"]
                args += [
                    out_path, pg_dsn,
                    "-sql", f'SELECT * FROM "{schema}"."{table}"',
                    "-nln", layer_name,
                ]
                files.run_ogr2ogr(args)
            out_files.append(out_path)
        else:
            for ref, schema, table in parsed:
                layer_name = _unique_name(table, schema, used_names)
                out_path = os.path.join(workdir, f"{layer_name}.{ext}")
                args = [
                    "ogr2ogr", "-f", driver, out_path, pg_dsn,
                    "-sql", f'SELECT * FROM "{schema}"."{table}"',
                    "-nln", layer_name,
                ]
                if fmt == "geojson":
                    args += ["-t_srs", "EPSG:4326"]
                if fmt == "csv":
                    args += ["-lco", "GEOMETRY=AS_WKT"]
                files.run_ogr2ogr(args)
                out_files.append(out_path)

        if len(out_files) == 1:
            artifact = out_files[0]
        else:
            artifact = os.path.join(workdir, "export.zip")
            with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in out_files:
                    zf.write(path, arcname=os.path.basename(path))

        filename = os.path.basename(artifact)
        object_key = f"exports/{exp_id}/{filename}"
        size_bytes = os.path.getsize(artifact)

        client = minio_client()
        ensure_bucket(client)
        client.fput_object(S3_BUCKET, object_key, artifact)
        log.info("uploaded %s (%d bytes)", object_key, size_bytes)

        sidecar_key = None
        if cite:
            citation = build_citation(conn, layers, fmt, exp_id)
            sidecar_path = os.path.join(workdir, f"{filename}.citation.md")
            with open(sidecar_path, "w", encoding="utf-8") as fh:
                fh.write(citation)
            sidecar_key = f"exports/{exp_id}/{filename}.citation.md"
            client.fput_object(S3_BUCKET, sidecar_key, sidecar_path)
            log.info("uploaded %s", sidecar_key)

        with conn.cursor() as cur:
            dbutil.insert_provenance(
                cur,
                kind="export",
                object_ref=object_key,
                session_id=session_id,
                input_tables=layers,
                job_id=job["id"],
                details={"layers": layers, "format": fmt},
            )

        return {
            "object_key": object_key,
            "sidecar_key": sidecar_key,
            "size_bytes": size_bytes,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
