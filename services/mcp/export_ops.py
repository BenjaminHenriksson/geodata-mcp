"""Implementation of the export tool: enqueue an export job, wait, presign MinIO URLs."""

import datetime
from urllib.parse import urlparse

from minio import Minio

import config
import db
import sessions
import sqlguard

FORMATS = ("gpkg", "geojson", "csv", "parquet")
WAIT_S = 30.0
EXPIRES_HOURS = 24


def _minio_client() -> Minio:
    parsed = urlparse(config.S3_PUBLIC_ENDPOINT)
    endpoint = parsed.netloc or parsed.path
    # The client signs against the PUBLIC endpoint (the host is part of the v4
    # signature) which is not reachable from inside the container — pinning the
    # region skips the bucket-location network lookup, making presigning a pure
    # local computation.
    return Minio(
        endpoint,
        access_key=config.MINIO_ROOT_USER,
        secret_key=config.MINIO_ROOT_PASSWORD,
        secure=parsed.scheme == "https",
        region="us-east-1",
    )


def _presign(client: Minio, key: str) -> str:
    return client.presigned_get_object(
        config.S3_BUCKET, key, expires=datetime.timedelta(hours=EXPIRES_HOURS)
    )


def run_export(session_id: str, layers, fmt: str, cite: bool) -> dict:
    if fmt not in FORMATS:
        return {"error": f"format must be one of {', '.join(FORMATS)}"}
    if isinstance(layers, str):
        layers = [layers]
    if not isinstance(layers, list) or not layers:
        return {"error": "layers must be a non-empty list of 'schema.table' references"}

    ws = sessions.ws_schema_for(session_id)
    sessions.touch_session(session_id)
    clean: list[str] = []
    with db.app_pool().connection() as conn:
        for ref in layers:
            m = sqlguard.LAYER_REF_RE.match(str(ref))
            if not m:
                return {"error": f"invalid layer ref {ref!r} — use 'ref.<table>' or '{ws}.<table>'"}
            if m.group(1).startswith("ws_") and m.group(1) != ws:
                return {"error": f"{ref!r} is another session's workspace"}
            exists = conn.execute("SELECT to_regclass(%s)", (str(ref),)).fetchone()
            if exists is None or exists[0] is None:
                return {"error": f"table {ref!r} does not exist — check layer(op='list')"}
            clean.append(str(ref))

    payload = {"layers": clean, "format": fmt, "cite": bool(cite), "session_id": session_id}
    job_id = db.enqueue_job("export", payload, session_id)
    job = db.wait_for_job(job_id, timeout_s=WAIT_S)

    if job is None:
        return {"error": f"job {job_id} vanished — check load(op='jobs')", "job_id": job_id}
    if job["status"] == "error":
        return {"error": f"export failed: {job.get('error') or 'unknown error'}", "job_id": job_id}
    if job["status"] in ("queued", "running"):
        return {"job_id": job_id, "status": job["status"],
                "note": "export still running — poll with load(op='status', job_id=...) "
                        "and call export again once done, or wait for the result keys"}

    result = job.get("result") or {}
    object_key = result.get("object_key")
    sidecar_key = result.get("sidecar_key")
    if not object_key:
        return {"error": "export job finished without an object key", "job_id": job_id,
                "result": result}
    try:
        client = _minio_client()
        url = _presign(client, object_key)
        sidecar_url = _presign(client, sidecar_key) if sidecar_key else None
    except Exception as e:
        return {"error": f"presigning failed: {str(e).strip()}", "job_id": job_id,
                "object_key": object_key, "sidecar_key": sidecar_key}
    return {"url": url, "sidecar_url": sidecar_url, "format": fmt,
            "expires_hours": EXPIRES_HOURS, "job_id": job_id, "status": "done"}
