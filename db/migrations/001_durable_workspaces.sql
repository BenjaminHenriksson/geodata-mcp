-- Migration 001: durable, API-key-owned workspaces.
--
-- Replaces the per-connection session model (app.sessions keyed on a hashed
-- mcp-session-id) with app.api_keys + app.workspaces, and renames the
-- session_id attribution columns to workspace_id (the stored value changes
-- meaning: it is now a workspace uuid, not a session hash).
--
-- Idempotent: safe to run more than once. Run as the postgres superuser
-- (the event-trigger functions are SECURITY DEFINER owned by postgres):
--   docker compose exec -T postgres psql -U postgres -d geodata < db/migrations/001_durable_workspaces.sql
--
-- Existing ws_* schemas that belonged to old transient sessions are NOT dropped
-- here; the worker's orphan sweep removes any ws_* schema with no app.workspaces
-- row. Historical provenance/query_log rows keep their old hash values — they
-- remain valid audit history, just written before this migration.

BEGIN;

SET ROLE geodata_app;

CREATE TABLE IF NOT EXISTS app.api_keys (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  key_hash   text UNIQUE NOT NULL CHECK (key_hash ~ '^[a-f0-9]{64}$'),
  name       text NOT NULL DEFAULT '',
  disabled   boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  last_used  timestamptz
);

CREATE TABLE IF NOT EXISTS app.workspaces (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  api_key_id uuid NOT NULL REFERENCES app.api_keys(id) ON DELETE CASCADE,
  name       text NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9_-]{0,39}$'),
  ws_schema  text UNIQUE NOT NULL CHECK (ws_schema ~ '^ws_[a-f0-9]{8}$'),
  is_active  boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  last_used  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (api_key_id, name)
);

CREATE UNIQUE INDEX IF NOT EXISTS workspaces_one_active_idx
  ON app.workspaces (api_key_id) WHERE is_active;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_schema = 'app' AND table_name = 'jobs' AND column_name = 'session_id') THEN
    ALTER TABLE app.jobs RENAME COLUMN session_id TO workspace_id;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_schema = 'app' AND table_name = 'map_views' AND column_name = 'session_id') THEN
    ALTER TABLE app.map_views RENAME COLUMN session_id TO workspace_id;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_schema = 'app' AND table_name = 'provenance' AND column_name = 'session_id') THEN
    ALTER TABLE app.provenance RENAME COLUMN session_id TO workspace_id;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_schema = 'app' AND table_name = 'query_log' AND column_name = 'session_id') THEN
    ALTER TABLE app.query_log RENAME COLUMN session_id TO workspace_id;
  END IF;
END $$;

DROP TABLE IF EXISTS app.sessions;

RESET ROLE;

-- Event-trigger backstop now attributes DDL to app.workspace_id (set by the layer
-- and workspace tools inside their transactions). CREATE OR REPLACE keeps the
-- postgres ownership + SECURITY DEFINER of the originals.

CREATE OR REPLACE FUNCTION app.provenance_ddl_end() RETURNS event_trigger
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
    IF r.schema_name IS NOT NULL AND (r.schema_name LIKE 'ws\_%' OR r.schema_name = 'ref') THEN
      INSERT INTO app.provenance (workspace_id, kind, object_ref, sql_text, details)
      VALUES (current_setting('app.workspace_id', true), 'ddl_event', r.object_identity,
              current_query(),
              jsonb_build_object('command_tag', r.command_tag, 'object_type', r.object_type,
                                 'role', session_user));
    END IF;
  END LOOP;
END $$;

CREATE OR REPLACE FUNCTION app.provenance_sql_drop() RETURNS event_trigger
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM pg_event_trigger_dropped_objects() LOOP
    IF r.schema_name IS NOT NULL AND (r.schema_name LIKE 'ws\_%' OR r.schema_name = 'ref') THEN
      INSERT INTO app.provenance (workspace_id, kind, object_ref, sql_text, details)
      VALUES (current_setting('app.workspace_id', true), 'ddl_event', r.object_identity,
              current_query(),
              jsonb_build_object('command_tag', 'DROP', 'object_type', r.object_type,
                                 'role', session_user));
    END IF;
  END LOOP;
END $$;

COMMIT;
