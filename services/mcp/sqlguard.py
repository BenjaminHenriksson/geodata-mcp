"""SQL validation for the agent-facing read/write paths, plus EXPLAIN plan walking."""

import re

from psycopg import sql as pgsql

IDENT_RE = re.compile(r"^[a-z0-9_]{1,63}$")
WS_SCHEMA_RE = re.compile(r"^ws_[a-f0-9]{8}$")
LAYER_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
LAYER_REF_RE = re.compile(r"^(ref|ws_[a-f0-9]{8})\.([a-z0-9_]{1,63})$")

READ_STARTS = ("select", "with", "explain", "show", "table", "values")
SELECT_STARTS = ("select", "with")


def valid_schema(name: str) -> bool:
    return bool(IDENT_RE.match(name)) and (name in ("ref", "app", "catalog", "doc") or bool(WS_SCHEMA_RE.match(name)))


def ident(name: str) -> pgsql.Identifier:
    """Quote a validated identifier. Raises ValueError on anything unexpected."""
    if not IDENT_RE.match(name):
        raise ValueError(f"invalid identifier: {name!r}")
    return pgsql.Identifier(name)


def qualified(schema: str, table: str) -> pgsql.Composed:
    if not IDENT_RE.match(schema) or not IDENT_RE.match(table):
        raise ValueError(f"invalid table reference: {schema!r}.{table!r}")
    return pgsql.SQL("{}.{}").format(pgsql.Identifier(schema), pgsql.Identifier(table))


def clean_single_statement(sql_text: str) -> tuple[str | None, str | None]:
    """Strip trailing semicolon(s)/whitespace; reject anything that still contains ';'.

    Returns (cleaned_sql, error). The ';' check is deliberately strict: semicolons inside
    string literals are also rejected — use chr(59) or dollar-quoting alternatives, or
    simply avoid literal semicolons.
    """
    if not isinstance(sql_text, str) or not sql_text.strip():
        return None, "empty SQL statement"
    cleaned = sql_text.strip()
    while cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    if not cleaned:
        return None, "empty SQL statement"
    if ";" in cleaned:
        return None, (
            "multiple statements are not allowed — send exactly one statement without ';'. "
            "(Semicolons inside string literals also trigger this check; avoid them.)"
        )
    return cleaned, None


def validate_readonly(sql_text: str) -> tuple[str | None, str | None]:
    """query tool: one statement starting with SELECT/WITH/EXPLAIN/SHOW/TABLE/VALUES."""
    cleaned, err = clean_single_statement(sql_text)
    if err:
        return None, err
    first = cleaned.lstrip("(").split(None, 1)[0].lower() if cleaned.lstrip("(") else ""
    if first not in READ_STARTS:
        return None, (
            "only read statements are allowed: the statement must start with "
            "SELECT, WITH, EXPLAIN, SHOW, TABLE or VALUES. Writes go through the layer tool."
        )
    return cleaned, None


def validate_select(sql_text: str) -> tuple[str | None, str | None]:
    """layer create: one statement that is a SELECT/WITH."""
    cleaned, err = clean_single_statement(sql_text)
    if err:
        return None, err
    first = cleaned.lstrip("(").split(None, 1)[0].lower() if cleaned.lstrip("(") else ""
    if first not in SELECT_STARTS:
        return None, "the layer SQL must be a single SELECT (or WITH ... SELECT) statement"
    return cleaned, None


def _walk_plan(node, out: set) -> None:
    if isinstance(node, dict):
        rel = node.get("Relation Name")
        if rel:
            schema = node.get("Schema")
            out.add(f"{schema}.{rel}" if schema else str(rel))
        for v in node.values():
            _walk_plan(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_plan(v, out)


def explain_input_tables(conn, sql_text: str) -> list[str]:
    """EXPLAIN (VERBOSE, FORMAT JSON) as the current role; collect schema.relation refs.

    Raises the underlying psycopg error when the statement is invalid — callers turn
    that into a helpful {"error": ...} reply.
    """
    cur = conn.execute(pgsql.SQL("EXPLAIN (VERBOSE, FORMAT JSON) {}").format(pgsql.SQL(sql_text)))
    plan = cur.fetchone()[0]
    refs: set = set()
    _walk_plan(plan, refs)
    # Resolve any schema-less names via to_regclass (search_path resolution).
    resolved = set()
    for r in refs:
        if "." in r:
            resolved.add(r)
        else:
            row = conn.execute("SELECT to_regclass(%s)::text", (r,)).fetchone()
            resolved.add(row[0] if row and row[0] else r)
    return sorted(resolved)
