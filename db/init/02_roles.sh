#!/bin/bash
# Role creation with passwords from the environment (see .env.example), so no
# credential is committed. Runs once, on an empty data volume, before the
# numbered .sql files that follow it.
set -euo pipefail

: "${APP_DB_PASSWORD:?APP_DB_PASSWORD is required (copy .env.example to .env)}"
: "${AGENT_RO_PASSWORD:?AGENT_RO_PASSWORD is required (copy .env.example to .env)}"
: "${AGENT_WS_PASSWORD:?AGENT_WS_PASSWORD is required (copy .env.example to .env)}"

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_pw="$APP_DB_PASSWORD" -v ro_pw="$AGENT_RO_PASSWORD" -v ws_pw="$AGENT_WS_PASSWORD" <<'EOSQL'
-- Service role: owns all app-managed schemas, used by mcp/worker/viewer control planes.
CREATE ROLE geodata_app LOGIN PASSWORD :'app_pw';
GRANT CREATE ON DATABASE geodata TO geodata_app;

-- Agent read path (the `query` tool).
CREATE ROLE agent_ro LOGIN PASSWORD :'ro_pw';
ALTER ROLE agent_ro SET statement_timeout = '15s';
ALTER ROLE agent_ro SET default_transaction_read_only = on;
ALTER ROLE agent_ro SET pgaudit.log = 'read';

-- Agent write path (the `layer` tool): may CREATE only inside ws_* schemas (granted per schema).
CREATE ROLE agent_ws LOGIN PASSWORD :'ws_pw';
ALTER ROLE agent_ws SET statement_timeout = '60s';
ALTER ROLE agent_ws SET pgaudit.log = 'all';

-- The app role may read workspace tables created by agent_ws (viewer/data endpoint, export),
-- and may clean them up (TTL garbage collection drops the whole schema).
GRANT agent_ws TO geodata_app;
EOSQL
