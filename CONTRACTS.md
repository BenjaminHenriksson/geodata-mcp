# Geodata MCP v2 — Implementation Contracts

This file is the binding spec for all services. It concretizes `geodata-mcp-architecture.md`
for the v1 build. When architecture doc and this file disagree, this file wins.

Scope notes for this build:
- Target municipality: **Sundsvall** (GovTech Pilot 3, `data_sources.xlsx`). CRS **EPSG:3014** (SWEREF 99 17 15).
- Local models: **EmbeddingGemma-300M** via ungated mirror `unsloth/embeddinggemma-300m`, truncated to **256 dims**, run inside the worker container (CPU); **SAM 3** concept segmentation for orthophoto change detection via `mlx-community/sam3-image`, run natively on the host by `services/segmenter/` (MLX needs Apple Silicon — the HTTP contract in that service is the seam for swapping in `facebook/sam3` on transformers/GPU). LightOnOCR, pgSTAC/TiTiler: **deferred** (documented, not built).
- Map snapshot PNG: deferred. **Both renderers are interactive**: MapLibre GL JS at `/v/<id>`, Origo 2.10 (OpenLayers) at `/v/<id>?renderer=origo`, compiled from the same map spec and vendored into the viewer image.

## Topology (docker compose, one origin via Caddy)

| service  | container name | port (internal) | host port | role |
|----------|---------------|-----------------|-----------|------|
| caddy    | caddy         | 80              | **8080**  | reverse proxy, one origin |
| postgres | postgres      | 5432            | 5433      | PostGIS 17 + pgvector + pg_trgm |
| mcp      | mcp           | 8000            | —         | FastMCP streamable HTTP at `/mcp`, bearer auth |
| viewer   | viewer        | 8001            | —         | map pages + data endpoint + workspace manager UI |
| worker   | worker        | 8100            | —         | job runner + `/embed` HTTP |
| minio    | minio         | 9000 (api)      | 9000      | exports bucket; presigned URLs use host port |

Outside compose: **segmenter** (`services/segmenter/`, native host process, port **8200**) — SAM 3
concept segmentation over HTTP. The worker reaches it via `SAM3_URL` (default
`http://host.docker.internal:8200`; compose adds the `host-gateway` extra_host for Linux
engines). MLX cannot run in Linux containers; the same HTTP contract is served by the
`transformers` backend (`SAM3_BACKEND=transformers`, `[hf]` extra) for a Linux/GPU deployment.

Caddy routes (host `http://localhost:8080`):
- `/mcp*` → `mcp:8000` (path preserved)
- everything else (`/v/*`, `/data/*`, `/tiles/*`, `/static/*`, `/healthz`) → `viewer:8001`

MinIO presigned URLs are generated against `S3_PUBLIC_ENDPOINT=http://localhost:9000`
(direct host port; signature-safe, not proxied through Caddy).

## Environment variables

Credentials live in `.env` (gitignored; `.env.example` is the committed template) and are
interpolated into `docker-compose.yml` — no password is committed. Compose refuses to start
if a required variable is missing. `db/init/02_roles.sh` reads the role passwords from the
environment on first initialization. Effective values:

```
DATABASE_URL_APP=postgresql://geodata_app:$APP_DB_PASSWORD@postgres:5432/$POSTGRES_DB
DATABASE_URL_RO=postgresql://agent_ro:$AGENT_RO_PASSWORD@postgres:5432/$POSTGRES_DB
DATABASE_URL_WS=postgresql://agent_ws:$AGENT_WS_PASSWORD@postgres:5432/$POSTGRES_DB
PUBLIC_BASE_URL=http://localhost:8080
S3_ENDPOINT=http://minio:9000          # in-cluster
S3_PUBLIC_ENDPOINT=http://localhost:9000  # signed into presigned URLs
S3_BUCKET=exports
EMBED_URL=http://worker:8100/embed
EMBED_MODEL=unsloth/embeddinggemma-300m
EMBED_DIM=256
GEODATA_API_KEYS=<comma-separated raw keys>   # mcp: bearer auth; refuses to start if empty
VIEWER_SECRET=<random string>                 # viewer: signs the manager-UI login cookie
LANTMATERIET_CREDENTIALS=<user:password>      # worker + viewer: Basic auth for *.lantmateriet.se
GEODATA_HTTP_CREDENTIALS=<host=user:pw,...>   # worker + viewer: per-host auth (optional)
SAM3_URL=http://host.docker.internal:8200     # worker: segmenter service (native host process)
```

Passwords are interpolated into connection URLs verbatim, so they must be URL-safe
(alphanumeric) unless pre-encoded.

Roles: `geodata_app` (services; owns all app-managed schemas), `agent_ro` (agent SQL through
`query`; SELECT-only + 15 s statement timeout), `agent_ws` (the `layer` tool's write path;
may CREATE in `ws_*` schemas only, SELECT elsewhere).

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
  added_by      text DEFAULT 'admin',            -- 'admin' | workspace_id
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
- `ws_<8 hex>` — one schema per **durable workspace** (random suffix, not derived), created
  lazily by the MCP server (as `geodata_app`) on first write, `GRANT USAGE, CREATE` to
  `agent_ws`, `GRANT USAGE` + default SELECT to `agent_ro`.
  Workspaces are **namespacing and lifecycle, not tenancy**: because all agent SQL shares one
  `agent_ro` role, every authenticated principal can read every workspace. Writes are
  workspace-scoped (the `layer` tool resolves the schema from the caller's active workspace);
  reads are not. Per-principal read isolation requires RLS or per-user roles (§11).
- Workspace tables ingested by the worker (`target='workspace'`) must be
  `ALTER TABLE … OWNER TO agent_ws` after load, or the `layer` tool cannot mutate them.
- The attribution key stored in `provenance`/`jobs`/`map_views`/`query_log` is the
  **workspace uuid** (`workspace_id`) — a plain identifier, not a credential. The credential
  is the API key, stored only as a SHA-256 digest in `app.api_keys`. Grants inside `app` are
  per table (never schema-wide `ALTER DEFAULT PRIVILEGES`); `app.api_keys` and
  `app.workspaces` are not granted to the agent roles.
- Workspaces are deleted explicitly (tool or manager UI), never by idle TTL. The worker sweeps
  hourly for `ws_*` schemas that no `app.workspaces` row owns and drops those.

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
app.api_keys   (id uuid PK default gen_random_uuid(),
                key_hash text UNIQUE NOT NULL,     -- sha256 hex of the raw bearer key
                name text DEFAULT '', disabled boolean NOT NULL DEFAULT false,
                created_at timestamptz DEFAULT now(), last_used timestamptz)

app.workspaces (id uuid PK default gen_random_uuid(),
                api_key_id uuid NOT NULL REFERENCES app.api_keys(id) ON DELETE CASCADE,
                name text NOT NULL CHECK (name ~ '^[a-z0-9][a-z0-9_-]{0,39}$'),
                ws_schema text UNIQUE NOT NULL CHECK (ws_schema ~ '^ws_[a-f0-9]{8}$'),
                is_active boolean NOT NULL DEFAULT false,
                created_at timestamptz DEFAULT now(), last_used timestamptz DEFAULT now(),
                UNIQUE (api_key_id, name))
-- partial unique index: exactly one active workspace per key
CREATE UNIQUE INDEX workspaces_one_active_idx ON app.workspaces (api_key_id) WHERE is_active;

app.jobs       (id bigserial PK,
                kind text NOT NULL CHECK (kind IN ('harvest_wfs','harvest_wms','harvest_wmts',
                     'harvest_ogcapi','harvest_stac','ingest_wfs','ingest_ogcapi',
                     'ingest_file','ingest_pdf','ingest_text','embed_catalog','export',
                     'change_detect')),
                payload jsonb NOT NULL DEFAULT '{}',
                status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','error')),
                result jsonb, error text, workspace_id text,
                attempts int DEFAULT 0, created_at timestamptz DEFAULT now(),
                started_at timestamptz, finished_at timestamptz)

app.map_views  (view_id text PK,                 -- 'v_' + 24 hex (secrets.token_hex(12))
                workspace_id text, title text,
                spec jsonb NOT NULL, version int NOT NULL DEFAULT 1,
                created_at timestamptz DEFAULT now(), updated_at timestamptz DEFAULT now())

app.layer_meta (schema_name text, table_name text, style jsonb DEFAULT '{}', notes text DEFAULT '',
                popup text[] DEFAULT '{}', label text DEFAULT '', visible bool DEFAULT true,
                PRIMARY KEY (schema_name, table_name))

app.provenance (id bigserial PK, ts timestamptz DEFAULT now(),
                workspace_id text, kind text NOT NULL,   -- 'load','layer_create','layer_update',
                                                        -- 'layer_drop','layer_rename','ddl_event',
                                                        -- 'export','inline','change_detect'
                object_ref text,                        -- 'schema.table' or dataset id or export key
                sql_text text, input_tables text[], job_id bigint, details jsonb DEFAULT '{}')
-- append-only: REVOKE UPDATE, DELETE ON app.provenance FROM PUBLIC and all app roles.

app.query_log  (query_id uuid PK DEFAULT gen_random_uuid(), ts timestamptz DEFAULT now(),
                workspace_id text, sql_text text NOT NULL, referenced_tables text[],
                row_count int, duration_ms int, error text)
```

Event triggers (backstop, §8.1): on `ddl_command_end` and `sql_drop`, if the affected object's
schema matches `ws_%` or `ref`, insert an `app.provenance` row with kind `ddl_event`, reading
`current_setting('app.workspace_id', true)` for attribution. The `layer` and `workspace` tools
set `app.workspace_id` inside their transactions.

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
   - `harvest_wmts {source_id}` — WMTS 1.0.0 GetCapabilities → datasets kind `raster_ref`, with
     `schema_summary.wmts` = {matrix_sets (name → crs + matrix ids), formats, default_style} so
     the viewer compilers can build GetTile templates; refreshed on re-harvest.
   - `harvest_ogcapi {source_id}` — OGC API Features `/collections` (rel=next pagination) →
     datasets kind `vector` (bbox from extent.spatial, storageCrs as crs_native).
   - `harvest_stac {source_id}` — STAC `/collections` (paginated, capped at 500) → datasets kind
     `raster_ref` with `schema_summary.stac` = {license, temporal, assets, bbox_4326}; global
     extents keep extent_3014 NULL (the shared >10° bbox guard). Catalog visibility only until
     pgSTAC/TiTiler land.
   - `ingest_wfs {dataset_id, target_schema, table_name}` — `ogr2ogr -f PostgreSQL` from the WFS
     (`WFS:<url>` datasource, `-t_srs EPSG:3014 -nlt PROMOTE_TO_MULTI -lco GEOMETRY_NAME=geom
     -lco FID=fid --config OGR_WFS_PAGING_ALLOWED ON`), into `target_schema.table_name`; then
     update `catalog.datasets.ref_table`, `feature_count`, `schema_summary`; gist index; append
     provenance kind `load`. Connection via `DATABASE_URL_APP` (worker owns ref).
   - `ingest_ogcapi {dataset_id, target_schema, table_name}` — like ingest_wfs via GDAL's OAPIF
     driver (`OAPIF:<url>`, `OGR_OAPIF_PAGE_SIZE 1000`).
   - `ingest_file {path|url, table_name, target_schema}` — same via ogr2ogr from GDAL-readable file.
   - `ingest_pdf {dataset_id|url, title}` — download, pdfplumber text per page, ~1200-char chunks
     with 150 overlap, insert doc.documents + doc.chunks, embed chunks (task `document`).
   - `ingest_text {dataset_id|url, title}` — download (20 MB cap), strip HTML to visible text
     (stdlib HTMLParser), chunk like ingest_pdf, insert doc.documents + doc.chunks, embed;
     re-ingesting a URL REPLACES its prior document row (delete-then-insert on source_url).
   - `embed_catalog {}` — embed all `catalog.datasets` rows where `embedding IS NULL OR
     embedding_model <> $EMBED_MODEL` (text = title + description + keywords), and any unembedded
     doc.chunks; batch 32; set `embedding_model`.
   - `export {layers: [..], format, workspace_id, cite}` — `ogr2ogr -f <driver>` from PG to
     `/tmp/exp_<id>.<ext>` (gpkg|geojson|csv|parquet), upload to MinIO `exports/` via boto3-style
     client (use `minio` py lib), plus `<name>.citation.md` sidecar built from `app.provenance` +
     `catalog.sources` for the exported layers when `cite`. Result: `{object_key, sidecar_key}`.
     CSV gets `-lco GEOMETRY=AS_WKT`. GeoJSON in 4326 (`-t_srs EPSG:4326`), others native 3014.
   - `change_detect {area_wkt_3014, table_name, target_schema, concepts, collection_a,
     collection_b, threshold, min_area_m2, method}` — SAM 3 orthophoto change detection
     (architecture §7, method `mask_compare` only). STAC item search over the area per
     collection (public endpoints; items filtered to `spektraltyp in (rgb, rgbi)`), window
     grid in EPSG:3006 at 1008 px / 96 px overlap at the pair's coarsest GSD (≤ 128 tiles),
     per-vintage `gdal.BuildVRT` over `/vsicurl/` COG hrefs (Basic auth via GDAL config,
     never logged), per-window PNG → `POST {SAM3_URL}/segment` with the concepts, masks
     georeferenced via MEM datasets (bytes only — no numpy/gdal_array, whose ABI is broken
     in-container) and polygonized, then one PostGIS pass: per-vintage/concept union + dump,
     morphological opening at 2·GSD (misregistration slivers), IoU full-outer match →
     `appeared`/`disappeared`/`changed` (IoU < 0.80), min-area filter (default
     `15·(gsd/0.16)²` m²). Segmenter 5xx aborts the job (a failing backend must not yield a
     clean zero-candidate result); per-tile 4xx → coverage `error`. Writes
     `{table}` (concept, change_class, confidence_a/b, iou, area_m2, vintage_a/b,
     datetime_a/b, geom Polygon 3014) and `{table}_coverage` (tile_id, status
     `analyzed|missing_a|missing_b|error`, gsd_m, geom) — grants + `OWNER TO agent_ws`,
     provenance kind `change_detect` (model, prompts, items, thresholds in details).
     Existing output tables are dropped only when this job's own provenance row claims
     them (attempt-2 rerun); otherwise the job refuses. The output transaction runs with
     `SET LOCAL statement_timeout='15min'` (role default 120 s is too small for the diff SQL).
   - Authenticated sources: `LANTMATERIET_CREDENTIALS=user:password` applies Basic auth to every
     `*.lantmateriet.se` host; `GEODATA_HTTP_CREDENTIALS=host=user:password,...` per-host entries
     override it. Worker-env only (never in the catalog); ogr2ogr gets `GDAL_HTTP_USERPWD`
     scoped to its subprocess. Loads yielding 0 features carry a result `warning` (empty or
     server-side-broken upstream layers are not presented as clean results).
2. **Embed HTTP** (same process, FastAPI on :8100):
   - `POST /embed {"texts": [...], "task": "query"|"document"}` → `{"embeddings": [[256 floats]...]}`.
     sentence-transformers `SentenceTransformer(EMBED_MODEL, truncate_dim=256)`;
     `encode(texts, prompt_name="query"|"document", normalize_embeddings=True)`.
   - `GET /healthz` → `{"ok": true, "model_loaded": bool}`. Model loads lazily on first use in a
     thread; job loop must not block on model load.

On start, worker ensures the MinIO bucket exists. GDAL present in image (use
`ghcr.io/osgeo/gdal:ubuntu-small-*` base or apt `gdal-bin python3-gdal`).

## MCP server (FastMCP, stateful streamable HTTP, bearer auth)

Mounted at `/mcp`, served by `uvicorn` over `mcp.streamable_http_app()` wrapped in a pure-ASGI
`BearerAuthMiddleware`: requests to `/mcp*` without a known, enabled key in
`Authorization: Bearer <key>` get **401 + `WWW-Authenticate: Bearer`**; `/healthz` stays open.
Valid digests are cached 30 s, so disabling a key takes effect within that window.

On each tool call, resolve the principal from the **per-request** Starlette request
(`ctx.request_context.request.headers`) — not a middleware contextvar, which does not
propagate into tool handlers in stateful mode because the MCP session task is spawned once at
`initialize`. Hash the key, look up `app.api_keys`, then take that key's active
`app.workspaces` row (creating and activating `default` when there is none). The `ws_*` schema
is created lazily only when `layer`/`load op=inline` first writes. Seven tools; all return
JSON-serializable dicts.

0. `workspace(op='current', name=None, new_name=None)` — durable workspace management, scoped to
   the calling key:
   - `current {}` → active workspace name, schema, layer list.
   - `list {}` → all the key's workspaces with layer/map counts and timestamps.
   - `new {name}` → create + activate (name `^[a-z0-9][a-z0-9_-]{0,39}$`, max 20 per key).
   - `use {name}` → make it active (single-transaction flip; the partial unique index enforces
     one active workspace per key).
   - `rename {name, new_name}` → relabel only; `delete {name}` → `DROP SCHEMA … CASCADE` plus
     `app.layer_meta`/`app.map_views`/`app.workspaces` cleanup, witnessed by the `sql_drop`
     event trigger. `new` on an existing name switches to it instead (reply carries `created`).
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
     (added_by = session), enqueue harvest job for wfs/wms/wmts/ogcapi/stac; return
     `{source_id, job_id}`. **Idempotent on (kind, url)**: an already-registered endpoint is
     re-harvested, never duplicated — a forked catalog doubles search results and embedding cost.
     For kind `pdf`/`file`/`text`: also insert a `catalog.datasets` row immediately (kind
     `document`, or `vector` for file, external_id = url) and return its `dataset_id` so
     `ingest` can target it without a harvest step.
   - `embed {}` → enqueue an `embed_catalog` job; returns `{job_id}`. (Normally triggered after
     harvests/PDF ingests; idempotent — only missing/model-mismatched rows are embedded.)
   - `ingest {dataset_id, table_name=None, target='ref'|'workspace'}` — refuses a target table
     already claimed by a *different* dataset (ogr2ogr runs `-overwrite`, so a name collision
     would silently replace another dataset's data). → enqueue the job kind matching the
     source protocol: vectors → `ingest_wfs` (or `ingest_ogcapi` for ogcapi sources),
     documents → `ingest_pdf` (or `ingest_text` for text sources), file sources →
     `ingest_file`; raster_ref is not ingestable (reference on maps via `wms:<id>`).
     `workspace` targets the session's ws schema. Poll the job up to 8 s before returning
     (`status` in the reply either way).
   - `change_detect {area, concepts, collection_a, collection_b, table_name, threshold=0.5,
     min_area_m2=None, method='mask_compare'}` — enqueue a SAM 3 change-detection job (kind
     `change_detect`). `area`: layer ref (`ref.x`/`ws_….x`, envelope of its extent), bbox
     string `'xmin,ymin,xmax,ymax'` (3014), or WKT 3014; must be a non-empty areal geometry
     ≤ **2.0 km²**. `concepts`: 1–6 free-text noun phrases (each ≤ 80 chars). Collections
     are `catalog.datasets.external_id` values from a `stac` source; must exist and differ.
     Refuses if `{table}` or `{table}_coverage` already exists. `ensure_ws_schema` before
     enqueue; waits up to 8 s then returns the ingest-style `{job_id, status, note}` reply.
     Docstring frames results as screening candidates, never assertions.
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
   `SET LOCAL app.workspace_id`:
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

FastAPI on :8001. DB via `DATABASE_URL_APP` (read paths only, plus workspace-manager writes).

- `GET /healthz` → ok.
- `GET /` → redirect to `/workspaces`.
- **Workspace manager UI** (requires `VIEWER_SECRET`; 503 without it):
  - `GET /login`, `POST /login {key}` — verifies the sha256 digest against `app.api_keys`;
    on success sets a signed HttpOnly cookie carrying only the api_key **id** (7-day TTL,
    HMAC-SHA256 over `<id>.<expiry>` with `VIEWER_SECRET`). `POST /logout` clears it.
  - `GET /workspaces` — the key's workspaces with layer counts, map links (both renderers),
    created/last-used times, and the active badge. Redirects to `/login` when unauthenticated.
  - `POST /workspaces/action {action, workspace_id, new_name?, csrf}` — `activate|rename|delete`.
    Every action re-checks that the workspace belongs to the cookie's key (otherwise "unknown
    workspace") and requires the per-principal CSRF token; errors re-render the page with 400.
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
  - the backdrop is **per-renderer, not per-view**: MapLibre always emits the Carto Positron
    raster source `https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png` (attribution
    CARTO/OSM) — fast, CDN-cached, aligned in its EPSG:3857 world. `spec.basemap` is accepted
    by the `map` tool but not consulted by either interactive renderer (Origo always uses the
    official municipal WMS; see origo.json below). `wms:` refs remain usable as overlay
    *layers* (raster source with a WMS GetMap URL template, EPSG:3857).
  - each vector layer → GeoJSON source `/data/{schema}.{table}.geojson?view={view_id}&crs=4326`
    if feature_count ≤ 20 000 (or unknown), else vector-tile source
    `/tiles/{schema}.{table}/{z}/{x}/{y}.mvt?view={view_id}` (source-layer = table name).
  - style mapping: polygons → fill layer (fill, opacity 0.45 default) + line layer (stroke);
    lines → line layer (stroke/width); points → circle layer (circle px, fill). Defaults from
    app.layer_meta.style, overridden by the spec's per-layer style. `metadata` on the style
    carries `{view_id, version, extent_4326, legend: [...], popups: {...}}` for the page.
- `GET /v/{view_id}/origo.json` — Origo 2.10 config: `projectionCode` EPSG:3014 with
  `proj4Defs` for 3014 + 3006 (`urn:ogc:def:crs` aliases), `projectionExtent`/`extent`, metre
  `resolutions` ladder, initial `center`/`zoom` derived from the view extent, `featureinfoOptions`,
  `legend`/`home` controls, WMS layers via named `source` blocks (version 1.1.1), vector layers
  as `GEOJSON` with `source: /data/<ref>.geojson?view=…&crs=3014` and `projection: EPSG:3014`,
  named `styles` (array-of-arrays), and a `geodata` block (`view_id`, `version`, `title`,
  optional `note`) that Origo ignores and the page reads.
  Ordering contract: **overlays first, background layers last** — Origo draws the array
  top-down. Background layers must carry a `style` whose rule has an `image` (the legend's
  background switcher throws otherwise and the whole legend control disappears).
  The backdrop is **per-renderer, not per-view**: Origo **always** uses the official municipal
  WMS backdrop, resolved from the catalog by layer name (`ORIGO_WMS_BACKDROP`, default
  `Lantmateriet:topowebbkartan_nedtonad`) — the GeoServer reprojects it to EPSG:3014, where
  Web-Mercator XYZ tiles (Positron/OSM) cannot be aligned. `spec.basemap` is not consulted.
  If the backdrop layer is missing from the catalog, the view gets a `geodata.note`
  explaining it.
- The Origo page (`?renderer=origo`) fetches that config and boots Origo with it as an **inline
  object** (a full absolute URL is mangled by Origo's permalink parsing), passing
  `svgSpritePath: /static/origo/css/svg/` — without that override every toolbar icon 404s
  silently. It polls `geodata.version` and offers a reload button on change (OpenLayers has no
  style-diff equivalent to MapLibre's `setStyle({diff:true})`).
- `GET /wmsref/{dataset_id}?view=<view_id>&<WMS KVP>` — authenticated-WMS relay. Both
  compilers emit this instead of the direct upstream URL **only** when
  `netauth.userpwd_for(source url)` finds credentials (viewer carries a verbatim copy of the
  worker's `netauth.py` and receives the same credential env vars). Capability check: the view
  must exist and reference `wms:<dataset_id>`; `REQUEST` must be GetMap (case-insensitive,
  every occurrence) else 403; upstream query = source URL's own params overlaid by the client
  KVP minus `view`; Basic auth injected server-side, never logged; upstream non-200 → 502;
  `Cache-Control: private, max-age=3600`. Unauthenticated WMS output is byte-identical to before.
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
6. `/mcp` returns 401 without a valid bearer key; a reconnecting client with the same key
   lands in the same workspace with its layers intact.
7. Both renderers draw the same view: `/v/<id>` (MapLibre) and `/v/<id>?renderer=origo`
   (Origo, EPSG:3014, legend + feature-info popups).
8. Every register kind has a live connector, verified against the official sources in
   `data_sources.xlsx` (`scripts/connector_test.py`): all 18 named Sundsvall/Trafikverket WFS
   layers ingest, the GWC WMTS renders on a map, a text page lands in doc.chunks, and the
   authenticated Lantmäteriet STAC + WMS harvest with `LANTMATERIET_CREDENTIALS`.
9. Orthophoto change detection end to end (`scripts/change_detect_test.py`, segmenter running
   on the host): `load(op='change_detect')` over two Lantmäteriet T2 vintages writes the
   candidates + coverage tables into the workspace, and the map view renders them over the
   årsvisa ortofoto WMS through `/wmsref` (migration 003 applied).
