"""Implementation of the workspace tool: durable session management for an API key.

All ops operate strictly on workspaces owned by the calling key; other keys'
workspaces are invisible here (reads of their *tables* remain possible through the
shared agent_ro role — the documented namespacing-not-tenancy boundary).
"""

from psycopg import sql as pgsql
from psycopg.rows import dict_row

import db
import sessions


def _owned(conn, api_key_id: str, name: str):
    return conn.execute(
        """SELECT id::text, name, ws_schema, is_active FROM app.workspaces
            WHERE api_key_id = %s AND name = %s""",
        (api_key_id, name),
    ).fetchone()


def _layer_counts(conn, schemas: list[str]) -> dict[str, int]:
    if not schemas:
        return {}
    rows = conn.execute(
        """SELECT n.nspname, count(*) FROM pg_class c
             JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r' AND n.nspname = ANY(%s)
            GROUP BY n.nspname""",
        (schemas,),
    ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def list_workspaces(api_key_id: str) -> dict:
    with db.app_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT w.id::text AS id, w.name, w.ws_schema, w.is_active,
                          w.created_at::text, w.last_used::text,
                          (SELECT count(*) FROM app.map_views v
                            WHERE v.workspace_id = w.id::text) AS map_count
                     FROM app.workspaces w
                    WHERE w.api_key_id = %s
                    ORDER BY w.last_used DESC""",
                (api_key_id,),
            )
            rows = cur.fetchall()
        counts = _layer_counts(conn, [r["ws_schema"] for r in rows])
    for r in rows:
        r["layer_count"] = counts.get(r["ws_schema"], 0)
        r["map_count"] = int(r["map_count"])
    return {"workspaces": rows,
            "note": "the active workspace receives all layer/map writes; "
                    "switch with workspace(op='use', name=...)"}


def create(api_key_id: str, name: str) -> dict:
    try:
        w = sessions.get_or_create_workspace(api_key_id, name, activate=True)
    except ValueError as e:
        return {"error": str(e)}
    out = {"workspace": w.name, "ws_schema": w.ws_schema, "active": True,
           "created": w.created}
    out["note"] = ("new workspaces start empty; previous layers live in their own workspaces"
                   if w.created else
                   f"a workspace named {name!r} already existed — switched to it with its "
                   "layers intact rather than creating a second one")
    return out


def use(api_key_id: str, name: str) -> dict:
    with db.app_pool().connection() as conn:
        with conn.transaction():
            sessions._lock_key(conn, api_key_id)
            row = _owned(conn, api_key_id, name)
            if row is None:
                return {"error": f"no workspace named {name!r} — see workspace(op='list')"}
            sessions._activate(conn, api_key_id, row[0])
    return {"workspace": name, "ws_schema": row[2], "active": True}


def rename(api_key_id: str, name: str, new_name: str) -> dict:
    if not sessions.WORKSPACE_NAME_RE.match(new_name or ""):
        return {"error": "new_name must match ^[a-z0-9][a-z0-9_-]{0,39}$"}
    with db.app_pool().connection() as conn:
        with conn.transaction():
            row = _owned(conn, api_key_id, name)
            if row is None:
                return {"error": f"no workspace named {name!r}"}
            if _owned(conn, api_key_id, new_name) is not None:
                return {"error": f"a workspace named {new_name!r} already exists"}
            conn.execute("UPDATE app.workspaces SET name = %s WHERE id = %s",
                         (new_name, row[0]))
    return {"workspace": new_name, "renamed_from": name, "ws_schema": row[2],
            "note": "only the label changed; the schema and its tables are untouched"}


def delete(api_key_id: str, name: str) -> dict:
    """Drop the workspace schema (CASCADE) and its bookkeeping. The sql_drop event
    trigger witnesses the DROP, so the deletion itself lands in provenance.

    Its map views go too: their layers are being deleted, so leaving the capability URLs
    alive would serve maps that render empty and can never be updated again (the owner
    check compares workspace_id, and the new workspace gets a new uuid). Provenance and
    query_log rows are append-only history and stay.
    """
    with db.app_pool().connection() as conn:
        with conn.transaction():
            sessions._lock_key(conn, api_key_id)
            row = _owned(conn, api_key_id, name)
            if row is None:
                return {"error": f"no workspace named {name!r}"}
            ws_id, _, ws_schema, was_active = row[0], row[1], row[2], row[3]
            conn.execute("SELECT set_config('app.workspace_id', %s, true)", (ws_id,))
            conn.execute(pgsql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                pgsql.Identifier(ws_schema)))
            conn.execute("DELETE FROM app.layer_meta WHERE schema_name = %s", (ws_schema,))
            views = conn.execute(
                "DELETE FROM app.map_views WHERE workspace_id = %s RETURNING view_id",
                (ws_id,)).fetchall()
            conn.execute("DELETE FROM app.workspaces WHERE id = %s", (ws_id,))
    out = {"deleted": name, "ws_schema": ws_schema, "map_views_deleted": len(views)}
    if was_active:
        out["note"] = ("that was the active workspace — the next tool call lands in "
                       "'default' (created if needed)")
    return out


def current(workspace: sessions.Workspace) -> dict:
    with db.app_pool().connection() as conn:
        counts = _layer_counts(conn, [workspace.ws_schema])
        tables = conn.execute(
            """SELECT c.relname FROM pg_class c
                 JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'r' AND n.nspname = %s
                ORDER BY c.relname""",
            (workspace.ws_schema,),
        ).fetchall()
    return {"workspace": workspace.name,
            "ws_schema": workspace.ws_schema,
            "layer_count": counts.get(workspace.ws_schema, 0),
            "layers": [t[0] for t in tables],
            "note": "durable: reconnecting with the same API key returns here"}
