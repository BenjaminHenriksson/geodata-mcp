-- Workspace dashboard roles and explicit MCP audit history.
-- Apply as the database superuser; safe to re-run. No production data backfill.
SET ROLE geodata_app;
ALTER TABLE app.api_keys ADD COLUMN IF NOT EXISTS is_admin boolean NOT NULL DEFAULT false;

-- No foreign keys to mutable principals/workspaces: audit survives their deletion.
CREATE TABLE IF NOT EXISTS app.mcp_calls (
  call_id uuid PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT clock_timestamp(),
  api_key_id uuid NOT NULL,
  workspace_id text NOT NULL,
  workspace_name text NOT NULL,
  tool_name text NOT NULL,
  operation text,
  argument_keys text[] NOT NULL DEFAULT '{}',
  sql_text text
);
-- Correlate by call_id in the application. A foreign key would take a row lock
-- requiring UPDATE on the start ledger, which is intentionally revoked.
CREATE TABLE IF NOT EXISTS app.mcp_call_results (
  call_id uuid PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT clock_timestamp(),
  workspace_id text NOT NULL,
  workspace_name text NOT NULL,
  status text NOT NULL CHECK (status IN ('success', 'error')),
  duration_ms bigint NOT NULL CHECK (duration_ms >= 0),
  query_id uuid,
  job_id bigint,
  row_count bigint,
  error text
);
CREATE INDEX IF NOT EXISTS mcp_calls_workspace_idx ON app.mcp_calls(workspace_id, ts DESC);
CREATE INDEX IF NOT EXISTS mcp_calls_principal_idx ON app.mcp_calls(api_key_id, ts DESC);
CREATE INDEX IF NOT EXISTS mcp_results_workspace_idx ON app.mcp_call_results(workspace_id, ts DESC);
ALTER TABLE app.query_log ADD COLUMN IF NOT EXISTS api_key_id uuid;
ALTER TABLE app.query_log ADD COLUMN IF NOT EXISTS mcp_call_id uuid;
CREATE INDEX IF NOT EXISTS query_log_workspace_idx ON app.query_log(workspace_id, ts DESC);
CREATE INDEX IF NOT EXISTS jobs_workspace_idx ON app.jobs(workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS map_views_workspace_idx ON app.map_views(workspace_id);
CREATE INDEX IF NOT EXISTS provenance_workspace_idx ON app.provenance(workspace_id, ts DESC);
RESET ROLE;

-- Application history is append-only. Agent SQL cannot enumerate the new MCP ledger.
REVOKE ALL ON app.mcp_calls, app.mcp_call_results FROM PUBLIC, agent_ro, agent_ws;
REVOKE UPDATE, DELETE, TRUNCATE ON app.mcp_calls, app.mcp_call_results, app.query_log
  FROM PUBLIC, geodata_app, agent_ro, agent_ws;
