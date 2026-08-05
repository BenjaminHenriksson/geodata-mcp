-- 003: widen app.jobs.kind and app.provenance.kind for orthophoto change detection.
--
-- load(op="change_detect") enqueues a 'change_detect' job (SAM3 segmentation over
-- two Lantmäteriet orthophoto vintages, diffed into a workspace layer), and the
-- worker records a provenance row of the same kind for the output tables.
--
-- Apply to an existing database with:
--   docker compose exec -T postgres psql -U postgres -d geodata < db/migrations/003_change_detection.sql
-- (db/init/04_tables.sql carries the same lists for fresh installs.)

BEGIN;
SET ROLE geodata_app;

ALTER TABLE app.jobs DROP CONSTRAINT IF EXISTS jobs_kind_check;
ALTER TABLE app.jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN (
  'harvest_wfs', 'harvest_wms', 'harvest_wmts', 'harvest_ogcapi', 'harvest_stac',
  'ingest_wfs', 'ingest_ogcapi', 'ingest_file', 'ingest_pdf', 'ingest_text',
  'embed_catalog', 'export', 'change_detect'));

ALTER TABLE app.provenance DROP CONSTRAINT IF EXISTS provenance_kind_check;
ALTER TABLE app.provenance ADD CONSTRAINT provenance_kind_check CHECK (kind IN (
  'load', 'layer_create', 'layer_update', 'layer_drop',
  'layer_rename', 'ddl_event', 'export', 'inline', 'change_detect'));

RESET ROLE;
COMMIT;
