"""Shared response handling for queued ingestion and analysis jobs."""

import db
import geometry


def submit(kind: str, payload: dict, workspace_id: str, pending_note: str) -> dict:
    job_id = db.enqueue_job(kind, payload, workspace_id)
    job = db.wait_for_job(job_id, timeout_s=8.0)
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
    return geometry.jsonable_row(reply)
