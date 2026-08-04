"""Implementation of the query tool: read-only SQL as agent_ro, logged to app.query_log."""

import time

from psycopg import sql as pgsql

import db
import geometry
import sqlguard

MAX_ROWS = 1000


def _log(workspace_id: str, sql_text: str, refs: list[str] | None, row_count: int | None,
         duration_ms: int | None, error: str | None) -> str:
    with db.app_pool().connection() as conn:
        row = conn.execute(
            """INSERT INTO app.query_log (workspace_id, sql_text, referenced_tables,
                                          row_count, duration_ms, error)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING query_id""",
            (workspace_id, sql_text, refs, row_count, duration_ms, error),
        ).fetchone()
        return str(row[0])


def run_query(workspace_id: str, sql_text: str, limit: int = 500) -> dict:
    cleaned, err = sqlguard.validate_readonly(sql_text)
    if err:
        qid = _log(workspace_id, str(sql_text or ""), None, None, None, err)
        return {"error": err, "query_id": qid}

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 500
    limit = max(1, min(limit, MAX_ROWS))

    first_word = cleaned.lstrip("(").split(None, 1)[0].lower()
    refs: list[str] = []
    started = time.monotonic()

    # Phase 1: EXPLAIN as agent_ro to collect referenced tables (skipped for statements
    # that cannot be nested under EXPLAIN, e.g. EXPLAIN/SHOW themselves).
    if first_word in ("select", "with", "table", "values"):
        try:
            with db.ro_pool().connection() as conn:
                refs = sqlguard.explain_input_tables(conn, cleaned)
        except Exception as e:
            msg = str(e).strip()
            duration = int((time.monotonic() - started) * 1000)
            qid = _log(workspace_id, cleaned, None, None, duration, msg)
            return {
                "error": f"SQL error: {msg}",
                "hint": (
                    "The statement failed to plan. Check table names (query "
                    "information_schema.tables or catalog.datasets.ref_table), column names, "
                    "and that geometry expressions use the 'geom' column (SRID 3014)."
                ),
                "query_id": qid,
            }

    # Phase 2: execute as agent_ro with a statement timeout.
    try:
        with db.ro_pool().connection() as conn:
            with conn.transaction():
                # Re-assert read-only per transaction rather than trusting the role default,
                # so no inherited session state can widen what `query` is allowed to do.
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute("SET LOCAL statement_timeout = '15s'")
                cur = conn.execute(pgsql.SQL("{}").format(pgsql.SQL(cleaned)))
                if cur.description is None:
                    columns, raw_rows = [], []
                else:
                    columns = [d.name for d in cur.description]
                    geom_oid = db.geometry_oid()
                    geom_flags = [d.type_code == geom_oid for d in cur.description]
                    raw_rows = cur.fetchmany(limit + 1)
    except Exception as e:
        msg = str(e).strip()
        duration = int((time.monotonic() - started) * 1000)
        qid = _log(workspace_id, cleaned, refs or None, None, duration, msg)
        return {
            "error": f"SQL error: {msg}",
            "hint": (
                "Statement ran as the read-only role (SELECT only, 15 s timeout). "
                "Simplify the query, add spatial filters (ST_DWithin), or LIMIT the result."
            ),
            "query_id": qid,
        }

    truncated = len(raw_rows) > limit
    raw_rows = raw_rows[:limit]
    rows = [
        [geometry.jsonable(v, is_geometry=geom_flags[i]) for i, v in enumerate(r)]
        for r in raw_rows
    ]
    duration = int((time.monotonic() - started) * 1000)
    qid = _log(workspace_id, cleaned, refs or None, len(rows), duration, None)
    return {
        "query_id": qid,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "referenced_tables": refs,
    }
