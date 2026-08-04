-- 002: widen app.jobs.kind for the ogcapi/wmts/stac/text connectors.
--
-- Registering those source kinds has been accepted since v2 launch, but no job
-- kind existed to harvest or ingest them (the "phantom kinds" gap). This adds:
--   harvest_ogcapi / ingest_ogcapi  — OGC API Features collections
--   harvest_wmts                    — WMTS capabilities (raster_ref layers)
--   harvest_stac                    — STAC API collections (raster_ref)
--   ingest_text                     — text/HTML documents into doc.*
--
-- Apply to an existing database with:
--   docker compose exec -T postgres psql -U postgres -d geodata < db/migrations/002_connector_job_kinds.sql
-- (db/init/04_tables.sql carries the same list for fresh installs.)

BEGIN;
SET ROLE geodata_app;

ALTER TABLE app.jobs DROP CONSTRAINT IF EXISTS jobs_kind_check;
ALTER TABLE app.jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN (
  'harvest_wfs', 'harvest_wms', 'harvest_wmts', 'harvest_ogcapi', 'harvest_stac',
  'ingest_wfs', 'ingest_ogcapi', 'ingest_file', 'ingest_pdf', 'ingest_text',
  'embed_catalog', 'export'));

RESET ROLE;
COMMIT;
