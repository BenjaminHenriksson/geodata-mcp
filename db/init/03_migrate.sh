#!/bin/bash
set -euo pipefail
/opt/geodata-db/migrate.sh --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"
