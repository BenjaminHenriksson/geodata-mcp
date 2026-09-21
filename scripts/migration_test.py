#!/usr/bin/env python3
"""Exercise migrations in a disposable Docker database (no ports or persistent volumes).

Usage: docker build -t geodata-db-test db && .venv/bin/python scripts/migration_test.py --image geodata-db-test
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    name = f'geodata-migrations-{uuid.uuid4().hex[:10]}'
    root = Path(__file__).resolve().parents[1]
    files = list((root/'db/migrations').glob('*.sql'))
    migration_count = len(files)
    next_version = max(int(f.name.split('_')[0]) for f in files) + 1
    probe = f'/opt/geodata-db/migrations/{next_version:03}_failure.sql'

    def docker(*args, input=None, ok=True):
        result = subprocess.run(['docker', *args], input=input, text=True, capture_output=True)
        if ok and result.returncode:
            raise AssertionError(f'{args}:\n{result.stdout}\n{result.stderr}')
        return result

    def sql(query, database='fixture', ok=True):
        return docker('exec', '-i', name, 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
                      '-U', 'postgres', '-d', database, '-At', input=query, ok=ok)

    def migrate(database='fixture', ok=True):
        return docker('exec', name, '/opt/geodata-db/migrate.sh', '-U', 'postgres',
                      '-d', database, ok=ok)

    def snapshot():
        dump = docker('exec', name, 'pg_dump', '-U', 'postgres', '-d', 'fixture',
                      '--exclude-table=public.geodata_schema_migrations').stdout
        return '\n'.join(line for line in dump.splitlines() if not line.startswith(('\\restrict', '\\unrestrict')))

    try:
        env = {'POSTGRES_DB': 'fixture', 'POSTGRES_PASSWORD': 'fixture',
               'APP_DB_PASSWORD': 'fixture', 'AGENT_RO_PASSWORD': 'fixture', 'AGENT_WS_PASSWORD': 'fixture'}
        docker('run', '--rm', '-d', '--name', name, '--tmpfs', '/var/lib/postgresql/data',
               *[arg for k, v in env.items() for arg in ('-e', f'{k}={v}')], args.image,
               '-c', 'shared_preload_libraries=pgaudit')
        for _ in range(90):
            if docker('exec', name, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres', '-d', 'fixture', ok=False).returncode == 0:
                break
            time.sleep(1)
        else:
            raise AssertionError(docker('logs', name, ok=False).stdout)
        assert sql('SELECT count(*) FROM public.geodata_schema_migrations').stdout.strip() == str(migration_count)
        assert sql("SELECT has_database_privilege('geodata_app', 'fixture', 'CREATE')").stdout.strip() == 't'
        print('PASS fresh install, tracked versions and non-default database name', flush=True)

        # Simulate a fully populated pre-tracking deployment. Include values that an
        # older migration must not reject, plus identity, workspace and OAuth state.
        sql("""
          DROP TABLE public.geodata_schema_migrations;
          INSERT INTO app.api_keys(id,key_hash) VALUES ('00000000-0000-0000-0000-000000000001',repeat('a',64));
          INSERT INTO app.workspaces(id,api_key_id,name,ws_schema,is_active)
            VALUES ('00000000-0000-0000-0000-000000000002','00000000-0000-0000-0000-000000000001','fixture','ws_12345678',true);
          CREATE SCHEMA ws_12345678 AUTHORIZATION geodata_app;
          SET ROLE geodata_app;
          CREATE TABLE ws_12345678.points (id int, geom geometry(Point,3014));
          INSERT INTO ws_12345678.points VALUES (1,ST_SetSRID(ST_MakePoint(1,2),3014));
          INSERT INTO catalog.sources(slug,kind,title) VALUES ('fixture','inline','Fixture');
          INSERT INTO app.jobs(kind,status,workspace_id) VALUES ('change_detect','cancelled','00000000-0000-0000-0000-000000000002');
          INSERT INTO app.map_views(view_id,workspace_id,spec) VALUES ('v_123456789012345678901234','00000000-0000-0000-0000-000000000002','{}');
          INSERT INTO app.provenance(kind,object_ref) VALUES ('change_detect','ws_12345678.points');
          INSERT INTO app.query_log(sql_text) VALUES ('SELECT 1');
          INSERT INTO app.oauth_clients(client_id,redirect_uris) VALUES ('fixture','["https://example.test/callback"]');
          INSERT INTO app.oauth_tokens(token,kind,client_id,subject,expires_at) VALUES ('fixture','access','fixture','fixture',now()+interval '1 day');
        """)
        before = snapshot()
        migrate()
        assert snapshot() == before, 'Upgrade changed the existing schema or data'
        ledger = sql('TABLE public.geodata_schema_migrations ORDER BY filename').stdout
        migrate()
        assert sql('TABLE public.geodata_schema_migrations ORDER BY filename').stdout == ledger
        assert snapshot() == before
        assert sql('SET ROLE agent_ro; SELECT * FROM app.api_keys', ok=False).returncode != 0
        print('PASS untracked upgrade preserves schema, rows, spatial data and privileges; rerun is a no-op', flush=True)

        # Reconstruct the older transient-session schema before 001-005.
        sql('CREATE DATABASE old_fixture; GRANT CREATE ON DATABASE old_fixture TO geodata_app;')
        sql((root/'db/init/01_extensions.sql').read_text(), 'old_fixture')
        sql((root/'db/migrations/000_initial.sql').read_text(), 'old_fixture')
        sql("""
          CREATE TABLE app.sessions(id text PRIMARY KEY);
          ALTER TABLE app.jobs RENAME COLUMN workspace_id TO session_id;
          ALTER TABLE app.map_views RENAME COLUMN workspace_id TO session_id;
          ALTER TABLE app.provenance RENAME COLUMN workspace_id TO session_id;
          ALTER TABLE app.query_log RENAME COLUMN workspace_id TO session_id;
          INSERT INTO app.jobs(kind,status,session_id) VALUES ('export','done','historical-session');
        """, 'old_fixture')
        migrate('old_fixture')
        assert sql('SELECT workspace_id FROM app.jobs', 'old_fixture').stdout.strip() == 'historical-session'
        sql("INSERT INTO app.jobs(kind,status) VALUES ('change_detect','cancelled')", 'old_fixture')
        assert sql("SELECT to_regclass('app.sessions') IS NULL AND to_regclass('app.oauth_tokens') IS NOT NULL", 'old_fixture').stdout.strip() == 't'
        print('PASS older session schema upgrades and retains historical attribution', flush=True)

        # A failure must not leave either the DDL or a recorded history entry.
        docker('exec', '-i', name, 'sh', '-c', f'cat > {probe}',
               input='CREATE TABLE app.rollback_probe(id int); SELECT 1/0;\n')
        assert migrate(ok=False).returncode != 0
        assert sql("SELECT to_regclass('app.rollback_probe') IS NULL").stdout.strip() == 't'
        assert sql('SELECT count(*) FROM public.geodata_schema_migrations').stdout.strip() == str(migration_count)
        docker('exec', '-i', name, 'sh', '-c', f'cat > {probe}',
               input='SELECT pg_sleep(1); CREATE TABLE app.rollback_probe(id int);\n')
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: migrate(), range(2)))
        assert sql('SELECT count(*) FROM public.geodata_schema_migrations').stdout.strip() == str(migration_count + 1)
        print('PASS transactional failure/retry and concurrent migration runners', flush=True)
        docker('exec', name, 'sh', '-c', f'echo "-- changed" >> {probe}')
        result = migrate(ok=False)
        assert result.returncode != 0 and 'Applied migrations are missing or changed' in result.stderr
        docker('exec', name, 'rm', probe)
        assert migrate(ok=False).returncode != 0
        print('PASS changed or missing applied migrations are rejected', flush=True)
    finally:
        docker('rm', '-f', name, ok=False)


if __name__ == '__main__':
    main()
