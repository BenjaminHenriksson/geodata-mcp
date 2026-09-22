-- Add document inspection to the existing workspace job queue and provenance.
SET ROLE geodata_app;

ALTER TABLE app.jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE app.jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN (
  'harvest_wfs', 'harvest_wms', 'harvest_wmts', 'harvest_ogcapi', 'harvest_stac',
  'ingest_wfs', 'ingest_ogcapi', 'ingest_file', 'ingest_pdf', 'ingest_text',
  'embed_catalog', 'export', 'change_detect', 'inspect'));

ALTER TABLE app.provenance DROP CONSTRAINT provenance_kind_check;
ALTER TABLE app.provenance ADD CONSTRAINT provenance_kind_check CHECK (kind IN (
  'load', 'layer_create', 'layer_update', 'layer_drop',
  'layer_rename', 'ddl_event', 'export', 'inline', 'change_detect', 'inspect'));

RESET ROLE;
