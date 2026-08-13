"""API-key identity and durable workspace resolution.

Auth model: every /mcp request carries `Authorization: Bearer <key>` (enforced by the
transport middleware in server.py). Only the key's SHA-256 digest is ever stored, so a
leaked app.api_keys row is not a replayable credential.

Workspaces are durable rows in app.workspaces owned by an API key; exactly one is
'active' per key and receives all reads/writes. This replaces the old keying on the
per-connection mcp-session-id header, which lost the workspace on every reconnect.
"""

import hashlib
import re
import uuid as uuidlib
from dataclasses import dataclass

from psycopg import sql as pgsql
from psycopg.errors import UniqueViolation

import db
import oauth

AUTH_HEADER = "authorization"
WORKSPACE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
DEFAULT_WORKSPACE = "default"
MAX_WORKSPACES_PER_KEY = 20


class AuthError(Exception):
    """Raised when a tool call cannot be attributed to a valid API key."""


@dataclass
class Workspace:
    id: str          # app.workspaces.id (uuid, text form) — the attribution id
    name: str
    ws_schema: str   # ws_<8 hex>
    api_key_id: str
    created: bool = False  # True only when this call created the row (see workspace op='new')


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def bearer_token(headers) -> str | None:
    """Extract the bearer token from a headers mapping (case-insensitive get)."""
    try:
        value = headers.get(AUTH_HEADER) or headers.get("Authorization")
    except Exception:
        return None
    if not value or not isinstance(value, str):
        return None
    parts = value.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        return None
    return parts[1].strip()


def key_hash_from_context(ctx) -> str:
    """SHA-256 of the caller's bearer key, read from the MCP request context.

    The middleware has already rejected unauthenticated requests, so a missing header
    here means the context plumbing broke — surface it as an AuthError, not a KeyError.
    """
    raw = None
    try:
        rc = getattr(ctx, "request_context", None)
        req = getattr(rc, "request", None)
        headers = getattr(req, "headers", None)
        if headers is not None:
            raw = bearer_token(headers)
    except Exception:
        pass
    if not raw:
        raise AuthError("missing Authorization: Bearer <api key> header")
    return hash_key(raw)


def key_id_for_hash(key_hash: str) -> str | None:
    """api_keys.id for an enabled key digest, bumping last_used; None if unknown."""
    with db.app_pool().connection() as conn:
        row = conn.execute(
            """UPDATE app.api_keys SET last_used = now()
                WHERE key_hash = %s AND NOT disabled
               RETURNING id::text""",
            (key_hash,),
        ).fetchone()
        return row[0] if row else None


def raw_bearer_from_context(ctx) -> str | None:
    """The raw bearer token string from the MCP request context, or None."""
    try:
        rc = getattr(ctx, "request_context", None)
        req = getattr(rc, "request", None)
        headers = getattr(req, "headers", None)
        if headers is not None:
            return bearer_token(headers)
    except Exception:
        pass
    return None


def ensure_oauth_principal(subject: str) -> str | None:
    """api_keys.id for an OAuth authorization subject, provisioning the row on first
    use. Each subject is a distinct principal, so each OAuth login owns its own
    workspaces. The digest is a namespaced SHA-256 of the subject (never a bearer that
    could be replayed), so it satisfies the api_keys.key_hash CHECK. Returns None if
    the row exists but was manually disabled."""
    kh = hash_key("oauth-sub:" + subject)
    with db.app_pool().connection() as conn:
        conn.execute(
            """INSERT INTO app.api_keys (key_hash, name) VALUES (%s, %s)
               ON CONFLICT (key_hash) DO NOTHING""",
            (kh, "oauth:" + subject[:34]),
        )
    return key_id_for_hash(kh)


def principal_id_from_raw(raw: str | None) -> str | None:
    """Resolve a raw bearer to an app.api_keys.id, accepting either a legacy shared
    key or an OAuth access token. This is the single identity path shared by the
    transport middleware (auth gate) and resolve() (workspace attribution), so both
    agree on who the caller is."""
    if not raw:
        return None
    kid = key_id_for_hash(hash_key(raw))
    if kid is not None:
        return kid
    subject = oauth.subject_for_token(raw)
    if subject is not None:
        return ensure_oauth_principal(subject)
    return None


def bootstrap_env_keys(raw_keys: list[str]) -> None:
    """Reconcile app.api_keys with GEODATA_API_KEYS: env is the source of truth.

    Keys present in the env are inserted if new; keys that have disappeared from the env
    are disabled. Deliberately NOT `ON CONFLICT DO UPDATE SET disabled = false` — that
    would resurrect a key an operator had revoked in the database on the next restart,
    which turns `restart: unless-stopped` into a credential-rollback mechanism. Removing
    a key from .env and restarting is therefore a real revocation, as the README claims.
    Rows are disabled rather than deleted so their history stays attributable.
    """
    hashes = [hash_key(raw) for raw in raw_keys]
    with db.app_pool().connection() as conn:
        with conn.transaction():
            for i, kh in enumerate(hashes):
                conn.execute(
                    """INSERT INTO app.api_keys (key_hash, name) VALUES (%s, %s)
                       ON CONFLICT (key_hash) DO NOTHING""",
                    (kh, f"env:{i + 1}"),
                )
            conn.execute(
                # Only env-managed keys are reconciled here. OAuth-provisioned
                # principals (name 'oauth:%', created lazily by ensure_oauth_principal)
                # must survive restarts — without the name filter, every restart would
                # disable them and orphan their workspaces.
                "UPDATE app.api_keys SET disabled = true "
                " WHERE NOT disabled AND name LIKE 'env:%%' AND NOT (key_hash = ANY(%s))",
                (hashes,),
            )


def _new_ws_schema() -> str:
    return "ws_" + uuidlib.uuid4().hex[:8]


def _lock_key(conn, api_key_id: str) -> None:
    """Serialize workspace bookkeeping for one API key.

    Two concurrent activations would otherwise both clear the old flag and both set
    theirs, tripping workspaces_one_active_idx and surfacing a raw duplicate-key error.
    An advisory lock (not SELECT … FOR UPDATE) because the rows being contended may not
    exist yet — the same race creates the 'default' workspace twice.
    """
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (api_key_id,))


def _activate(conn, api_key_id: str, workspace_id: str) -> None:
    """Make one workspace the key's active one. Caller must hold _lock_key."""
    conn.execute(
        "UPDATE app.workspaces SET is_active = false WHERE api_key_id = %s AND is_active",
        (api_key_id,),
    )
    conn.execute(
        "UPDATE app.workspaces SET is_active = true, last_used = now() WHERE id = %s",
        (workspace_id,),
    )


def get_or_create_workspace(api_key_id: str, name: str, activate: bool = False) -> Workspace:
    """Fetch or create a named workspace for a key. The schema itself stays lazy
    (ensure_ws_schema) — creating a workspace row costs nothing until first write."""
    if not WORKSPACE_NAME_RE.match(name):
        raise ValueError("workspace name must match ^[a-z0-9][a-z0-9_-]{0,39}$")
    created = False
    with db.app_pool().connection() as conn:
        with conn.transaction():
            # Serialized per key, so the count check below cannot be raced past and two
            # connections cannot both create the same name.
            _lock_key(conn, api_key_id)
            row = conn.execute(
                """SELECT id::text, name, ws_schema FROM app.workspaces
                    WHERE api_key_id = %s AND name = %s""",
                (api_key_id, name),
            ).fetchone()
            if row is None:
                count = conn.execute(
                    "SELECT count(*) FROM app.workspaces WHERE api_key_id = %s",
                    (api_key_id,),
                ).fetchone()[0]
                if int(count) >= MAX_WORKSPACES_PER_KEY:
                    raise ValueError(
                        f"workspace limit reached ({MAX_WORKSPACES_PER_KEY} per key) — "
                        "delete one with workspace(op='delete') first"
                    )
                for _ in range(4):  # ws_schema is 8 hex chars; retry the rare collision
                    try:
                        with conn.transaction():
                            row = conn.execute(
                                """INSERT INTO app.workspaces (api_key_id, name, ws_schema)
                                   VALUES (%s, %s, %s) RETURNING id::text, name, ws_schema""",
                                (api_key_id, name, _new_ws_schema()),
                            ).fetchone()
                        created = True
                        break
                    except UniqueViolation:
                        # ws_schema collision across keys — retry with a fresh suffix.
                        row = None
                if row is None:
                    raise RuntimeError("could not allocate a workspace schema name")
            if activate:
                _activate(conn, api_key_id, row[0])
    return Workspace(id=row[0], name=row[1], ws_schema=row[2], api_key_id=api_key_id,
                     created=created)


def resolve(ctx) -> Workspace:
    """The caller's active workspace (creating/activating 'default' when none is).

    Called at the top of every tool; also bumps last_used on key and workspace.
    """
    raw = raw_bearer_from_context(ctx)
    if not raw:
        raise AuthError("missing Authorization: Bearer <api key> header")
    api_key_id = principal_id_from_raw(raw)
    if api_key_id is None:
        raise AuthError("unknown or disabled API key")
    with db.app_pool().connection() as conn:
        row = conn.execute(
            """UPDATE app.workspaces SET last_used = now()
                WHERE api_key_id = %s AND is_active
               RETURNING id::text, name, ws_schema""",
            (api_key_id,),
        ).fetchone()
        if row is None:
            # No active workspace — but a concurrent op='use' may simply be mid-flight,
            # having cleared the old flag before setting the new one. Re-check under the
            # per-key lock so we do not stomp that switch by activating 'default'.
            with conn.transaction():
                _lock_key(conn, api_key_id)
                row = conn.execute(
                    """UPDATE app.workspaces SET last_used = now()
                        WHERE api_key_id = %s AND is_active
                       RETURNING id::text, name, ws_schema""",
                    (api_key_id,),
                ).fetchone()
    if row is not None:
        return Workspace(id=row[0], name=row[1], ws_schema=row[2], api_key_id=api_key_id)
    return get_or_create_workspace(api_key_id, DEFAULT_WORKSPACE, activate=True)


def workspace_by_id(workspace_id: str) -> Workspace | None:
    with db.app_pool().connection() as conn:
        row = conn.execute(
            """SELECT id::text, name, ws_schema, api_key_id::text
                 FROM app.workspaces WHERE id = %s""",
            (workspace_id,),
        ).fetchone()
    if row is None:
        return None
    return Workspace(id=row[0], name=row[1], ws_schema=row[2], api_key_id=row[3])


def ws_schema_for(workspace_id: str) -> str:
    """Workspace schema for a workspace id; raises when the workspace is gone
    (deleted from another connection mid-call)."""
    w = workspace_by_id(workspace_id)
    if w is None:
        raise AuthError("workspace no longer exists — it was deleted; call any tool again "
                        "to land in your default workspace")
    return w.ws_schema


def ensure_ws_schema(workspace_id: str) -> str:
    """Create the workspace schema (idempotent) and grant agent roles. Runs as geodata_app.

    Takes the per-key lock and re-reads the workspace row inside the same transaction as
    the CREATE SCHEMA: without that, a delete landing between the check and the create
    would leave a schema no workspace owns, which the worker's sweep silently drops —
    after the layer tool has already reported success.
    """
    w = workspace_by_id(workspace_id)
    if w is None:
        raise AuthError("workspace no longer exists — it was deleted; call any tool again "
                        "to land in your default workspace")
    ws = w.ws_schema
    ws_ident = pgsql.Identifier(ws)
    with db.app_pool().connection() as conn:
        with conn.transaction():
            _lock_key(conn, w.api_key_id)
            still = conn.execute("SELECT 1 FROM app.workspaces WHERE id = %s",
                                 (workspace_id,)).fetchone()
            if still is None:
                raise AuthError("workspace was deleted while this call was running — "
                                "call any tool again to land in your default workspace")
            conn.execute(
                pgsql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION geodata_app").format(
                    ws_ident)
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
