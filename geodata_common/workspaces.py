"""Workspace ownership and mutations, independent of HTTP/MCP presentation."""

import re
import uuid

from psycopg import sql

WORKSPACE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
WS_SCHEMA_RE = re.compile(r"^ws_[a-f0-9]{8}$")


def lock_key(conn, api_key_id):
    """Serialize workspace bookkeeping, including creation, for one principal."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (api_key_id,))


def owned(conn, api_key_id, *, workspace_id=None, name=None):
    if workspace_id is not None:
        try:
            value = str(uuid.UUID(str(workspace_id)))
        except (ValueError, TypeError, AttributeError):
            return None
        field = "id"
    else:
        field, value = "name", name
    return conn.execute(
        f"""SELECT id::text, name, ws_schema, is_active FROM app.workspaces
             WHERE api_key_id = %s AND {field} = %s""", (api_key_id, value)
    ).fetchone()


def activate(conn, api_key_id, workspace_id):
    with conn.transaction():
        lock_key(conn, api_key_id)
        if owned(conn, api_key_id, workspace_id=workspace_id) is None:
            return False
        conn.execute(
            "UPDATE app.workspaces SET is_active = false WHERE api_key_id = %s AND is_active",
            (api_key_id,))
        conn.execute(
            "UPDATE app.workspaces SET is_active = true, last_used = now() WHERE id = %s",
            (workspace_id,))
    return True


def rename(conn, api_key_id, workspace_id, new_name, *, allow_same=True):
    """Return an error string or None. Serialize the uniqueness check and update."""
    if not WORKSPACE_NAME_RE.match(new_name or ""):
        return "name must match ^[a-z0-9][a-z0-9_-]{0,39}$"
    with conn.transaction():
        lock_key(conn, api_key_id)
        if owned(conn, api_key_id, workspace_id=workspace_id) is None:
            return "unknown workspace"
        clash = owned(conn, api_key_id, name=new_name)
        if clash and (not allow_same or clash[0] != str(workspace_id)):
            return f"a workspace named {new_name!r} already exists"
        conn.execute("UPDATE app.workspaces SET name = %s WHERE id = %s",
                     (new_name, workspace_id))
    return None


def delete(conn, api_key_id, workspace_id, ws_schema):
    """Drop owned schema and bookkeeping atomically; retain append-only history."""
    if not WS_SCHEMA_RE.match(ws_schema):
        raise ValueError(f"suspicious schema name {ws_schema!r}")
    with conn.transaction():
        lock_key(conn, api_key_id)
        row = owned(conn, api_key_id, workspace_id=workspace_id)
        if row is None:
            return 0
        if row[2] != ws_schema:
            raise ValueError("workspace schema does not match its owner")
        conn.execute("SELECT set_config('app.workspace_id', %s, true)", (workspace_id,))
        conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(ws_schema)))
        conn.execute("DELETE FROM app.layer_meta WHERE schema_name = %s", (ws_schema,))
        views = conn.execute(
            "DELETE FROM app.map_views WHERE workspace_id = %s RETURNING view_id",
            (workspace_id,)).fetchall()
        conn.execute("DELETE FROM app.workspaces WHERE id = %s", (workspace_id,))
    return len(views)


def layer_counts(conn, schemas):
    if not schemas:
        return {}
    rows = conn.execute(
        """SELECT n.nspname, count(*) FROM pg_class c
             JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r' AND n.nspname = ANY(%s)
            GROUP BY n.nspname""", (schemas,)).fetchall()
    return {name: int(count) for name, count in rows}
