-- Core schema. IF NOT EXISTS also supports databases initialized before migration tracking.
CREATE SCHEMA IF NOT EXISTS catalog AUTHORIZATION geodata_app;
CREATE SCHEMA IF NOT EXISTS ref     AUTHORIZATION geodata_app;
CREATE SCHEMA IF NOT EXISTS doc     AUTHORIZATION geodata_app;
CREATE SCHEMA IF NOT EXISTS app     AUTHORIZATION geodata_app;

GRANT USAGE ON SCHEMA catalog, ref, doc, app TO agent_ro, agent_ws;
-- Everything geodata_app creates in the data schemas is readable by both agent roles.
-- `app` is deliberately NOT in this list: it holds API-key digests and workspace
-- bookkeeping, and blanket SELECT there would expose every principal's rows to every
-- other one. Read access to the audit tables is granted explicitly in the migrations below.
ALTER DEFAULT PRIVILEGES FOR ROLE geodata_app IN SCHEMA catalog, ref, doc
  GRANT SELECT ON TABLES TO agent_ro, agent_ws;
-- PostGIS metadata lives in public.
GRANT USAGE ON SCHEMA public TO agent_ro, agent_ws, geodata_app;

SET ROLE geodata_app;

-- ─── catalog ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS catalog.sources (
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

CREATE TABLE IF NOT EXISTS catalog.datasets (
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

CREATE INDEX IF NOT EXISTS datasets_trgm_idx ON catalog.datasets
  USING gin ((title || ' ' || description) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS datasets_embedding_idx ON catalog.datasets
  USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS datasets_extent_idx ON catalog.datasets USING gist (extent_3014);

-- ─── doc ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS doc.documents (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id uuid REFERENCES catalog.datasets(id) ON DELETE SET NULL,
  source_url text NOT NULL,
  title      text NOT NULL,
  pages      int,
  fetched_at timestamptz NOT NULL DEFAULT now(),
  meta       jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS doc.chunks (
  id              bigserial PRIMARY KEY,
  document_id     uuid NOT NULL REFERENCES doc.documents(id) ON DELETE CASCADE,
  page            int,
  chunk_index     int NOT NULL,
  text            text NOT NULL,
  embedding       vector(256),
  embedding_model text
);

CREATE INDEX IF NOT EXISTS chunks_trgm_idx ON doc.chunks USING gin (text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON doc.chunks USING hnsw (embedding vector_cosine_ops);

-- ─── app ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS app.jobs (
  id          bigserial PRIMARY KEY,
  kind        text NOT NULL CHECK (kind IN ('harvest_wfs','harvest_wms','harvest_wmts',
                                            'harvest_ogcapi','harvest_stac',
                                            'ingest_wfs','ingest_ogcapi','ingest_file',
                                            'ingest_pdf','ingest_text',
                                            'embed_catalog','export')),
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
CREATE INDEX IF NOT EXISTS jobs_queued_idx ON app.jobs (status, created_at) WHERE status = 'queued';

CREATE TABLE IF NOT EXISTS app.map_views (
  view_id    text PRIMARY KEY CHECK (view_id ~ '^v_[a-f0-9]{24}$'),
  workspace_id text,
  title      text NOT NULL DEFAULT '',
  spec       jsonb NOT NULL,
  version    int NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app.layer_meta (
  schema_name text NOT NULL,
  table_name  text NOT NULL,
  style       jsonb NOT NULL DEFAULT '{}'::jsonb,
  notes       text NOT NULL DEFAULT '',
  popup       text[] NOT NULL DEFAULT '{}',
  label       text NOT NULL DEFAULT '',
  visible     boolean NOT NULL DEFAULT true,
  PRIMARY KEY (schema_name, table_name)
);

CREATE TABLE IF NOT EXISTS app.provenance (
  id           bigserial PRIMARY KEY,
  ts           timestamptz NOT NULL DEFAULT now(),
  workspace_id text,
  kind         text NOT NULL CHECK (kind IN ('load','layer_create','layer_update','layer_drop',
                                             'layer_rename','ddl_event','export','inline')),
  object_ref   text,
  sql_text     text,
  input_tables text[],
  job_id       bigint,
  details      jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS provenance_object_idx ON app.provenance (object_ref, ts);

CREATE TABLE IF NOT EXISTS app.query_log (
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

-- Agent read access inside `app` is granted per table, never schema-wide (see the default privileges above).
-- The audit tables are readable — that is the point of the auditing use case, and the
-- workspace_id they carry is a plain identifier, not a credential (auth is the API key).
-- app.api_keys and app.workspaces stay private so key digests and other principals'
-- workspace inventory are not enumerable from the query tool.
GRANT SELECT ON app.provenance, app.query_log, app.jobs, app.map_views, app.layer_meta
  TO agent_ro, agent_ws;
-- The event-trigger backstop function (SECURITY DEFINER, owner postgres) inserts regardless
-- of which role ran the DDL.

SET ROLE geodata_app;

-- Geocoding as SQL (§8 of the architecture): trigram search over the ingested address layer.
-- The worker creates/refreshes app.address_points (addr text, geom geometry(Point,3014))
-- after ingesting the municipal address dataset; until then these raise a clear error.

CREATE OR REPLACE FUNCTION app.geocode(q text, max_results int DEFAULT 5)
RETURNS TABLE (address text, geom geometry, score real)
LANGUAGE plpgsql STABLE AS $$
BEGIN
  IF to_regclass('app.address_points') IS NULL THEN
    RAISE EXCEPTION 'address layer not loaded yet — ingest the municipal address dataset '
                    '(Adressplats) first; the worker then creates app.address_points';
  END IF;
  RETURN QUERY EXECUTE
    'SELECT addr, geom, similarity(addr, $1)::real AS score
       FROM app.address_points
      WHERE addr % $1 OR addr ILIKE ''%'' || $1 || ''%''
      ORDER BY score DESC LIMIT $2'
    USING q, max_results;
END $$;

CREATE OR REPLACE FUNCTION app.reverse_geocode(x float8, y float8)
RETURNS TABLE (address text, geom geometry, distance_m float8)
LANGUAGE plpgsql STABLE AS $$
BEGIN
  IF to_regclass('app.address_points') IS NULL THEN
    RAISE EXCEPTION 'address layer not loaded yet — ingest the municipal address dataset '
                    '(Adressplats) first; the worker then creates app.address_points';
  END IF;
  RETURN QUERY EXECUTE
    'SELECT addr, geom, ST_Distance(geom, ST_SetSRID(ST_MakePoint($1,$2),3014)) AS d
       FROM app.address_points
      WHERE ST_DWithin(geom, ST_SetSRID(ST_MakePoint($1,$2),3014), 500)
      ORDER BY d LIMIT 1'
    USING x, y;
END $$;

RESET ROLE;
