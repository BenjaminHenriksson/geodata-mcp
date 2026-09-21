-- 002: widen app.jobs.kind for the ogcapi/wmts/stac/text connectors.
--
-- Registering those source kinds has been accepted since v2 launch, but no job
-- kind existed to harvest or ingest them (the "phantom kinds" gap). This adds:
--   harvest_ogcapi / ingest_ogcapi  — OGC API Features collections
--   harvest_wmts                    — WMTS capabilities (raster_ref layers)
--   harvest_stac                    — STAC API collections (raster_ref)
--   ingest_text                     — text/HTML documents into doc.*
--
SET ROLE geodata_app;

-- Untracked installations may already include 003. Never narrow their constraint:
-- existing change_detect jobs must remain valid while establishing migration history.
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'app.jobs'::regclass
      AND conname = 'jobs_kind_check' AND pg_get_constraintdef(oid) LIKE '%change_detect%'
  ) THEN
    ALTER TABLE app.jobs DROP CONSTRAINT IF EXISTS jobs_kind_check;
    ALTER TABLE app.jobs ADD CONSTRAINT jobs_kind_check CHECK (kind IN (
      'harvest_wfs', 'harvest_wms', 'harvest_wmts', 'harvest_ogcapi', 'harvest_stac',
      'ingest_wfs', 'ingest_ogcapi', 'ingest_file', 'ingest_pdf', 'ingest_text',
      'embed_catalog', 'export'));
  END IF;
END $$;

RESET ROLE;
