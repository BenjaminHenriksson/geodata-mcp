-- Provenance backstop (§8.1 of the architecture): any DDL touching ws_* or ref is witnessed
-- at the database level, whatever code path caused it. Owned by superuser; SECURITY DEFINER
-- so the insert works for any executing role.

CREATE OR REPLACE FUNCTION app.provenance_ddl_end() RETURNS event_trigger
LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE r record;
BEGIN
  FOR r IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
    IF r.schema_name IS NOT NULL AND (r.schema_name LIKE 'ws\_%' OR r.schema_name = 'ref') THEN
      INSERT INTO app.provenance (session_id, kind, object_ref, sql_text, details)
      VALUES (current_setting('app.session_id', true), 'ddl_event', r.object_identity,
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
      INSERT INTO app.provenance (session_id, kind, object_ref, sql_text, details)
      VALUES (current_setting('app.session_id', true), 'ddl_event', r.object_identity,
              current_query(),
              jsonb_build_object('command_tag', 'DROP', 'object_type', r.object_type,
                                 'role', session_user));
    END IF;
  END LOOP;
END $$;

CREATE EVENT TRIGGER provenance_ddl_end ON ddl_command_end
  EXECUTE FUNCTION app.provenance_ddl_end();
CREATE EVENT TRIGGER provenance_sql_drop ON sql_drop
  EXECUTE FUNCTION app.provenance_sql_drop();
