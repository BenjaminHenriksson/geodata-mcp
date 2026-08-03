# Official postgres image (arm64/amd64) + PGDG packages; postgis/postgis lacks arm64.
FROM postgres:17-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      postgresql-17-postgis-3 postgresql-17-pgvector postgresql-17-pgaudit \
 && rm -rf /var/lib/apt/lists/*
