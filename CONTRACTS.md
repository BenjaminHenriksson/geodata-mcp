# Geodata MCP v2 — Implementation Contracts

This file is the binding spec for all services. It concretizes `geodata-mcp-architecture.md`
for the v1 build. When architecture doc and this file disagree, this file wins.

Scope notes for this build:
- Target municipality: **Sundsvall** (GovTech Pilot 3, `data_sources.xlsx`). CRS **EPSG:3014** (SWEREF 99 17 15).
- Only local model: **EmbeddingGemma-300M** via ungated mirror `unsloth/embeddinggemma-300m`, truncated to **256 dims**, run inside the worker container (CPU). SAM 3 change detection, LightOnOCR, pgSTAC/TiTiler: **deferred** (documented, not built).
- Map snapshot PNG: deferred. Origo support = config compiler endpoint (see §Viewer); interactive page uses MapLibre.

## Topology (docker compose, one origin via Caddy)

| service  | container name | port (internal) | host port | role |
|----------|---------------|-----------------|-----------|------|
| caddy    | caddy         | 80              | **8080**  | reverse proxy, one origin |
| postgres | postgres      | 5432            | 5433      | PostGIS 17 + pgvector + pg_trgm |
| mcp      | mcp           | 8000            | —         | FastMCP streamable HTTP at `/mcp` |
| viewer   | viewer        | 8001            | —         | map pages + data endpoint |
| worker   | worker        | 8100            | —         | job runner + `/embed` HTTP |
| minio    | minio         | 9000 (api)      | 9000      | exports bucket; presigned URLs use host port |

Caddy routes (host `http://localhost:8080`):
- `/mcp*` → `mcp:8000` (path preserved)
- everything else (`/v/*`, `/data/*`, `/tiles/*`, `/static/*`, `/healthz`) → `viewer:8001`

MinIO presigned URLs are generated against `S3_PUBLIC_ENDPOINT=http://localhost:9000`
(direct host port; signature-safe, not proxied through Caddy).

## Environment variables (set in docker-compose; defaults in `.env`)

```
POSTGRES_DB=geodata            POSTGRES_USER=postgres        POSTGRES_PASSWORD=geodata_dev
DATABASE_URL_APP=postgresql://geodata_app:geodata_app@postgres:5432/geodata
DATABASE_URL_RO=postgresql://agent_ro:agent_ro@postgres:5432/geodata
DATABASE_URL_WS=postgresql://agent_ws:agent_ws@postgres:5432/geodata
PUBLIC_BASE_URL=http://localhost:8080
S3_ENDPOINT=http://minio:9000
S3_PUBLIC_ENDPOINT=http://localhost:9000
S3_BUCKET=exports
MINIO_ROOT_USER=geodata        MINIO_ROOT_PASSWORD=geodata_dev_minio
EMBED_URL=http://worker:8100/embed
EMBED_MODEL=unsloth/embeddinggemma-300m
EMBED_DIM=256
```

Roles: `geodata_app` (services; owns all app-managed schemas), `agent_ro` (agent SQL through
`query`; SELECT-only + 15 s statement timeout), `agent_ws` (the `layer` tool's write path;
may CREATE in `ws_*` schemas only, SELECT elsewhere). Passwords equal role names (dev).

## Database schema (created by `db/init/*.sql`, owner `geodata_app`)

Extensions: `postgis`, `vector`, `pg_trgm`, `unaccent`.

### catalog

```sql
catalog.sources (
  id            uuid PK default gen_random_uuid(),
  slug          text UNIQUE NOT NULL,            -- [a-z0-9_]+
  kind          text NOT NULL CHECK (kind IN ('wfs','wms','wmts','ogcapi','file','pdf','text','stac','inline')),
  url           text,
  title         text NOT NULL,
  description   text DEFAULT '',
  license       text DEFAULT '',
  attribution   text DEFAULT '',
  trust         text NOT NULL DEFAULT 'official' CHECK (trust IN ('official','community','agent')),
  auth_note     text DEFAULT '',                 -- e.g. 'requires Lantmäteriet account' (no secrets)
  added_by      text DEFAULT 'admin',            -- 'admin' | session_id
  created_at    timestamptz DEFAULT now()
)

catalog.datasets (
  id             uuid PK default gen_random_uuid(),
  source_id      uuid NOT NULL REFERENCES catalog.sources(id) ON DELETE CASCADE,
  external_id    text NOT NULL,                  -- WFS typename / WMS layer name / file path / doc URL
  kind           text NOT NULL CHECK (kind IN ('vector','raster_ref','document','table')),
  title          text NOT NULL,
  description    text DEFAULT '',
  keywords       text[] DEFAULT '{}',
  crs_native     text DEFAULT '',
  schema_summary jsonb DEFAULT '{}'::jsonb,      -- {"fields":[{"name":..,"type":..}], "geometry_type":..}
  extent_3014    geometry(Polygon, 3014),
  feature_count  bigint,
  ref_table      text,                           -- 'ref.<slug>' once ingested, else NULL
  embedding      vector(256),
  embedding_model text,
  created_at     timestamptz DEFAULT now(),
  updated_at     timestamptz DEFAULT now(),
  UNIQUE (source_id, external_id)
)
-- GIN trigram index on (title || ' ' || description); ivfflat/hnsw index on embedding (cosine).
```

### ref / workspaces

- `ref.*` — shared ingested tables, geometry column **`geom`**, SRID 3014, gist-indexed. Table
  names: `[a-z0-9_]{1,60}`.
- `ws_<8 hex>` — one schema per MCP session, created lazily by the MCP server (as
  `geodata_app`), `GRANT USAGE, CREATE` to `agent_ws`, `GRANT USAGE` + default SELECT to `agent_ro`.
  Workspaces are **namespacing and lifecycle, not tenancy**: because all agent SQL shares one
  `agent_ro` role, every session can read every workspace. Writes are session-scoped (the
  `layer` tool resolves the schema from the caller's session); reads are not. Per-user
  isolation requires auth + RLS or per-user roles, which §11 of the architecture defers.
- Workspace tables ingested by the worker (`target='workspace'`) must be
  `ALTER TABLE … OWNER TO agent_ws` after load, or the `layer` tool cannot mutate them.
- The session key stored in `app.sessions`/`provenance`/`jobs`/`map_views` is
  **SHA-256 of the `mcp-session-id` header**, never the raw token: the raw header is a bearer
  credential, so a leaked row must not be replayable. `ws_<hash>` = first 8 hex of that digest.
  Grants inside `app` are per table (never schema-wide `ALTER DEFAULT PRIVILEGES`), and
  `app.sessions` is not granted to the agent roles.

### doc

```sql
doc.documents (id uuid PK, dataset_id uuid REFERENCES catalog.datasets(id), source_url text,
               title text, pages int, fetched_at timestamptz, meta jsonb DEFAULT '{}')
doc.chunks    (id bigserial PK, document_id uuid REFERENCES doc.documents(id) ON DELETE CASCADE,
               page int, chunk_index int, text text NOT NULL,
               embedding vector(256), embedding_model text)
```

### app

```sql
app.sessions   (session_id text PK, ws_schema text UNIQUE NOT NULL,
                created_at timestamptz DEFAULT now(), last_seen timestamptz DEFAULT now())

app.jobs       (id bigserial PK,
                kind text NOT NULL CHECK (kind IN ('harvest_wfs','harvest_wms','ingest_wfs',
                     'ingest_file','ingest_pdf','embed_catalog','export')),
                payload jsonb NOT NULL DEFAULT '{}',
                status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','error')),
                result jsonb, error text, session_id text,
                attempts int DEFAULT 0, created_at timestamptz DEFAULT now(),
                started_at timestamptz, finished_at timestamptz)

app.map_views  (view_id text PK,                 -- 'v_' + 24 hex (secrets.token_hex(12))
                session_id text, title text,
                spec jsonb NOT NULL, version int NOT NULL DEFAULT 1,
                created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now())

app.layer_meta (schema_name text, table_name text, style jsonb DEFAULT '{}', notes text DEFAULT '',
                popup text[] DEFAULT '{}', label text DEFAULT '', visible bool DEFAULT true,
                PRIMARY KEY (schema_name, table_name))

app.provenance (id bigserial PK, ts timestamptz DEFAULT now(),
                session_id text, kind text NOT NULL,   -- 'load','layer_create','layer_update',
                                                        -- 'layer_drop','layer_rename','ddl_event','export','inline'
                object_ref text,                        -- 'schema.table' or dataset id or export key
                sql_text text, input_tables text[], job_id bigint, details jsonb DEFAULT '{}')
-- append-only: REVOKE UPDATE, DELETE ON app.provenance FROM PUBLIC and all app roles.

app.query_log  (query_id uuid PK DEFAULT gen_random_uuid(), ts timestamptz DEFAULT now(),
                session_id text, sql_text text NOT NULL, referenced_tables text[],
                row_count int, duration_ms int, error text)
```

Event triggers (backstop, §8.1): on `ddl_command_end` and `sql_drop`, if the affected object's
schema matches `ws_%` or `ref`, insert an `app.provenance` row with kind `ddl_event`, reading
`current_setting('app.session_id', true)` for attribution. The `layer` tool sets
`SET LOCAL app.session_id = '<sid>'` inside its transaction.

Geocoding functions (SECURITY DEFINER, owner `geodata_app`, executable by `agent_ro`):
- `app.geocode(q text, max_results int default 5)` → rows `(address text, geom geometry, score real)` —
  trigram similarity over the ingested address layer `ref.adressplats` (columns discovered at
  ingest; the function tolerates the table not existing yet by raising a clear notice).
- `app.reverse_geocode(x float8, y float8)` → nearest address within 500 m (input in 3014).

## Worker

Single Python process, two responsibilities:

1. **Job loop**: poll `app.jobs` every 2 s (`FOR UPDATE SKIP LOCKED`, status queued→running→done/error,
   max 2 attempts). The status update commits *before* the handler runs (the row lock cannot be
   held across a 15-minute ogr2ogr), so on startup the worker first sweeps jobs stranded in
   `running` by a previous crash — requeueing them, or failing them once attempts are spent.
   Connectors:
   - `harvest_wfs {source_id}` — GET GetCapabilities (WFS 2.0.0), parse FeatureTypes (name, title,
     abstract, keywords, default CRS, WGS84 bbox → transform to 3014 extent polygon), upsert
     `catalog.datasets` rows (kind `vector`). Result: `{datasets: N}`.
   - `harvest_wms {source_id}` — GetCapabilities (WMS 1.3.0), named layers → datasets kind `raster_ref`.
   - `ingest_wfs {dataset_id, target_schema, table_name}` — `ogr2ogr -f PostgreSQL` from the WFS
     (`WFS:<url>` datasource, `-t_srs EPSG:3014 -nlt PROMOTE_TO_MULTI -lco GEOMETRY_NAME=geom
     -lco FID=fid --config OGR_WFS_PAGING_ALLOWED ON`), into `target_schema.table_name`; then
     update `catalog.datasets.ref_table`, `feature_count`, `schema_summary`; gist index; append
     provenance kind `load`. Connection via `DATABASE_URL_APP` (worker owns ref).
   - `ingest_file {path|url, table_name, target_schema}` — same via ogr2ogr from GDAL-readable file.
   - `ingest_pdf {dataset_id|url, title}` — download, pdfplumber text per page, ~1200-char chunks
     with 150 overlap, insert doc.documents + doc.chunks, embed chunks (task `document`).
   - `embed_catalog {}` — embed all `catalog.datasets` rows where `embedding IS NULL OR
     embedding_model <> $EMBED_MODEL` (text = title + description + keywords), and any unembedded
     doc.chunks; batch 32; set `embedding_model`.
   - `export {layers: [..], format, session_id, cite}` — `ogr2ogr -f <driver>` from PG to
     `/tmp/exp_<id>.<ext>` (gpkg|geojson|csv|parquet), upload to MinIO `exports/` via boto3-style
     client (use `minio` py lib), plus `<name>.citation.md` sidecar built from `app.provenance` +
     `catalog.sources` for the exported layers when `cite`. Result: `{object_key, sidecar_key}`.
     CSV gets `-lco GEOMETRY=AS_WKT`. GeoJSON in 4326 (`-t_srs EPSG:4326`), others native 3014.
2. **Embed HTTP** (same process, FastAPI on :8100):
   - `POST /embed {"texts": [...], "task": "query"|"document"}` → `{"embeddings": [[256 floats]...]}`.
     sentence-transformers `SentenceTransformer(EMBED_MODEL, truncate_dim=256)`;
     `encode(texts, prompt_name="query"|"document", normalize_embeddings=True)`.
   - `GET /healthz` → `{"ok": true, "model_loaded": bool}`. Model loads lazily on first use in a
     thread; job loop must not block on model load.

On start, worker ensures the MinIO bucket exists. GDAL present in image (use
`ghcr.io/osgeo/gdal:ubuntu-small-*` base or apt `gdal-bin python3-gdal`).

## MCP server (FastMCP, streamable HTTP, stateless_http-compatible)

Mounted at `/mcp`. On each tool call, resolve the MCP session id (from FastMCP context); get or
create `app.sessions` row (ws schema `ws_` + first 8 hex of sha256(session id); create schema
lazily only when `layer`/`load op=inline` first writes). Six tools; all return JSON-serializable dicts.
Docstrings must be agent-facing and include SQL guidance (PostGIS 3.5, `geom` column, SRID 3014,
`app.geocode`, catalog tables readable).

1. `search(query=None, id=None, kind=None, limit=15)`
   - `id` given → full `catalog.datasets` row (minus embedding) + source + provenance entries
     touching its `ref_table`, + for documents its doc.documents row.
   - else hybrid search: trigram `similarity()` on title+description (threshold 0.05) UNION
     vector cosine over embeddings of `query` (via `EMBED_URL`, task `query`; skip silently if
     embed service unavailable) → merge by dataset id keeping best rank; also search doc.chunks
     (vector + trigram) returning top chunks grouped per document. Return
     `{datasets: [{id, title, kind, external_id, description, ref_table, source_slug, score}...], chunks: [...]}`.
2. `load(op, ...)` — ops:
   - `register {kind, url, title, slug=None, license='', notes=''}` → insert catalog.sources
     (added_by = session), enqueue harvest job for wfs/wms; return `{source_id, job_id}`.
     **Idempotent on (kind, url)**: an already-registered endpoint is re-harvested, never
     duplicated — a forked catalog doubles search results and embedding cost.
     For kind `pdf`/`file`: also insert a `catalog.datasets` row immediately (kind `document` /
     source-kind-appropriate, external_id = url) and return its `dataset_id` so `ingest` can
     target it without a harvest step.
   - `embed {}` → enqueue an `embed_catalog` job; returns `{job_id}`. (Normally triggered after
     harvests/PDF ingests; idempotent — only missing/model-mismatched rows are embedded.)
   - `ingest {dataset_id, table_name=None, target='ref'|'workspace'}` — refuses a target table
     already claimed by a *different* dataset (ogr2ogr runs `-overwrite`, so a name collision
     would silently replace another dataset's data). → enqueue `ingest_wfs` (or
     `ingest_pdf` for kind document, `ingest_file` for file sources). `workspace` targets the
     session's ws schema. Poll the job up to 8 s before returning (`status` in the reply either way).
   - `inline {rows, table_name, source}` — rows = list of flat dicts, optional `wkt` or
     `lon`/`lat` keys become geom (4326→3014). Synchronous insert into ws schema; provenance kind `inline`.
   - `status {job_id}` → job row. `jobs {}` → last 20 jobs.
3. `query(sql, limit=500)` — single statement, must start with SELECT/WITH/EXPLAIN/SHOW/VALUES/TABLE
   (case-insensitive). Run `EXPLAIN (FORMAT JSON)` first as agent_ro → collect referenced
   `schema.relation`s; then execute as agent_ro with `SET LOCAL statement_timeout='15s'`, cap
   `limit` at 1000 rows. Geometry columns are returned as `ST_AsText` capped at 400 chars
   (server post-processes via information from cursor description / value sniffing). Log to
   app.query_log; return `{query_id, columns, rows, row_count, truncated, referenced_tables}`.
   On SQL error: return `{error, hint}` (no exception), still logged.
4. `layer(op, ...)` — the ONLY write path; every op appends `app.provenance` and runs with
   `SET LOCAL app.session_id`:
   - `create {name, sql, notes='', style=None}` — validate name; EXPLAIN the SELECT (as agent_ro)
     for input tables; as agent_ws: `CREATE TABLE ws_x.<name> AS <sql>`; if a geometry column
     exists, gist index it; upsert app.layer_meta; provenance `layer_create` (sql_text, inputs).
     Return `{table: 'ws_x.name', row_count, columns}`.
   - `update {name, key_column, values}` — values = `{key: {col: val}}`; parameterized
     `UPDATE ws_x.name SET .. FROM (VALUES ..)`; new columns added as needed (text/numeric
     inferred); provenance `layer_update` with the values dict in details.
   - `style {name, style=None, popup=None, label=None, visible=None}` — upsert layer_meta only.
   - `rename {name, new_name}`, `drop {name}`, `list {}` (session layers + ref tables with meta).
5. `map(op='upsert', view_id=None, title=None, layers=None, basemap='positron', extent_3014=None, legend=True)`
   - upsert: validate layer refs (existing table in ref/own ws, or `wms:<dataset_id>` for
     raster_ref datasets); missing extent → compute from ST_Extent of the vector layers; insert or
     update app.map_views (version++). Layer entry: `{ref, style?, popup?, label?, visible?}`;
     style keys: `fill, stroke, opacity, circle, width` (hex colors). Return
     `{view_id, url: PUBLIC_BASE_URL/v/<view_id>, version}`.
   - `get {view_id}` → spec + version. `list {}` → session's views.
6. `export(layers, format='gpkg', cite=True)` — enqueue export job, wait up to 30 s, presign
   MinIO GET URLs (24 h) against `S3_PUBLIC_ENDPOINT` → `{url, sidecar_url, format, expires_hours}`.

Errors: tools return `{"error": "..."}` dicts rather than raising, with actionable messages.

## Viewer

FastAPI on :8001. DB via `DATABASE_URL_APP` (read paths only).

- `GET /healthz` → ok.
- `GET /v/{view_id}?renderer=maplibre|origo` — HTML page. 404 for unknown view. The page:
  - loads vendored `/static/maplibre-gl.js` + `.css` (vendor at build time from npm/unpkg into the image),
  - fetches `/v/{view_id}/style.json`, applies it, fits `extent_3014` (transformed client-side
    to 4326 via a tiny inline proj snippet or server-provided `extent_4326` — server provides
    `extent_4326` in style metadata; use it),
  - **ETag polling**: every 4 s `fetch('/v/{id}/style.json', {headers: {'If-None-Match': etag}})`;
    on 200 (changed), `map.setStyle(newStyle, {diff: true})` and refit if extent changed,
  - popups: click on a layer with `popup` attrs shows those attributes,
  - legend control (simple HTML overlay listing visible layers with their fill/stroke swatches),
    when `spec.legend`.
- `GET /v/{view_id}/style.json` — MapLibre style compiled from the spec. Response header
  `ETag: W/"<version>"`; honours `If-None-Match` → 304. Style content:
  - basemap `positron` → raster source `https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png`
    (attribution CARTO/OSM), `osm` → tile.openstreetmap.org, `none` → background only;
    `wms:<dataset_id>` → raster source with WMS GetMap URL template (EPSG:3857).
  - each vector layer → GeoJSON source `/data/{schema}.{table}.geojson?view={view_id}&crs=4326`
    if feature_count ≤ 20 000 (or unknown), else vector-tile source
    `/tiles/{schema}.{table}/{z}/{x}/{y}.mvt?view={view_id}` (source-layer = table name).
  - style mapping: polygons → fill layer (fill, opacity 0.45 default) + line layer (stroke);
    lines → line layer (stroke/width); points → circle layer (circle px, fill). Defaults from
    app.layer_meta.style, overridden by the spec's per-layer style. `metadata` on the style
    carries `{view_id, version, extent_4326, legend: [...], popups: {...}}` for the page.
- `GET /v/{view_id}/origo.json` — Origo JSON config: projection `EPSG:3014` (+proj4 def),
  extent, WMS layers pass-through, vector layers as GeoJSON sources (`crs=3014`), styles mapped
  to Origo style objects. (Interop contract; not served as an interactive page in v1.)
- `GET /data/{layer}.geojson?view=<view_id>&crs=4326|3014&limit=<n≤50000>` — `layer` is
  `schema.table`; **require** the `view` param and check the layer is referenced by that view
  (capability check); ST_AsGeoJSON FeatureCollection, default cap 20 000 features, geometry
  simplified with `ST_SimplifyPreserveTopology` tolerance chosen by feature_count (0 below 5 000).
- `GET /tiles/{layer}/{z}/{x}/{y}.mvt?view=<view_id>` — same capability check;
  `ST_AsMVT(ST_AsMVTGeom(ST_Transform(geom,3857), ST_TileEnvelope(z,x,y)), <table>, 4096)`,
  properties included, 15 s timeout, empty tile → 204.

Identifier safety everywhere: schema/table names validated `^[a-z0-9_]{1,63}$` and quoted via
`format('%I')`/psycopg `sql.Identifier`; never interpolated raw.

## Definition of done for this build

1. `docker compose up` from clean → all services healthy.
2. Sundsvall WFS registered + harvested (~900 datasets in catalog), ≥ 12 pilot layers ingested
   into `ref`, plan-PDF ingested into doc with chunks embedded.
3. `search("strandskydd")` returns the right dataset via hybrid search (embeddings live).
4. Full agent flow over MCP HTTP: search → load → query (query_id logged) → layer create
   (provenance row) → map upsert → URL opens in Chrome and renders → export GPKG verified
   with ogrinfo; citation sidecar present.
5. Map updates picked up by an open browser page within ~5 s via ETag polling.
