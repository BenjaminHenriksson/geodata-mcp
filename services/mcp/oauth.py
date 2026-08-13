"""Minimal OAuth 2.1 + PKCE authorization server — invite-code login.

Lets claude.ai / Claude Code / ChatGPT connect to /mcp with a browser login
instead of a pasted bearer key. Identity model is deliberately simple: you
authorise by typing a shared invite code (GEODATA_INVITE_CODE). Every completed
authorization mints a fresh, stable ``subject``; sessions.py provisions one
app.api_keys row per subject, so each OAuth login gets its own isolated set of
workspaces (the "per-authorization workspaces" model). Token refresh preserves
the subject, so a workspace survives the 7-day access-token lifetime.

Legacy shared bearer keys (GEODATA_API_KEYS) keep working alongside this for
scripts and CI — those are validated in sessions.py, not here.

Endpoints (all public — they sit outside the /mcp bearer check by path):

    GET  /.well-known/oauth-protected-resource       RFC 9728 pointer
    GET  /.well-known/oauth-protected-resource/mcp    RFC 9728 (path-aware clients)
    GET  /.well-known/oauth-authorization-server      RFC 8414 metadata
    POST /oauth/register                              RFC 7591 dynamic client reg
    GET  /oauth/authorize                             render invite-code form
    POST /oauth/authorize                             validate code -> auth code
    POST /oauth/token                                 exchange code -> access token

Clients and issued tokens persist to Postgres (app.oauth_clients /
app.oauth_tokens) so a restart does not force every connector to re-authorise.
Auth codes stay in-memory (single-process server; 5-min TTL, single use).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import secrets
import threading
import time
from urllib.parse import urlencode, urlparse

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

import config
import db

INVITE_CODE = config.GEODATA_INVITE_CODE
ISSUER = config.PUBLIC_BASE_URL  # e.g. https://govtech.benjaminhenriksson.com

ACCESS_TOKEN_TTL_S = 7 * 24 * 60 * 60     # 7 days
REFRESH_TOKEN_TTL_S = 30 * 24 * 60 * 60   # 30 days
AUTH_CODE_TTL_S = 5 * 60                  # 5 minutes

# Auth codes are short-lived and single-use: in-memory is fine for a single-process
# server (the tokens they mint are what persist to Postgres).
_lock = threading.Lock()
_auth_codes: dict[str, dict] = {}


def _now() -> float:
    return time.time()


def _random_token(n: int = 32) -> str:
    return secrets.token_urlsafe(n)


def _reap_auth_codes() -> None:
    now = _now()
    for k in [k for k, v in _auth_codes.items() if v.get("exp", 0) < now]:
        _auth_codes.pop(k, None)


# ---- Postgres-backed client + token store --------------------------------

def init() -> None:
    """Create the OAuth tables if missing (idempotent). Runs as geodata_app, which
    owns the app schema. db/migrations/004_oauth.sql carries the same DDL for a
    hand-applied migration; this makes a fresh deploy self-bootstrapping."""
    with db.app_pool().connection() as conn:
        with conn.transaction():
            conn.execute(
                """CREATE TABLE IF NOT EXISTS app.oauth_clients (
                       client_id     text PRIMARY KEY,
                       redirect_uris jsonb NOT NULL,
                       client_name   text NOT NULL DEFAULT '',
                       created_at    timestamptz NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS app.oauth_tokens (
                       token      text PRIMARY KEY,
                       kind       text NOT NULL CHECK (kind IN ('access','refresh')),
                       client_id  text NOT NULL
                                    REFERENCES app.oauth_clients(client_id) ON DELETE CASCADE,
                       subject    text NOT NULL,
                       expires_at timestamptz NOT NULL,
                       created_at timestamptz NOT NULL DEFAULT now()
                   )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS oauth_tokens_expires_idx "
                "ON app.oauth_tokens (expires_at)"
            )


def _db_insert_client(client_id: str, redirect_uris: list[str], client_name: str) -> None:
    import json as _json
    with db.app_pool().connection() as conn:
        conn.execute(
            "INSERT INTO app.oauth_clients (client_id, redirect_uris, client_name) "
            "VALUES (%s, %s, %s)",
            (client_id, _json.dumps(redirect_uris), client_name),
        )


def _db_get_client(client_id: str) -> dict | None:
    with db.app_pool().connection() as conn:
        row = conn.execute(
            "SELECT redirect_uris, client_name FROM app.oauth_clients WHERE client_id = %s",
            (client_id,),
        ).fetchone()
    if row is None:
        return None
    return {"redirect_uris": row[0], "client_name": row[1]}


def _db_issue_pair(client_id: str, subject: str) -> tuple[str, str]:
    """Mint + persist an access/refresh token pair for a subject. Opportunistically
    reaps expired rows so the table does not grow without bound."""
    access = "at_" + _random_token(32)
    refresh = "rt_" + _random_token(32)
    with db.app_pool().connection() as conn:
        with conn.transaction():
            conn.execute("DELETE FROM app.oauth_tokens WHERE expires_at < now()")
            conn.execute(
                "INSERT INTO app.oauth_tokens (token, kind, client_id, subject, expires_at) "
                "VALUES (%s, 'access', %s, %s, now() + make_interval(secs => %s))",
                (access, client_id, subject, ACCESS_TOKEN_TTL_S),
            )
            conn.execute(
                "INSERT INTO app.oauth_tokens (token, kind, client_id, subject, expires_at) "
                "VALUES (%s, 'refresh', %s, %s, now() + make_interval(secs => %s))",
                (refresh, client_id, subject, REFRESH_TOKEN_TTL_S),
            )
    return access, refresh


def _db_consume_refresh(refresh: str, client_id: str) -> str | None:
    """Single-use refresh: delete the row and return its subject (None if invalid or
    expired or client mismatch)."""
    with db.app_pool().connection() as conn:
        row = conn.execute(
            "DELETE FROM app.oauth_tokens "
            " WHERE token = %s AND kind = 'refresh' AND client_id = %s AND expires_at > now() "
            "RETURNING subject",
            (refresh, client_id),
        ).fetchone()
    return row[0] if row else None


def _db_access_subject(access: str) -> str | None:
    with db.app_pool().connection() as conn:
        row = conn.execute(
            "SELECT subject FROM app.oauth_tokens "
            " WHERE token = %s AND kind = 'access' AND expires_at > now()",
            (access,),
        ).fetchone()
    return row[0] if row else None


# ---- discovery metadata --------------------------------------------------

def _resource_metadata() -> dict:
    return {
        "resource": f"{ISSUER}/mcp",
        "authorization_servers": [ISSUER],
        "bearer_methods_supported": ["header"],
    }


async def protected_resource_metadata(request: Request) -> JSONResponse:
    return JSONResponse(_resource_metadata())


async def authorization_server_metadata(request: Request) -> JSONResponse:
    return JSONResponse({
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/oauth/authorize",
        "token_endpoint": f"{ISSUER}/oauth/token",
        "registration_endpoint": f"{ISSUER}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["mcp"],
    })


# ---- dynamic client registration -----------------------------------------

async def register(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_client_metadata",
                             "error_description": "body must be JSON"}, status_code=400)
    redirect_uris = body.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return JSONResponse({"error": "invalid_redirect_uri",
                             "error_description": "redirect_uris is required"},
                            status_code=400)
    client_id = "c_" + _random_token(16)
    client_name = str(body.get("client_name", "unknown"))
    uris = [str(u) for u in redirect_uris]
    await anyio.to_thread.run_sync(_db_insert_client, client_id, uris, client_name)
    return JSONResponse({
        "client_id": client_id,
        "client_id_issued_at": int(_now()),
        "redirect_uris": uris,
        "client_name": client_name,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }, status_code=201)


# ---- authorize (invite-code form) ----------------------------------------

_AUTH_FORM_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Connect to Sundsvall Geodata MCP</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <style>
    :root{--bg:#fbfaf7;--ink:#14110d;--mid:#5a5445;--faint:#918a78;
          --rule:rgba(20,17,13,.16);--hair:rgba(20,17,13,.08);--accent:#2f6d4f;
          --sans:'Inter',system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
          --mono:ui-monospace,SFMono-Regular,Menlo,monospace;}
    @media (prefers-color-scheme:dark){:root{--bg:#12130f;--ink:#eceadf;--mid:#a39c8a;
          --faint:#6b6455;--rule:rgba(255,255,255,.18);--hair:rgba(255,255,255,.09);
          --accent:#7fc79c;}}
    *,*::before,*::after{box-sizing:border-box}
    body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
         font-size:15px;line-height:1.6;min-height:100vh;display:flex;
         align-items:center;justify-content:center;padding:2rem 1rem;
         -webkit-font-smoothing:antialiased}
    .card{background:var(--bg);border:1px solid var(--rule);
          padding:2.2rem;width:100%;max-width:440px}
    .eyebrow{font-size:10px;letter-spacing:.2em;text-transform:uppercase;
             color:var(--accent);margin-bottom:.7rem;font-weight:600}
    h1{font-size:22px;font-weight:600;letter-spacing:.01em;margin:0 0 1.1rem}
    .lede{color:var(--mid);font-size:14px;margin:0 0 1.2rem}
    .lede .client{color:var(--ink);font-weight:600}
    .label{display:block;font-size:10px;letter-spacing:.16em;text-transform:uppercase;
           color:var(--faint);margin:1.4rem 0 .45rem;font-weight:600}
    .callback{display:block;border:1px solid var(--rule);padding:.55rem .7rem;
              font-family:var(--mono);font-size:13px;word-break:break-all;user-select:all}
    .warn{margin-top:.9rem;padding:.7rem 0 .7rem .8rem;border-left:2px solid var(--accent);
          color:var(--mid);font-size:12.5px;line-height:1.55}
    .warn strong{color:var(--ink);font-weight:600}
    .warn code{font-family:var(--mono);font-size:11px;color:var(--ink)}
    input[type=password]{width:100%;padding:.6rem 0;background:transparent;
          color:var(--ink);border:none;border-bottom:1px solid var(--rule);
          font-family:var(--mono);font-size:14px;letter-spacing:.04em}
    input[type=password]:focus{outline:none;border-bottom-color:var(--accent)}
    button{margin-top:1.6rem;width:100%;padding:.8rem 1rem;background:var(--accent);
           color:var(--bg);border:1px solid var(--accent);font-family:var(--sans);
           font-size:11px;font-weight:600;letter-spacing:.16em;text-transform:uppercase;
           cursor:pointer;transition:opacity .12s}
    button:hover{opacity:.85}
    .err{margin-top:1rem;padding:.2rem 0 .2rem .7rem;border-left:2px solid #c0392b;
         color:var(--ink);font-size:12.5px}
    .meta{margin-top:1.9rem;padding-top:1rem;border-top:1px solid var(--hair);
          color:var(--faint);font-size:11px;letter-spacing:.02em}
  </style>
</head>
<body>
  <form class="card" method="post" action="/oauth/authorize">
    <div class="eyebrow">Authorize</div>
    <h1>Connect to Sundsvall Geodata&nbsp;MCP</h1>
    <p class="lede"><span class="client">@@CLIENT_NAME@@</span> is requesting access
      to the geodata control plane. Verify the callback host below — that is where
      your access token will be sent.</p>
    <span class="label">Callback host</span>
    <span class="callback">@@REDIRECT_HOST@@</span>
    <div class="warn">If this is not a host you recognise (e.g.
      <code>claude.ai</code>, <code>chatgpt.com</code>, or <code>localhost</code>),
      <strong>do not continue</strong> — it could be a phishing attempt to hijack
      your session.</div>
    <label class="label" for="code">Invite code</label>
    <input type="password" id="code" name="invite_code" autocomplete="off" autofocus required>
    <button type="submit">Connect</button>
    @@ERROR@@
    @@HIDDEN@@
    <div class="meta">Sundsvall municipal geodata MCP. Access is by invite code.</div>
  </form>
</body>
</html>
"""


def _render_form(client_name: str, params: dict, error: str | None = None) -> HTMLResponse:
    hidden = "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
        for k, v in params.items()
    )
    err_html = f'<div class="err">{html.escape(error)}</div>' if error else ""
    redirect_uri = params.get("redirect_uri", "")
    try:
        parsed = urlparse(redirect_uri)
        preview = (f"{parsed.scheme}://{parsed.netloc}"
                   if parsed.scheme and parsed.netloc else redirect_uri or "(missing)")
    except Exception:
        preview = redirect_uri or "(missing)"
    page = (_AUTH_FORM_HTML
            .replace("@@CLIENT_NAME@@", html.escape(client_name or "a client"))
            .replace("@@REDIRECT_HOST@@", html.escape(preview))
            .replace("@@HIDDEN@@", hidden)
            .replace("@@ERROR@@", err_html))
    return HTMLResponse(page)


async def authorize_get(request: Request):
    params = dict(request.query_params)
    required = ["response_type", "client_id", "redirect_uri", "code_challenge"]
    missing = [k for k in required if not params.get(k)]
    if missing:
        return JSONResponse({"error": "invalid_request",
                             "error_description": f"missing: {missing}"}, status_code=400)
    if params.get("response_type") != "code":
        return JSONResponse({"error": "unsupported_response_type"}, status_code=400)
    if params.get("code_challenge_method", "S256") != "S256":
        return JSONResponse({"error": "invalid_request",
                             "error_description": "only S256 is supported"}, status_code=400)
    client = await anyio.to_thread.run_sync(_db_get_client, params["client_id"])
    if client is None:
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    if params["redirect_uri"] not in client["redirect_uris"]:
        return JSONResponse({"error": "invalid_redirect_uri"}, status_code=400)
    return _render_form(client["client_name"], params)


def _redirect_with_code(redirect_uri: str, code: str, state: str | None):
    sep = "&" if "?" in redirect_uri else "?"
    q = urlencode({"code": code, **({"state": state} if state else {})})
    return RedirectResponse(f"{redirect_uri}{sep}{q}", status_code=302)


async def authorize_post(request: Request):
    form = await request.form()
    params = {k: (v if isinstance(v, str) else v.filename) for k, v in form.items()}
    if not INVITE_CODE:
        return JSONResponse({"error": "server_error",
                             "error_description": "invite code not configured"},
                            status_code=500)
    submitted = params.get("invite_code", "")
    ok = hmac.compare_digest(submitted.strip(), INVITE_CODE)
    client_id = params.get("client_id")
    client = await anyio.to_thread.run_sync(_db_get_client, client_id) if client_id else None
    if client is None:
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    if params.get("redirect_uri") not in client["redirect_uris"]:
        return JSONResponse({"error": "invalid_redirect_uri"}, status_code=400)
    if not ok:
        keep = {k: v for k, v in params.items() if k != "invite_code"}
        return _render_form(client["client_name"], keep, error="Invalid invite code.")
    # A fresh subject per authorization -> its own workspace scope (see sessions.py).
    subject = "s_" + _random_token(18)
    code = "ac_" + _random_token(24)
    with _lock:
        _reap_auth_codes()
        _auth_codes[code] = {
            "client_id": client_id,
            "redirect_uri": params["redirect_uri"],
            "code_challenge": params["code_challenge"],
            "code_challenge_method": params.get("code_challenge_method", "S256"),
            "scope": params.get("scope", "mcp"),
            "subject": subject,
            "exp": _now() + AUTH_CODE_TTL_S,
        }
    return _redirect_with_code(params["redirect_uri"], code, params.get("state"))


# ---- token ---------------------------------------------------------------

def _verify_pkce(code_verifier: str, challenge: str) -> bool:
    if not code_verifier or not challenge:
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(expected, challenge)


async def token(request: Request) -> JSONResponse:
    form = await request.form()
    grant_type = form.get("grant_type")

    if grant_type == "authorization_code":
        code = form.get("code") or ""
        verifier = form.get("code_verifier") or ""
        redirect_uri = form.get("redirect_uri") or ""
        client_id = form.get("client_id") or ""
        with _lock:
            _reap_auth_codes()
            entry = _auth_codes.pop(code, None)
        if entry is None or entry["client_id"] != client_id \
                or entry["redirect_uri"] != redirect_uri:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if not _verify_pkce(verifier, entry["code_challenge"]):
            return JSONResponse({"error": "invalid_grant",
                                 "error_description": "PKCE verification failed"},
                                status_code=400)
        access, refresh = await anyio.to_thread.run_sync(
            _db_issue_pair, client_id, entry["subject"])
        return JSONResponse({
            "access_token": access, "token_type": "Bearer",
            "expires_in": ACCESS_TOKEN_TTL_S, "refresh_token": refresh,
            "scope": entry.get("scope") or "mcp",
        })

    if grant_type == "refresh_token":
        rt = form.get("refresh_token") or ""
        client_id = form.get("client_id") or ""
        subject = await anyio.to_thread.run_sync(_db_consume_refresh, rt, client_id)
        if subject is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        access, new_rt = await anyio.to_thread.run_sync(
            _db_issue_pair, client_id, subject)
        return JSONResponse({
            "access_token": access, "token_type": "Bearer",
            "expires_in": ACCESS_TOKEN_TTL_S, "refresh_token": new_rt, "scope": "mcp",
        })

    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)


# ---- token validation (used by sessions.py / the /mcp middleware) ---------

def subject_for_token(token_str: str | None) -> str | None:
    """The authorization subject for a valid, unexpired OAuth access token; else None.
    Called from sync identity code, so this is synchronous."""
    if not token_str or not token_str.startswith("at_"):
        return None
    return _db_access_subject(token_str)


def www_authenticate_value() -> str:
    """Value for the WWW-Authenticate header on a 401 from /mcp. The resource_metadata
    pointer is what lets an OAuth client discover this server and start the browser
    flow instead of demanding a pasted key."""
    return ('Bearer resource_metadata='
            f'"{ISSUER}/.well-known/oauth-protected-resource"')


def routes() -> list:
    return [
        Route("/.well-known/oauth-protected-resource",
              protected_resource_metadata, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource/mcp",
              protected_resource_metadata, methods=["GET"]),
        Route("/.well-known/oauth-authorization-server",
              authorization_server_metadata, methods=["GET"]),
        Route("/oauth/register", register, methods=["POST"]),
        Route("/oauth/authorize", authorize_get, methods=["GET"]),
        Route("/oauth/authorize", authorize_post, methods=["POST"]),
        Route("/oauth/token", token, methods=["POST"]),
    ]
