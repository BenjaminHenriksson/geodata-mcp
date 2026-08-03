"""Implementation of the layer tool — the ONLY write path; every op records provenance
and runs its writes as agent_ws inside a transaction stamped with app.session_id."""

from psycopg import sql as pgsql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

import db
import geometry
import provenance
import sessions
import sqlguard


def _stamp(conn, session_id: str) -> None:
    conn.execute("SELECT set_config('app.session_id', %s, true)", (session_id,))


def _table_columns(conn, schema: str, table: str) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """SELECT column_name AS name, data_type, udt_name
                 FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position""",
            (schema, table),
        )
        return cur.fetchall()


def _geom_columns(cols: list[dict]) -> list[str]:
    return [c["name"] for c in cols if c["udt_name"] == "geometry"]


def _table_exists(conn, schema: str, table: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s)", (f"{schema}.{table}",)).fetchone()
    return row is not None and row[0] is not None


def _upsert_meta(schema: str, table: str, style=None, notes=None, popup=None,
                 label=None, visible=None) -> None:
    with db.app_pool().connection() as conn:
        conn.execute(
            """INSERT INTO app.layer_meta (schema_name, table_name, style, notes, popup, label, visible)
               VALUES (%s, %s, COALESCE(%s, '{}'::jsonb), COALESCE(%s, ''),
                       COALESCE(%s, '{}'::text[]), COALESCE(%s, ''), COALESCE(%s, true))
               ON CONFLICT (schema_name, table_name) DO UPDATE SET
                 style   = CASE WHEN %s THEN EXCLUDED.style   ELSE app.layer_meta.style   END,
                 notes   = CASE WHEN %s THEN EXCLUDED.notes   ELSE app.layer_meta.notes   END,
                 popup   = CASE WHEN %s THEN EXCLUDED.popup   ELSE app.layer_meta.popup   END,
                 label   = CASE WHEN %s THEN EXCLUDED.label   ELSE app.layer_meta.label   END,
                 visible = CASE WHEN %s THEN EXCLUDED.visible ELSE app.layer_meta.visible END""",
            (schema, table,
             Jsonb(style) if style is not None else None,
             notes, popup, label, visible,
             style is not None, notes is not None, popup is not None, label is not None,
             visible is not None),
        )


def create(session_id: str, name: str, sql_text: str, notes: str = "",
           style: dict | None = None) -> dict:
    if not name or not sqlguard.LAYER_NAME_RE.match(name):
        return {"error": "layer name must match ^[a-z][a-z0-9_]{0,59}$"}
    cleaned, err = sqlguard.validate_select(sql_text)
    if err:
        return {"error": err}

    # Validate + extract input tables as the read-only role.
    try:
        with db.ro_pool().connection() as conn:
            input_tables = sqlguard.explain_input_tables(conn, cleaned)
    except Exception as e:
        return {"error": f"SQL error: {str(e).strip()}",
                "hint": "the SELECT must run as the read-only role first; check tables/columns"}

    ws = sessions.ensure_ws_schema(session_id)
    table = sqlguard.qualified(ws, name)
    try:
        with db.ws_pool().connection() as conn:
            with conn.transaction():
                _stamp(conn, session_id)
                if _table_exists(conn, ws, name):
                    return {"error": f"table {ws}.{name} already exists — drop it or pick another name"}
                conn.execute(pgsql.SQL("CREATE TABLE {} AS {}").format(table, pgsql.SQL(cleaned)))
                conn.execute(pgsql.SQL("GRANT SELECT ON {} TO agent_ro").format(table))
                row_count = conn.execute(
                    pgsql.SQL("SELECT count(*) FROM {}").format(table)).fetchone()[0]
                cols = _table_columns(conn, ws, name)
                for g in _geom_columns(cols):
                    conn.execute(pgsql.SQL("CREATE INDEX ON {} USING gist ({})").format(
                        table, pgsql.Identifier(g)))
    except Exception as e:
        return {"error": f"layer create failed: {str(e).strip()}",
                "hint": "geometry expressions must produce typed geometry (e.g. ST_Buffer(geom, 100)); "
                        "cast ambiguous columns explicitly"}

    _upsert_meta(ws, name, style=style, notes=notes or None)
    provenance.add("layer_create", session_id, object_ref=f"{ws}.{name}",
                   sql_text=cleaned, input_tables=input_tables,
                   details={"notes": notes or "", "row_count": int(row_count)})
    return {"table": f"{ws}.{name}", "row_count": int(row_count),
            "columns": [{"name": c["name"], "type": c["data_type"]} for c in cols]}


def update(session_id: str, name: str, key_column: str, values: dict) -> dict:
    if not name or not sqlguard.LAYER_NAME_RE.match(name):
        return {"error": "layer name must match ^[a-z][a-z0-9_]{0,59}$"}
    if not key_column or not sqlguard.IDENT_RE.match(key_column):
        return {"error": "key_column must be a lowercase identifier"}
    if not isinstance(values, dict) or not values or \
            not all(isinstance(v, dict) for v in values.values()):
        return {"error": "values must be {key: {column: value, ...}, ...} with at least one key"}

    ws = sessions.ws_schema_for(session_id)
    sessions.touch_session(session_id)
    table = sqlguard.qualified(ws, name)

    data_cols: list[str] = []
    for patch in values.values():
        for c in patch:
            if c not in data_cols:
                data_cols.append(c)
    for c in data_cols:
        if not sqlguard.IDENT_RE.match(c):
            return {"error": f"column name {c!r} invalid — use lowercase [a-z0-9_]"}
    if key_column in data_cols:
        return {"error": "values may not modify the key_column itself"}

    try:
        with db.ws_pool().connection() as conn:
            with conn.transaction():
                _stamp(conn, session_id)
                if not _table_exists(conn, ws, name):
                    return {"error": f"no table {ws}.{name} — create it with layer op='create' first"}
                existing = {c["name"]: c for c in _table_columns(conn, ws, name)}
                if key_column not in existing:
                    return {"error": f"key_column {key_column!r} does not exist on {ws}.{name}"}
                added: list[str] = []
                col_types: dict[str, str] = {}
                for c in data_cols:
                    if c in existing:
                        udt = str(existing[c]["udt_name"])
                        # Cast to the column's own type; arrays/exotics fall back to text.
                        if sqlguard.IDENT_RE.match(udt) and not udt.startswith("_"):
                            col_types[c] = udt
                        else:
                            col_types[c] = "text"
                    else:
                        t = geometry.infer_pg_type([p.get(c) for p in values.values() if c in p])
                        col_types[c] = t
                        conn.execute(pgsql.SQL("ALTER TABLE {} ADD COLUMN {} {}").format(
                            table, pgsql.Identifier(c), pgsql.SQL(t)))
                        added.append(c)

                # UPDATE .. FROM (VALUES ..): per data column a value slot + a 'was it set' flag,
                # so partial patches leave other columns untouched.
                value_rows, params = [], []
                slots_per_row = 1 + 2 * len(data_cols)
                row_template = "(" + ", ".join(["%s"] * slots_per_row) + ")"
                for key, patch in values.items():
                    params.append(str(key))
                    for c in data_cols:
                        present = c in patch
                        params.append(geometry.coerce_for_type(patch.get(c), col_types[c])
                                      if present else None)
                        params.append(present)
                    value_rows.append(row_template)

                v_cols = [pgsql.Identifier("k")]
                set_parts = []
                for c in data_cols:
                    v_cols.append(pgsql.Identifier(c))
                    v_cols.append(pgsql.Identifier(c + "__set"))
                    set_parts.append(pgsql.SQL("{col} = CASE WHEN v.{flag} THEN v.{col}::{typ} ELSE t.{col} END").format(
                        col=pgsql.Identifier(c), flag=pgsql.Identifier(c + "__set"),
                        typ=pgsql.SQL(col_types[c])))
                update_sql = pgsql.SQL(
                    "UPDATE {table} AS t SET {sets} FROM (VALUES {rows}) AS v ({vcols}) "
                    "WHERE t.{key}::text = v.k"
                ).format(table=table, sets=pgsql.SQL(", ").join(set_parts),
                         rows=pgsql.SQL(", ".join(value_rows)),
                         vcols=pgsql.SQL(", ").join(v_cols),
                         key=pgsql.Identifier(key_column))
                cur = conn.execute(update_sql, params)
                updated = cur.rowcount
                sql_for_ledger = update_sql.as_string(conn)
    except Exception as e:
        return {"error": f"layer update failed: {str(e).strip()}",
                "hint": "values are matched on key_column cast to text; check keys and value types"}

    provenance.add("layer_update", session_id, object_ref=f"{ws}.{name}",
                   sql_text=sql_for_ledger,
                   details={"values": values, "key_column": key_column, "columns_added": added,
                            "rows_matched": updated})
    return {"table": f"{ws}.{name}", "rows_updated": updated, "columns_added": added,
            "keys_given": len(values)}


def style(session_id: str, name: str, style: dict | None = None, popup: list | None = None,
          label: str | None = None, visible: bool | None = None, notes: str | None = None) -> dict:
    if not name or not sqlguard.LAYER_NAME_RE.match(name):
        return {"error": "layer name must match ^[a-z][a-z0-9_]{0,59}$"}
    ws = sessions.ws_schema_for(session_id)
    sessions.touch_session(session_id)
    _upsert_meta(ws, name, style=style, notes=notes, popup=popup, label=label, visible=visible)
    return {"table": f"{ws}.{name}", "style": style, "popup": popup, "label": label,
            "visible": visible}


def rename(session_id: str, name: str, new_name: str) -> dict:
    for n in (name, new_name):
        if not n or not sqlguard.LAYER_NAME_RE.match(n):
            return {"error": "names must match ^[a-z][a-z0-9_]{0,59}$"}
    ws = sessions.ws_schema_for(session_id)
    sessions.touch_session(session_id)
    try:
        with db.ws_pool().connection() as conn:
            with conn.transaction():
                _stamp(conn, session_id)
                if not _table_exists(conn, ws, name):
                    return {"error": f"no table {ws}.{name}"}
                if _table_exists(conn, ws, new_name):
                    return {"error": f"table {ws}.{new_name} already exists"}
                conn.execute(pgsql.SQL("ALTER TABLE {} RENAME TO {}").format(
                    sqlguard.qualified(ws, name), pgsql.Identifier(new_name)))
    except Exception as e:
        return {"error": f"rename failed: {str(e).strip()}"}
    with db.app_pool().connection() as conn:
        conn.execute(
            "UPDATE app.layer_meta SET table_name = %s WHERE schema_name = %s AND table_name = %s",
            (new_name, ws, name))
    provenance.add("layer_rename", session_id, object_ref=f"{ws}.{new_name}",
                   details={"from": f"{ws}.{name}", "to": f"{ws}.{new_name}"})
    return {"table": f"{ws}.{new_name}", "renamed_from": f"{ws}.{name}"}


def drop(session_id: str, name: str) -> dict:
    if not name or not sqlguard.LAYER_NAME_RE.match(name):
        return {"error": "layer name must match ^[a-z][a-z0-9_]{0,59}$"}
    ws = sessions.ws_schema_for(session_id)
    sessions.touch_session(session_id)
    try:
        with db.ws_pool().connection() as conn:
            with conn.transaction():
                _stamp(conn, session_id)
                if not _table_exists(conn, ws, name):
                    return {"error": f"no table {ws}.{name}"}
                conn.execute(pgsql.SQL("DROP TABLE {}").format(sqlguard.qualified(ws, name)))
    except Exception as e:
        return {"error": f"drop failed: {str(e).strip()}"}
    with db.app_pool().connection() as conn:
        conn.execute("DELETE FROM app.layer_meta WHERE schema_name = %s AND table_name = %s",
                     (ws, name))
    provenance.add("layer_drop", session_id, object_ref=f"{ws}.{name}")
    return {"dropped": f"{ws}.{name}"}


def list_layers(session_id: str) -> dict:
    ws = sessions.ws_schema_for(session_id)
    sessions.touch_session(session_id)
    out = {"workspace_schema": ws, "workspace_layers": [], "ref_layers": []}
    with db.app_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT n.nspname AS schema, c.relname AS table,
                          c.reltuples::bigint AS row_estimate,
                          m.style, m.notes, m.popup, m.label, m.visible
                     FROM pg_class c
                     JOIN pg_namespace n ON n.oid = c.relnamespace
                     LEFT JOIN app.layer_meta m
                       ON m.schema_name = n.nspname AND m.table_name = c.relname
                    WHERE c.relkind = 'r' AND n.nspname IN ('ref', %s)
                    ORDER BY n.nspname, c.relname""",
                (ws,),
            )
            for r in cur.fetchall():
                entry = geometry.jsonable_row(r)
                if r["schema"] == ws:
                    exact = conn.execute(pgsql.SQL("SELECT count(*) FROM {}").format(
                        sqlguard.qualified(r["schema"], r["table"]))).fetchone()[0]
                    entry["row_count"] = int(exact)
                    entry.pop("row_estimate", None)
                    out["workspace_layers"].append(entry)
                else:
                    entry["row_count_estimate"] = max(0, int(r["row_estimate"]))
                    entry.pop("row_estimate", None)
                    out["ref_layers"].append(entry)
    return out
