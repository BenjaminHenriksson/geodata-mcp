CREATE SCHEMA catalog AUTHORIZATION geodata_app;
CREATE SCHEMA ref     AUTHORIZATION geodata_app;
CREATE SCHEMA doc     AUTHORIZATION geodata_app;
CREATE SCHEMA app     AUTHORIZATION geodata_app;

GRANT USAGE ON SCHEMA catalog, ref, doc, app TO agent_ro, agent_ws;
-- Everything geodata_app creates in the data schemas is readable by both agent roles.
-- `app` is deliberately NOT in this list: it holds session bookkeeping, and blanket SELECT
-- there would expose every session's row to every other session. Read access to the audit
-- tables is granted explicitly in 04_tables.sql.
ALTER DEFAULT PRIVILEGES FOR ROLE geodata_app IN SCHEMA catalog, ref, doc
  GRANT SELECT ON TABLES TO agent_ro, agent_ws;
-- PostGIS metadata lives in public.
GRANT USAGE ON SCHEMA public TO agent_ro, agent_ws, geodata_app;
