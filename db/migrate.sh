#!/bin/bash
# Run as the database superuser. Pass ordinary psql connection arguments.
# One session holds the lock; each migration and its history entry commit together.
set -euo pipefail
export LC_ALL=C
migrations=${MIGRATIONS_DIR:-$(dirname "$0")/migrations}
plan=$(mktemp)
trap 'rm -f "$plan"' EXIT
shopt -s nullglob
files=("$migrations"/*.sql)
[[ ${#files[@]} -gt 0 ]] || { echo "No migrations in $migrations" >&2; exit 1; }

cat > "$plan" <<'SQL'
SELECT pg_advisory_lock(715907234, 1);
CREATE TABLE IF NOT EXISTS public.geodata_schema_migrations (
  filename text PRIMARY KEY,
  checksum text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON public.geodata_schema_migrations FROM PUBLIC, geodata_app, agent_ro, agent_ws;
CREATE TEMP TABLE migration_files (filename text PRIMARY KEY, checksum text NOT NULL);
SQL

previous=''
for file in "${files[@]}"; do
  name=${file##*/}
  [[ $name =~ ^[0-9]{3}_[a-z0-9_]+\.sql$ ]] || { echo "Invalid migration filename: $name" >&2; exit 1; }
  version=${name%%_*}
  [[ $version != "$previous" ]] || { echo "Duplicate migration version: $version" >&2; exit 1; }
  previous=$version
  checksum=$(sha256sum "$file")
  checksum=${checksum%% *}
  printf "INSERT INTO migration_files VALUES ('%s', '%s');\n" "$name" "$checksum" >> "$plan"
done

cat >> "$plan" <<'SQL'
DO $$ BEGIN
  IF EXISTS (
    SELECT FROM public.geodata_schema_migrations applied
    LEFT JOIN migration_files files USING (filename)
    WHERE files.filename IS NULL OR files.checksum <> applied.checksum
  ) THEN
    RAISE EXCEPTION 'Applied migrations are missing or changed. Restore the original files; append new migrations instead.';
  END IF;
END $$;
SQL

for file in "${files[@]}"; do
  name=${file##*/}
  cat >> "$plan" <<SQL
SELECT NOT EXISTS (SELECT FROM public.geodata_schema_migrations WHERE filename = '$name') AS pending \gset
\if :pending
\echo Applying $name
BEGIN;
SQL
  cat "$file" >> "$plan"
  cat >> "$plan" <<SQL

RESET ROLE;
INSERT INTO public.geodata_schema_migrations (filename, checksum)
SELECT filename, checksum FROM migration_files WHERE filename = '$name';
COMMIT;
\endif
SQL
done
psql -X -v ON_ERROR_STOP=1 "$@" -f "$plan"
