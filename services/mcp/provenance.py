"""Append-only provenance ledger writes (via the APP connection)."""

from psycopg.types.json import Jsonb

import db


def add(
    kind: str,
    session_id: str,
    object_ref: str | None = None,
    sql_text: str | None = None,
    input_tables: list[str] | None = None,
    job_id: int | None = None,
    details: dict | None = None,
) -> int:
    with db.app_pool().connection() as conn:
        row = conn.execute(
            """INSERT INTO app.provenance
                   (session_id, kind, object_ref, sql_text, input_tables, job_id, details)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                session_id,
                kind,
                object_ref,
                sql_text,
                input_tables,
                job_id,
                Jsonb(details or {}),
            ),
        ).fetchone()
        return int(row[0])


def for_object(object_ref: str, limit: int = 50) -> list[dict]:
    from psycopg.rows import dict_row

    with db.app_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT id, ts, session_id, kind, object_ref, sql_text, input_tables,
                          job_id, details
                     FROM app.provenance
                    WHERE object_ref = %s
                    ORDER BY ts DESC LIMIT %s""",
                (object_ref, limit),
            )
            return cur.fetchall()
