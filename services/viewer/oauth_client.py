"""OAuth 2.1 login for the workspace-manager UI, alongside API-key login.

The MCP service (services/mcp/oauth.py) already runs an OAuth 2.1 + PKCE
authorization server with an invite-code login. This module makes the viewer a
*client* of that same server: a human clicks "Logga in med OAuth", authorises on
the MCP invite-code form, and is redirected back here. We then resolve the
authorization to the very same app.api_keys principal the MCP would derive
(a namespaced SHA-256 of the OAuth subject — see
services/mcp/sessions.ensure_oauth_principal) and issue the normal viewer session
cookie. Both login paths therefore land on the same api_key_id and see the same
workspaces.

Topology notes:
- The browser is sent to {PUBLIC_BASE_URL}/oauth/authorize — the same public
  origin; Caddy routes /oauth/* to the MCP service.
- The code->token exchange is a server-to-server POST to MCP_INTERNAL_URL
  (default http://mcp:8000), because the public origin is generally not
  resolvable from inside the viewer container (e.g. http://localhost:8080).
- Auth codes live only in the MCP process's memory, so the viewer must do the
  standard token exchange; it cannot shortcut via the shared database.
- PKCE (S256) plus a signed, short-lived state cookie protect the callback.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode

import httpx

import viewer_auth  # reuse VIEWER_SECRET for signing the state cookie

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")
MCP_INTERNAL_URL = os.environ.get("MCP_INTERNAL_URL", "http://mcp:8000").rstrip("/")
INVITE_CODE = os.environ.get("GEODATA_INVITE_CODE", "")  # presence => OAuth offered

CLIENT_ID = "c_viewer"
CLIENT_NAME = "Geodata arbetsytehanterare"
REDIRECT_PATH = "/auth/callback"
REDIRECT_URI = f"{PUBLIC_BASE_URL}{REDIRECT_PATH}"
SCOPE = "mcp"

STATE_COOKIE = "gdw_oauth"
STATE_TTL_S = 600  # 10 minutes to complete the round trip


def enabled() -> bool:
    """OAuth login is offered only when both the manager cookie secret and the MCP
    invite code are configured (the MCP authorize form requires the invite code)."""
    return bool(viewer_auth.SECRET) and bool(INVITE_CODE)


# ---- client registration (idempotent; the viewer shares the app DB) --------

def ensure_client(conn) -> None:
    """Ensure this viewer is a registered OAuth client of the MCP server, with our
    callback as an allowed redirect_uri. Written straight to app.oauth_clients (owned
    by geodata_app, which the viewer connects as) rather than via the HTTP
    registration endpoint. Idempotent; keeps redirect_uris in sync if PUBLIC_BASE_URL
    changes."""
    conn.execute(
        """INSERT INTO app.oauth_clients (client_id, redirect_uris, client_name)
                VALUES (%s, %s, %s)
           ON CONFLICT (client_id)
                DO UPDATE SET redirect_uris = EXCLUDED.redirect_uris""",
        (CLIENT_ID, json.dumps([REDIRECT_URI]), CLIENT_NAME),
    )


# ---- PKCE + signed state cookie -------------------------------------------

def _challenge(verifier: str) -> str:
    """S256 code challenge — byte-for-byte what services/mcp/oauth._verify_pkce checks."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# Domain separation: prefix the signed content with a scheme label so a valid state
# cookie can never also validate as a session cookie (viewer_auth._sig), and vice
# versa, even though both derive from VIEWER_SECRET with the same HMAC construction.
_STATE_DOMAIN = "gdw-oauth-state.v1:"


def _sign(payload: str) -> str:
    return hmac.new(viewer_auth.SECRET.encode("utf-8"),
                    (_STATE_DOMAIN + payload).encode("utf-8"),
                    hashlib.sha256).hexdigest()[:32]


def begin() -> tuple[str, str]:
    """Start an authorization. Returns (authorize_url, state_cookie_value). The state
    cookie carries the CSRF state and the PKCE verifier, signed with VIEWER_SECRET and
    time-boxed. token_urlsafe output never contains '.', so the dotted payload splits
    cleanly."""
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(24)
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
        "state": state,
        "scope": SCOPE,
    }
    authorize_url = f"{PUBLIC_BASE_URL}/oauth/authorize?{urlencode(params)}"
    payload = f"{state}.{verifier}.{int(time.time()) + STATE_TTL_S}"
    return authorize_url, f"{payload}.{_sign(payload)}"


def parse_state(cookie_value, state_param) -> str | None:
    """Return the PKCE verifier iff the signed cookie is valid and unexpired and its
    embedded state matches the ?state= handed to the callback; else None."""
    if not viewer_auth.SECRET or not cookie_value or not isinstance(cookie_value, str):
        return None
    parts = cookie_value.rsplit(".", 1)
    # Compare as bytes: hmac.compare_digest raises TypeError on non-ASCII str inputs,
    # and both the cookie value and the state query param are attacker-controlled.
    if len(parts) != 2 or not hmac.compare_digest(
            _sign(parts[0]).encode("ascii"), parts[1].encode("utf-8")):
        return None
    fields = parts[0].split(".")
    if len(fields) != 3:
        return None
    state, verifier, exp = fields
    try:
        if int(exp) < time.time():
            return None
    except (TypeError, ValueError):
        return None
    if not state_param or not hmac.compare_digest(
            state.encode("utf-8"), str(state_param).encode("utf-8")):
        return None
    return verifier


# ---- token exchange + principal resolution --------------------------------

def exchange(code: str, verifier: str) -> str | None:
    """Exchange an authorization code for an access token at the MCP token endpoint
    (server-to-server). Returns the access token, or None on any failure."""
    try:
        r = httpx.post(
            f"{MCP_INTERNAL_URL}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
            },
            timeout=10.0,
        )
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        tok = r.json().get("access_token")
    except (ValueError, AttributeError):
        return None
    return tok if isinstance(tok, str) and tok.startswith("at_") else None


def principal_id_for_access(conn, access_token: str) -> str | None:
    """Resolve an OAuth access token to the app.api_keys.id it maps to, provisioning
    that principal row on first use — the same identity the MCP server derives in
    sessions.ensure_oauth_principal, so both agree on who the caller is. Returns None
    if the token is unknown/expired or the principal row exists but was disabled."""
    row = conn.execute(
        "SELECT subject FROM app.oauth_tokens "
        " WHERE token = %s AND kind = 'access' AND expires_at > now()",
        (access_token,),
    ).fetchone()
    if row is None:
        return None
    subject = row[0]
    key_hash = hashlib.sha256(("oauth-sub:" + subject).encode("utf-8")).hexdigest()
    conn.execute(
        "INSERT INTO app.api_keys (key_hash, name) VALUES (%s, %s) "
        "ON CONFLICT (key_hash) DO NOTHING",
        (key_hash, "oauth:" + subject[:34]),
    )
    got = conn.execute(
        "SELECT id::text FROM app.api_keys WHERE key_hash = %s AND NOT disabled",
        (key_hash,),
    ).fetchone()
    return got[0] if got else None
