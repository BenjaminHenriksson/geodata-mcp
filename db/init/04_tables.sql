SET ROLE geodata_app;

-- ─── catalog ────────────────────────────────────────────────────────────────

CREATE TABLE catalog.sources (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug        text UNIQUE NOT NULL CHECK (slug ~ '^[a-z0-9_]{1,60}$'),
  kind        text NOT NULL CHECK (kind IN ('wfs','wms','wmts','ogcapi','file','pdf','text','stac','inline')),
  url         text,
  title       text NOT NULL,
  description text NOT NULL DEFAULT '',
  license     text NOT NULL DEFAULT '',
  attribution text NOT NULL DEFAULT '',
  trust       text NOT NULL DEFAULT 'official' CHECK (trust IN ('official','community','agent')),
  auth_note   text NOT NULL DEFAULT '',
  added_by    text NOT NULL DEFAULT 'admin',
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE catalog.datasets (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id       uuid NOT NULL REFERENCES catalog.sources(id) ON DELETE CASCADE,
  external_id     text NOT NULL,
  kind            text NOT NULL CHECK (kind IN ('vector','raster_ref','document','table')),
  title           text NOT NULL,
  description     text NOT NULL DEFAULT '',
  keywords        text[] NOT NULL DEFAULT '{}',
  crs_native      text NOT NULL DEFAULT '',
  schema_summary  jsonb NOT NULL DEFAULT '{}'::jsonb,
  extent_3014     geometry(Polygon, 3014),
  feature_count   bigint,
  ref_table       text,
  embedding       vector(256),
  embedding_model text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_id, external_id)
);

CREATE INDEX datasets_trgm_idx ON catalog.datasets
  USING gin ((title || ' ' || description) gin_trgm_ops);
CREATE INDEX datasets_embedding_idx ON catalog.datasets
  USING hnsw (embedding vector_cosine_ops);
CREATE INDEX datasets_extent_idx ON catalog.datasets USING gist (extent_3014);

-- ─── doc ────────────────────────────────────────────────────────────────────

CREATE TABLE doc.documents (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id uuid REFERENCES catalog.datasets(id) ON DELETE SET NULL,
  source_url text NOT NULL,
  title      text NOT NULL,
  pages      int,
  fetched_at timestamptz NOT NULL DEFAULT now(),
  meta       jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE doc.chunks (
  id              bigserial PRIMARY KEY,
  document_id     uuid NOT NULL REFERENCES doc.documents(id) ON DELETE CASCADE,
  page            int,
  chunk_index     int NOT NULL,
  text            text NOT NULL,
  embedding       vector(256),
  embedding_model text
);

CREATE INDEX chunks_trgm_idx ON doc.chunks USING gin (text gin_trgm_ops);
CREATE INDEX chunks_embedding_idx ON doc.chunks USING hnsw (embedding vector_cosine_ops);

-- ─── app ────────────────────────────────────────────────────────────────────

-- API keys are the durable principal: the raw key travels as `Authorization: Bearer`
-- and only its SHA-256 digest is stored, so no row here is a replayable credential.
CREATE TABLE app.api_keys (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  key_hash   text UNIQUE NOT NULL CHECK (key_hash ~ '^[a-f0-9]{64}$'),
  name       text NOT NULL DEFAULT '',
  disabled   boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  last_used  timestamptz
);

-- Durable, named workspaces owned by an API key. Exactly one is active per key at a
-- time (partial unique index below); every MCP call reads/writes the active one, so a
-- reconnecting client lands in the same workspace instead of an empty schema.
CREATE TABLE app.workspaces (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  api_key_id uuid NOT NULL REFERENCES app.api_keys(id) ON DELETE CASCADE,
  name       text NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9_-]{0,39}$'),
  ws_schema  text UNIQUE NOT NULL CHECK (ws_schema ~ '^ws_[a-f0-9]{8}$'),
  is_active  boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now(),
  last_used  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (api_key_id, name)
);
CREATE UNIQUE INDEX workspaces_one_active_idx ON app.workspaces (api_key_id) WHERE is_active;

CREATE TABLE app.jobs (
  id          bigserial PRIMARY KEY,
  kind        text NOT NULL CHECK (kind IN ('harvest_wfs','harvest_wms','harvest_wmts',
                                            'harvest_ogcapi','harvest_stac',
                                            'ingest_wfs','ingest_ogcapi','ingest_file',
                                            'ingest_pdf','ingest_text',
                                            'embed_catalog','export','change_detect')),
  payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
  status      text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','error')),
  result      jsonb,
  error       text,
  workspace_id text,
  attempts    int NOT NULL DEFAULT 0,
  created_at  timestamptz NOT NULL DEFAULT now(),
  started_at  timestamptz,
  finished_at timestamptz
);
CREATE INDEX jobs_queued_idx ON app.jobs (status, created_at) WHERE status = 'queued';

CREATE TABLE app.map_views (
  view_id    text PRIMARY KEY CHECK (view_id ~ '^v_[a-f0-9]{24}$'),
  workspace_id text,
  title      text NOT NULL DEFAULT '',
  spec       jsonb NOT NULL,
  version    int NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE app.layer_meta (
  schema_name text NOT NULL,
  table_name  text NOT NULL,
  style       jsonb NOT NULL DEFAULT '{}'::jsonb,
  notes       text NOT NULL DEFAULT '',
  popup       text[] NOT NULL DEFAULT '{}',
  label       text NOT NULL DEFAULT '',
  visible     boolean NOT NULL DEFAULT true,
  PRIMARY KEY (schema_name, table_name)
);

CREATE TABLE app.provenance (
  id           bigserial PRIMARY KEY,
  ts           timestamptz NOT NULL DEFAULT now(),
  workspace_id text,
  kind         text NOT NULL CHECK (kind IN ('load','layer_create','layer_update','layer_drop',
                                             'layer_rename','ddl_event','export','inline',
                                             'change_detect')),
  object_ref   text,
  sql_text     text,
  input_tables text[],
  job_id       bigint,
  details      jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX provenance_object_idx ON app.provenance (object_ref, ts);

CREATE TABLE app.query_log (
  query_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ts                timestamptz NOT NULL DEFAULT now(),
  workspace_id      text,
  sql_text          text NOT NULL,
  referenced_tables text[],
  row_count         int,
  duration_ms       int,
  error             text
);

RESET ROLE;

-- Append-only ledger: nobody (owner included, absent re-granting) updates or deletes.
REVOKE UPDATE, DELETE, TRUNCATE ON app.provenance FROM PUBLIC, geodata_app, agent_ro, agent_ws;

-- Agent read access inside `app` is granted per table, never schema-wide (see 03_schemas.sql).
-- The audit tables are readable — that is the point of the auditing use case, and the
-- workspace_id they carry is a plain identifier, not a credential (auth is the API key).
-- app.api_keys and app.workspaces stay private so key digests and other principals'
-- workspace inventory are not enumerable from the query tool.
GRANT SELECT ON app.provenance, app.query_log, app.jobs, app.map_views, app.layer_meta
  TO agent_ro, agent_ws;
-- The event-trigger backstop function (SECURITY DEFINER, owner postgres) inserts regardless
-- of which role ran the DDL.
