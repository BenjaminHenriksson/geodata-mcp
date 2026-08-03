"""Connection pools (app / ro / ws) and small DB helpers."""

import threading
import time

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

import config

_pools: dict = {}
_lock = threading.Lock()


def _reset_connection(conn) -> None:
    """Scrub session state before a connection goes back into the pool.

    Without this, anything a statement changed at session scope (GUCs, temp tables)
    leaks into the next tool call that borrows the same connection — including
    `SET default_transaction_read_only = off`, which would quietly disarm the query
    tool's read-only guarantee for every later caller.

    Deliberately not `DISCARD ALL`: that also runs DEALLOCATE ALL, which desyncs
    psycopg's client-side prepared-statement cache and makes later calls fail with
    "prepared statement _pg3_0 does not exist". Prepared statements carry no privilege,
    so keeping them is safe. These commands cannot run inside a transaction block,
    hence the autocommit dance.
    """
    conn.rollback()
    prev = conn.autocommit
    conn.autocommit = True
    try:
        conn.execute("RESET ALL")
        conn.execute("CLOSE ALL")
        conn.execute("UNLISTEN *")
        conn.execute("DISCARD TEMP")
    finally:
        conn.autocommit = prev


def _pool(name: str, conninfo: str) -> ConnectionPool:
    with _lock:
        p = _pools.get(name)
        if p is None:
            p = ConnectionPool(
                conninfo,
                min_size=0,
                max_size=8,
                open=True,
                name=name,
                kwargs={"autocommit": False},
                check=ConnectionPool.check_connection,
                reset=_reset_connection,
            )
            _pools[name] = p
        return p


def app_pool() -> ConnectionPool:
    """Control plane: sessions, jobs, provenance, layer_meta, map_views, catalog reads."""
    return _pool("app", config.DATABASE_URL_APP)


def ro_pool() -> ConnectionPool:
    """Agent read path: the query tool and EXPLAIN validation."""
    return _pool("ro", config.DATABASE_URL_RO)


def ws_pool() -> ConnectionPool:
    """Agent write path: only the layer tool's CTAS/UPDATE/DROP/RENAME and inline loads."""
    return _pool("ws", config.DATABASE_URL_WS)


_geometry_oid: int | None = None


def geometry_oid() -> int:
    """The OID of the PostGIS 'geometry' type, cached after first lookup (RO connection)."""
    global _geometry_oid
    if _geometry_oid is None:
        with ro_pool().connection() as conn:
            row = conn.execute(
                "SELECT oid FROM pg_type WHERE typname = 'geometry' LIMIT 1"
            ).fetchone()
            _geometry_oid = int(row[0]) if row else -1
    return _geometry_oid


# ── jobs ─────────────────────────────────────────────────────────────────────

def enqueue_job(kind: str, payload: dict, session_id: str) -> int:
    with app_pool().connection() as conn:
        row = conn.execute(
            "INSERT INTO app.jobs (kind, payload, session_id) VALUES (%s, %s, %s) RETURNING id",
            (kind, Jsonb(payload), session_id),
        ).fetchone()
        return int(row[0])


def get_job(job_id: int) -> dict | None:
    with app_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT id, kind, payload, status, result, error, session_id, attempts,
                          created_at, started_at, finished_at
                     FROM app.jobs WHERE id = %s""",
                (job_id,),
            )
            return cur.fetchone()


def wait_for_job(job_id: int, timeout_s: float, poll_s: float = 0.5) -> dict | None:
    """Poll a job until it is done/error or the timeout elapses; return the latest row."""
    deadline = time.monotonic() + timeout_s
    job = get_job(job_id)
    while job is not None and job["status"] in ("queued", "running"):
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_s)
        job = get_job(job_id)
    return job


def recent_jobs(limit: int = 20) -> list[dict]:
    with app_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT id, kind, status, error, session_id, attempts,
                          created_at, started_at, finished_at
                     FROM app.jobs ORDER BY id DESC LIMIT %s""",
                (limit,),
            )
            return cur.fetchall()
