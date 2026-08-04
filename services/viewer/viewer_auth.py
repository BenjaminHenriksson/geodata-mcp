"""Cookie auth for the workspace manager UI.

Login = presenting the API key once; the server verifies its hash against
app.api_keys and sets a signed, HttpOnly cookie carrying only the api_key id
(never the key). VIEWER_SECRET signs the cookie; without it the manager UI is
disabled (the map pages themselves stay capability-URL based and need no login).
"""

import hashlib
import hmac
import os
import time

SECRET = os.environ.get("VIEWER_SECRET", "")
COOKIE_NAME = "gdw_auth"
COOKIE_TTL_S = 7 * 24 * 3600


def enabled() -> bool:
    return bool(SECRET)


def _sig(payload: str) -> str:
    return hmac.new(SECRET.encode("utf-8"), payload.encode("utf-8"),
                    hashlib.sha256).hexdigest()[:32]


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_cookie(api_key_id: str) -> str:
    payload = f"{api_key_id}.{int(time.time()) + COOKIE_TTL_S}"
    return f"{payload}.{_sig(payload)}"


def parse_cookie(value) -> str | None:
    """api_key_id from a valid, unexpired cookie; None otherwise."""
    if not enabled() or not value or not isinstance(value, str):
        return None
    parts = value.rsplit(".", 1)
    if len(parts) != 2 or not hmac.compare_digest(_sig(parts[0]), parts[1]):
        return None
    fields = parts[0].rsplit(".", 1)
    if len(fields) != 2:
        return None
    api_key_id, exp = fields
    try:
        if int(exp) < time.time():
            return None
    except ValueError:
        return None
    return api_key_id


def csrf_token(api_key_id: str) -> str:
    """Per-principal CSRF token for the manager forms (stable across the session)."""
    return _sig("csrf." + api_key_id)


def csrf_ok(api_key_id: str, token) -> bool:
    return isinstance(token, str) and hmac.compare_digest(csrf_token(api_key_id), token)
