-- 004: OAuth 2.1 + PKCE authorization-server state (invite-code login).
--
-- The MCP server (services/mcp/oauth.py) acts as an OAuth authorization server so
-- claude.ai / Claude Code / ChatGPT can connect to /mcp with a browser login instead
-- of a pasted bearer key. Registered clients and issued tokens persist here so a
-- server restart does not force every connector to re-authorise. Each completed
-- authorization mints a stable `subject`; sessions.ensure_oauth_principal provisions
-- one app.api_keys row per subject, so every OAuth login gets its own isolated
-- workspace scope. Auth codes are short-lived and kept in-process, not here.
--
-- Apply to an existing database with:
--   docker compose exec -T postgres psql -U postgres -d geodata < db/migrations/004_oauth.sql
-- (db/init/04_tables.sql carries the same DDL for fresh installs; oauth.init() also
-- creates these idempotently at MCP startup, so this migration is belt-and-braces.)

BEGIN;
SET ROLE geodata_app;

CREATE TABLE IF NOT EXISTS app.oauth_clients (
  client_id     text PRIMARY KEY,
  redirect_uris jsonb NOT NULL,
  client_name   text NOT NULL DEFAULT '',
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.oauth_tokens (
  token      text PRIMARY KEY,
  kind       text NOT NULL CHECK (kind IN ('access','refresh')),
  client_id  text NOT NULL REFERENCES app.oauth_clients(client_id) ON DELETE CASCADE,
  subject    text NOT NULL,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS oauth_tokens_expires_idx ON app.oauth_tokens (expires_at);

RESET ROLE;
COMMIT;
