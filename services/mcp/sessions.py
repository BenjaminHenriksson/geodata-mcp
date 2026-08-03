"""Session identity and lazy workspace-schema creation."""

import hashlib

from psycopg import sql as pgsql

import db

SESSION_HEADER = "mcp-session-id"


def session_id_from_context(ctx) -> str:
    """Stable per-session key derived from the MCP session id.

    The raw mcp-session-id header is a bearer token: anyone who learns it can act as
    that session. So it is hashed here and only the digest is ever stored or returned —
    a row leaked out of app.sessions / app.provenance is then useless as a credential.
    """
    raw = "default"
    try:
        rc = getattr(ctx, "request_context", None)
        req = getattr(rc, "request", None)
        headers = getattr(req, "headers", None)
        if headers is not None:
            sid = headers.get(SESSION_HEADER)
            if sid:
                raw = str(sid)
    except Exception:
        pass
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ws_schema_for(session_key: str) -> str:
    """Workspace schema for a hashed session key (see session_id_from_context)."""
    return "ws_" + session_key[:8]


def touch_session(session_id: str) -> str:
    """Upsert app.sessions (bumping last_seen) and return the ws schema name.

    Does NOT create the schema — that happens lazily on first write (ensure_ws_schema).
    """
    ws = ws_schema_for(session_id)
    with db.app_pool().connection() as conn:
        conn.execute(
            """INSERT INTO app.sessions (session_id, ws_schema) VALUES (%s, %s)
               ON CONFLICT (session_id) DO UPDATE SET last_seen = now()""",
            (session_id, ws),
        )
    return ws


def ensure_ws_schema(session_id: str) -> str:
    """Create the session workspace schema (idempotent) and grant agent roles. Runs as geodata_app."""
    ws = touch_session(session_id)
    ws_ident = pgsql.Identifier(ws)
    with db.app_pool().connection() as conn:
        conn.execute(
            pgsql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION geodata_app").format(ws_ident)
        )
        conn.execute(
            pgsql.SQL("GRANT USAGE, CREATE ON SCHEMA {} TO agent_ws").format(ws_ident)
        )
        conn.execute(pgsql.SQL("GRANT USAGE ON SCHEMA {} TO agent_ro").format(ws_ident))
        # geodata_app is a member of agent_ws, so it may set default privileges for it:
        # future agent_ws tables in this schema become readable by agent_ro automatically
        # (the layer tool additionally grants per-table, belt and braces).
        conn.execute(
            pgsql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE agent_ws IN SCHEMA {} "
                "GRANT SELECT ON TABLES TO agent_ro"
            ).format(ws_ident)
        )
    return ws
