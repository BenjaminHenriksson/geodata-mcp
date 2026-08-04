"""Shared database helpers for the ingestion worker.

All schema/table identifiers that end up interpolated into SQL are validated
here and quoted with psycopg.sql.Identifier; all values travel as bind params.
"""

import os
import re
import urllib.parse

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Json

DATABASE_URL_APP = os.environ.get(
    "DATABASE_URL_APP", "postgresql://geodata_app:geodata_app@postgres:5432/geodata"
)

IDENT_RE = re.compile(r"^[a-z0-9_]{1,63}$")
WS_SCHEMA_RE = re.compile(r"^ws_[a-f0-9]{8}$")
LAYER_REF_RE = re.compile(r"^(ref|ws_[a-f0-9]{8})\.([a-z0-9_]{1,63})$")


def connect() -> psycopg.Connection:
    """New connection as the app role, dict rows, explicit transactions."""
    return psycopg.connect(DATABASE_URL_APP, row_factory=dict_row, autocommit=False)


def check_table_name(name: str) -> str:
    if not isinstance(name, str) or not IDENT_RE.match(name):
        raise ValueError(f"invalid table name: {name!r} (must match ^[a-z0-9_]{{1,63}}$)")
    return name


def check_schema_name(name: str) -> str:
    if not isinstance(name, str) or not IDENT_RE.match(name):
        raise ValueError(f"invalid schema name: {name!r} (must match ^[a-z0-9_]{{1,63}}$)")
    if name.startswith("ws_") and not WS_SCHEMA_RE.match(name):
        raise ValueError(f"invalid workspace schema: {name!r} (must match ^ws_[a-f0-9]{{8}}$)")
    return name


def qualify(schema: str, table: str) -> sql.Identifier:
    """Safe `"schema"."table"` composed identifier (validates both parts)."""
    check_schema_name(schema)
    check_table_name(table)
    return sql.Identifier(schema, table)


def pg_ogr_dsn() -> str:
    """GDAL/OGR PG datasource string derived from DATABASE_URL_APP."""
    u = urllib.parse.urlsplit(DATABASE_URL_APP)
    return "PG:host={host} port={port} dbname={db} user={user} password={pw}".format(
        host=u.hostname or "postgres",
        port=u.port or 5432,
        db=(u.path or "/geodata").lstrip("/"),
        user=urllib.parse.unquote(u.username or "geodata_app"),
        pw=urllib.parse.unquote(u.password or ""),
    )


def redact(text: str) -> str:
    """Mask password=... in command lines before logging / persisting."""
    return re.sub(r"password=\S+", "password=***", text)


def table_schema_summary(cur, schema: str, table: str) -> dict:
    """{"fields":[{"name","type"}...], "geometry_type": ...} from catalogs."""
    cur.execute(
        """
        SELECT column_name, data_type, udt_name
          FROM information_schema.columns
         WHERE table_schema = %s AND table_name = %s
         ORDER BY ordinal_position
        """,
        (schema, table),
    )
    fields = []
    for row in cur.fetchall():
        if row["udt_name"] == "geometry":
            continue
        col_type = row["udt_name"] if row["data_type"] == "USER-DEFINED" else row["data_type"]
        fields.append({"name": row["column_name"], "type": col_type})
    cur.execute(
        """
        SELECT type FROM public.geometry_columns
         WHERE f_table_schema = %s AND f_table_name = %s
         ORDER BY (f_geometry_column = 'geom') DESC
         LIMIT 1
        """,
        (schema, table),
    )
    row = cur.fetchone()
    return {"fields": fields, "geometry_type": row["type"] if row else None}


def grant_select(cur, schema: str, table: str) -> None:
    cur.execute(
        sql.SQL("GRANT SELECT ON {} TO agent_ro, agent_ws").format(qualify(schema, table))
    )


def analyze(cur, schema: str, table: str) -> None:
    cur.execute(sql.SQL("ANALYZE {}").format(qualify(schema, table)))


def count_rows(cur, schema: str, table: str) -> int:
    cur.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(qualify(schema, table)))
    return int(cur.fetchone()["n"])


def insert_provenance(
    cur,
    *,
    kind: str,
    object_ref: str,
    workspace_id=None,
    sql_text=None,
    input_tables=None,
    job_id=None,
    details=None,
) -> None:
    cur.execute(
        """
        INSERT INTO app.provenance (workspace_id, kind, object_ref, sql_text,
                                    input_tables, job_id, details)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            workspace_id,
            kind,
            object_ref,
            sql_text,
            list(input_tables or []),
            job_id,
            Json(details or {}),
        ),
    )


def vector_literal(vec) -> str:
    """pgvector input literal: '[0.1,0.2,...]' (bind as text, cast ::vector)."""
    return "[" + ",".join(str(float(x)) for x in vec) + "]"
