"""The job loop: poll app.jobs every 2 s with FOR UPDATE SKIP LOCKED, run the
handler for the claimed job, and record done/error (one retry: attempts < 2
and failed → back to queued). One bad job never kills the loop."""

import json
import logging
import os
import re
import time

from psycopg import sql
from psycopg.types.json import Json

import dbutil
import embedder
import exporter
from connectors import files, pdf, wfs

log = logging.getLogger("worker.jobs")

POLL_INTERVAL_S = 2.0
# Idle time after which a session's workspace schema is dropped, and how often to look.
WORKSPACE_TTL_HOURS = float(os.environ.get("WORKSPACE_TTL_HOURS", "72"))
SWEEP_INTERVAL_S = 3600.0
WS_SCHEMA_RE = re.compile(r"^ws_[a-f0-9]{8}$")
MAX_ATTEMPTS = 2

HANDLERS = {
    "harvest_wfs": wfs.harvest_wfs,
    "harvest_wms": wfs.harvest_wms,
    "ingest_wfs": wfs.ingest_wfs,
    "ingest_file": files.ingest_file,
    "ingest_pdf": pdf.ingest_pdf,
    "embed_catalog": embedder.embed_catalog,
    "export": exporter.export,
}


def _update_job(conn, query: str, params):
    """Run a job-state update, reconnecting once if the connection is broken.
    Returns the (possibly new) connection."""
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
        conn.commit()
        return conn
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        fresh = dbutil.connect()
        with fresh.cursor() as cur:
            cur.execute(query, params)
        fresh.commit()
        return fresh


def _poll_once() -> bool:
    """Claim and run at most one job. Returns True if a job was processed."""
    conn = dbutil.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM app.jobs
                 WHERE status = 'queued'
                 ORDER BY created_at
                 LIMIT 1
                 FOR UPDATE SKIP LOCKED
                """
            )
            job = cur.fetchone()
            if job is None:
                conn.rollback()
                return False
            cur.execute(
                """
                UPDATE app.jobs
                   SET status = 'running', attempts = attempts + 1, started_at = now()
                 WHERE id = %s
                """,
                (job["id"],),
            )
        conn.commit()

        attempts = job["attempts"] + 1
        job["attempts"] = attempts
        log.info("job %s %s running (attempt %d)", job["id"], job["kind"], attempts)

        try:
            handler = HANDLERS.get(job["kind"])
            if handler is None:
                raise RuntimeError(f"unknown job kind: {job['kind']}")
            result = handler(conn, job)
            conn.commit()
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = dbutil.connect()
            error = f"{type(exc).__name__}: {exc}"[:4000]
            if attempts < MAX_ATTEMPTS:
                conn = _update_job(
                    conn,
                    "UPDATE app.jobs SET status = 'queued', error = %s WHERE id = %s",
                    (error, job["id"]),
                )
                log.warning(
                    "job %s %s failed (attempt %d), requeued: %s",
                    job["id"], job["kind"], attempts, error,
                )
            else:
                conn = _update_job(
                    conn,
                    "UPDATE app.jobs SET status = 'error', error = %s, "
                    "finished_at = now() WHERE id = %s",
                    (error, job["id"]),
                )
                log.error("job %s %s error: %s", job["id"], job["kind"], error)
            return True

        conn = _update_job(
            conn,
            "UPDATE app.jobs SET status = 'done', result = %s, error = NULL, "
            "finished_at = now() WHERE id = %s",
            (Json(result), job["id"]),
        )
        log.info(
            "job %s %s done: %s",
            job["id"], job["kind"], json.dumps(result, default=str)[:500],
        )
        return True
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _requeue_orphans() -> None:
    """Recover jobs left 'running' by a worker that died mid-job.

    The status update commits before the handler runs (the row lock cannot be held for a
    15-minute ogr2ogr), so a crash or restart would otherwise strand the job forever.
    Safe because compose runs a single worker: nothing else can be legitimately running.
    Before scaling to multiple worker replicas this must become lease-based (a worker id
    plus heartbeat on app.jobs), or a restarting replica will requeue its peers' in-flight
    jobs — see README "What's left".
    """
    try:
        with dbutil.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE app.jobs
                       SET status      = CASE WHEN attempts < 2 THEN 'queued' ELSE 'error' END,
                           error       = coalesce(error, 'worker restarted mid-job'),
                           finished_at = CASE WHEN attempts >= 2 THEN now() END
                     WHERE status = 'running'
                    RETURNING id, status
                    """
                )
                rows = cur.fetchall()
            conn.commit()
        if rows:
            log.warning("recovered %d orphaned job(s): %s", len(rows),
                        ", ".join(f"{r['id']}→{r['status']}" for r in rows))
    except Exception:
        log.exception("orphan-job sweep failed (continuing)")


def sweep_workspaces() -> None:
    """Drop workspace schemas whose session has been idle past the TTL.

    The schema-per-session tenancy model assumes this reaper exists: without it schema
    count grows monotonically, which is the one cost the architecture calls out for
    choosing schemas over row-level security. A workspace is only dropped once its
    session row is older than WORKSPACE_TTL_HOURS with no activity; the drop is
    witnessed by the sql_drop event trigger, so it lands in app.provenance.
    """
    try:
        with dbutil.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT session_id, ws_schema FROM app.sessions
                     WHERE last_seen < now() - make_interval(hours => %s)
                    """,
                    (WORKSPACE_TTL_HOURS,),
                )
                stale = cur.fetchall()
                for row in stale:
                    ws = row["ws_schema"]
                    if not WS_SCHEMA_RE.match(ws):
                        log.error("refusing to drop suspicious schema name %r", ws)
                        continue
                    cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(ws)))
                    cur.execute("DELETE FROM app.sessions WHERE session_id = %s",
                                (row["session_id"],))
            conn.commit()
        if stale:
            log.info("workspace TTL sweep dropped %d idle workspace(s): %s",
                     len(stale), ", ".join(r["ws_schema"] for r in stale))
    except Exception:
        log.exception("workspace TTL sweep failed (continuing)")


def run_forever() -> None:
    """Entry point for the background thread started from main.py."""
    exporter.startup_ensure_bucket()
    _requeue_orphans()
    log.info("job loop started (poll every %.0f s, workspace TTL %s h)",
             POLL_INTERVAL_S, WORKSPACE_TTL_HOURS)
    next_sweep = 0.0
    while True:
        now = time.monotonic()
        if now >= next_sweep:
            sweep_workspaces()
            next_sweep = now + SWEEP_INTERVAL_S
        try:
            worked = _poll_once()
        except Exception:
            log.exception("job loop iteration failed")
            worked = False
        if not worked:
            time.sleep(POLL_INTERVAL_S)
