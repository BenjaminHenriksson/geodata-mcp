# Access and security

`/mcp` requires a configured bearer API key or a valid OAuth token. API key
digests are stored in `app.api_keys`; raw configured keys remain in the
service environment. Setting `GEODATA_INVITE_CODE` enables OAuth browser login
with authorization code and PKCE. An invite code is not a bearer credential.

The workspace manager uses an HttpOnly signed cookie and checks ownership and
CSRF tokens for mutations. Set a private `VIEWER_SECRET` to enable it.

Map and data URLs are capabilities: anyone who can reach the service and knows
the view URL can view its referenced layers. Keep the reverse proxy on the
intended network, and use TLS for remote access.

## Database boundaries

- `query` runs as `agent_ro` in a read-only transaction with a statement timeout.
  Pooled connections are reset before reuse.
- Layer mutations are restricted to the caller's active workspace. Other keys'
  workspace bookkeeping and API key hashes cannot be queried by agent roles.
- Workspace tables share a read role: authenticated users can read another
  workspace's tables if they know the names. Workspaces do not provide tenant
  read isolation.
- Map updates and workspace-manager mutations check ownership. Map URLs remain
  shareable.
- Query logs, provenance and DDL event triggers record operations; provenance
  is also included in export citation sidecars.

Every HTML page uses a nonce-based Content Security Policy. The MapLibre page
allows evaluation needed by its renderer; the manager and Origo pages do not.
The WMS proxy adds upstream credentials server-side.

## Credentials and reporting

Keep `.env` and real credentials out of Git. Database passwords in the Compose
environment initialize roles only on first startup; rotate existing roles with
`ALTER ROLE`. Disabling a key in `app.api_keys` takes effect after the MCP
credential cache expires (up to 30 seconds).

Report vulnerabilities privately through the repository's GitHub security
reporting facility when available. Do not include live keys or personal data
in public issues. Run [`scripts/security_test.py`](scripts/security_test.py)
against a test stack with two distinct API keys to check these boundaries.
