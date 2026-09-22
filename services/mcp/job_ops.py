"""Shared response handling for queued ingestion and analysis jobs."""

import math

import db
import geometry


def enqueue(kind: str, payload: dict, workspace_id: str, timeout_s: float = 0):
    """One submission path; callers choose whether to wait for a quick result."""
    job_id = db.enqueue_job(kind, payload, workspace_id)
    job = db.wait_for_job(job_id, timeout_s=timeout_s) if timeout_s > 0 else None
    return job_id, job


def lookup(job_id, timeout_s=0, *, workspace_id=None, kind=None):
    """Read an existing job without submitting work; return (job, error)."""
    try:
        jid = int(job_id)
    except (TypeError, ValueError):
        return None, {"error": "job_id must be an integer"}
    try:
        wait = float(timeout_s or 0)
        if not math.isfinite(wait):
            raise ValueError
        wait = min(max(wait, 0.0), 25.0)
    except (TypeError, ValueError):
        return None, {"error": "timeout_s must be a finite number"}
    job = db.get_job(jid)
    if job is None or (workspace_id is not None and job.get("workspace_id") != workspace_id):
        return None, {"error": f"no job with id {job_id}"}
    if kind is not None and job["kind"] != kind:
        return None, {"error": f"job {job_id} is not an {kind} job"}
    if wait > 0 and job["status"] in ("queued", "running"):
        job = db.wait_for_job(jid, timeout_s=wait)
        if job is None or (workspace_id is not None and job.get("workspace_id") != workspace_id):
            return None, {"error": f"no job with id {job_id}"}
    return job, None


def status(job_id, timeout_s=0, *, workspace_id=None):
    job, error = lookup(job_id, timeout_s, workspace_id=workspace_id)
    return error if error else geometry.jsonable_row(job, truncate=False)


def cancel(job_id, *, workspace_id=None):
    job, error = lookup(job_id, workspace_id=workspace_id)
    if error:
        return error
    jid = int(job_id)
    with db.app_pool().connection() as conn:
        row = conn.execute(
            """UPDATE app.jobs SET status = 'cancelled', error = 'cancelled before start',
                      finished_at = now()
                WHERE id = %s AND status = 'queued'
                  AND (%s::text IS NULL OR workspace_id = %s)
                RETURNING id""", (jid, workspace_id, workspace_id)).fetchone()
        if row:
            return {"job_id": jid, "status": "cancelled"}
    latest, error = lookup(jid, workspace_id=workspace_id)
    if error:
        return error
    if latest["status"] == "running":
        return {"error": f"job {jid} is already running — cooperative interruption is "
                         "not implemented; it will finish or error on its own"}
    return {"job_id": jid, "status": latest["status"],
            "note": "only queued jobs can be cancelled; this one already finished"}


def submit(kind: str, payload: dict, workspace_id: str, pending_note: str) -> dict:
    job_id, job = enqueue(kind, payload, workspace_id, timeout_s=8.0)
    reply = {
        "job_id": job_id,
        "kind": kind,
        "status": job["status"] if job else "queued",
    }
    if job:
        for key in ("result", "error"):
            if job.get(key):
                reply[key] = job[key]
    if reply["status"] in ("queued", "running"):
        reply["note"] = pending_note
    return geometry.jsonable_row(reply, truncate=False)
