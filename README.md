# Geodata MCP v2

Municipal geodata platform for LLM-driven analysis (urban planning, municipal errands,
auditing), built per [`geodata-mcp-architecture.md`](geodata-mcp-architecture.md) with
[`CONTRACTS.md`](CONTRACTS.md) as the binding implementation spec. Pilot data:
**Sundsvall kommun** (GovTech Pilot 3), CRS **EPSG:3014** (SWEREF 99 17 15).

The architectural core: loaders write only to PostGIS, map clients read only from serving
endpoints off PostGIS, and the MCP server is a control plane that never sits in the data path.

```
loaders ──▶ PostGIS ──▶ /data /tiles endpoints ──▶ MapLibre page / Origo config / QGIS (GPKG)
               ▲
           MCP server (6 tools, streamable HTTP)
```

## Quickstart

```sh
cp .env.example .env                # then edit: every password there is a placeholder
docker compose up -d --build        # 6 containers: postgres, minio, mcp, worker, viewer, caddy
uv venv && uv pip install "mcp>=1.9" httpx
.venv/bin/python scripts/bootstrap_sundsvall.py   # register + harvest + ingest + embed (~10 min)
.venv/bin/python scripts/e2e_test.py              # full agent flow; prints a map URL
.venv/bin/python scripts/security_test.py         # attack regressions (see Security boundaries)
```

- MCP endpoint (streamable HTTP): `http://localhost:8080/mcp` — add to Claude with
  `claude mcp add --transport http geodata http://localhost:8080/mcp`
- Map views: `http://localhost:8080/v/<view_id>` (capability URL; ETag-polling live updates)
- Postgres: `localhost:5433` (`postgres` / `geodata_dev`), MinIO console: `localhost:9001`

## The six tools

| tool | what it does |
|------|--------------|
| `search` | hybrid trigram + EmbeddingGemma vector search over the catalog (2 100+ datasets) and document chunks; `id=` returns full schema + provenance |
| `load` | register sources (WFS/WMS/file/PDF/inline), harvest capabilities into the catalog, ingest datasets into `ref`/workspace via job queue; `op='embed'` refreshes embeddings |
| `query` | read-only SQL as `agent_ro` (PostGIS 3.5 + pgvector, 15 s timeout, 1 000-row cap); every call logged with a `query_id` and server-extracted referenced tables |
| `layer` | the only write path: CTAS into the session workspace, per-row updates, style/notes; every op appends to the append-only `app.provenance` ledger |
| `map` | upsert a renderer-agnostic map view; returns a capability URL; open pages pick up changes in ≤ 5 s via ETag polling |
| `export` | GPKG/GeoJSON/CSV/Parquet via ogr2ogr → MinIO presigned URL, with a citation sidecar generated from the provenance ledger |

## Provenance model (the auditing story)

- Reads: `app.query_log` records every `query` call (SQL, referenced tables from the
  planner, session, duration) and returns its `query_id` to the agent.
- Writes: only `layer`/`load` create state; each appends `app.provenance` (SQL text, input
  tables extracted from EXPLAIN — never self-reported, session, job id).
- Backstop: `ddl_command_end`/`sql_drop` event triggers record ANY DDL touching `ws_*`/`ref`
  schemas, whatever code path caused it. pgAudit logs the complete statement stream beneath.
- `export` bundles a human-readable citation sidecar walking the lineage chain to sources
  with URL + license.

## Security boundaries (what is and is not guaranteed)

Enforced today, with regression tests in `scripts/security_test.py`:

- `query` cannot write. It runs as `agent_ro` with `SET TRANSACTION READ ONLY` re-asserted
  per transaction, plus a statement-kind guard, 15 s timeout and row cap. Pooled connections
  are scrubbed on release so no statement can leave session state behind for the next caller.
- `agent_ws` may only CREATE inside the calling session's `ws_<hash>` schema.
- The MCP session id never reaches the database: only its SHA-256 digest is stored, so a
  leaked `app.provenance`/`app.jobs` row is not a usable credential. `app.sessions` is not
  readable by the agent roles at all (grants in `app` are per table, never schema-wide).
- A session cannot overwrite or read the owner of another session's **map view**, and
  `map(op='get')` never returns a session identifier — map URLs are shareable by design.
- Ingest refuses to overwrite a `ref` table already claimed by a different dataset.

**Not** guaranteed, by design — say so before a municipal deployment:

- **Workspaces are namespacing, not tenancy.** All agent SQL runs as one shared read-only
  role, so any session can *read* any workspace or `ref` table through `query`. Writes are
  session-scoped; reads are not. Per-user isolation means real auth plus either RLS or a
  role per user (§11 of the architecture keeps RLS as the documented fallback).
- **Map views are capability URLs.** Knowing the link is the permission (§5.2). Real
  authentication is a later addition at the Caddy chokepoint.
- All credentials come from `.env` (gitignored; `.env.example` is the committed template with
  placeholders). Nothing starts without it. Database role passwords are only applied on first
  start — changing them later needs `ALTER ROLE` or a `docker compose down -v`.
- MinIO is exposed directly on `:9000` rather than through Caddy, because the public endpoint
  is part of the presigned-URL signature. Put it behind TLS before any non-local use.

Session identity comes from the MCP `mcp-session-id` header, which the transport rejects if
unknown; workspaces are created lazily and survive reconnects.

## Data loaded (Sundsvall pilot)

Sources: `karta.sundsvall.se/geoserver` (WFS: 885 feature types harvested; WMS: 1 203 raster
reference layers incl. open ortofoto vintages), `isyroad.isy.se` (Trafikverket WFS), plan-archive
PDFs. Twelve layers ingested into `ref`: adressplats (36 247 — also drives `app.geocode()`),
fastighetsgrans (101 226), byggnader (85 499), detaljplan_gallande, anvandningsbestammelser,
strandskydd, naturreservat, kulturmiljoprogram, riksintresse_kulturmiljo, byggnadsminnen,
vaghallare, ovriga_byggnader. Lantmäteriet direct services from `data_sources.xlsx` require the
`suns0011` account and are catalogued as auth-required, not loaded.

Notes on source honesty: the WFS serves `Strandskydd_yta` as one dissolved multipolygon and
`td_ovrigbyggnad_yta` empty — the loaders reproduce the source faithfully rather than papering
over it. The example plan PDFs are scanned (no text layer); they are ingested as documents with
zero chunks and a warning, because OCR is deferred by decision (§ model stack).

## Model stack (v1, per the user decision: EmbeddingGemma only)

- **EmbeddingGemma-300M** via the ungated mirror `unsloth/embeddinggemma-300m`
  (`google/embeddinggemma-300m` is HF-gated; the mirror is a full copy incl.
  sentence-transformers config), truncated to 256 dims, CPU, inside the worker container.
  Serves both document/catalog embedding (jobs) and query embedding (`POST worker:8100/embed`).
  `embedding_model` is stored on every embedded row so model swaps are detectable.
- Deferred by decision: SAM 3 change detection (§7 of the architecture), LightOnOCR for
  scanned plans, rerankers. The job queue, STAC slot, and doc pipeline are in place for them.

## Deferred by decision (documented upgrade paths)

- **OGC services** (Martin / TiPg / TiTiler-pgSTAC) — the thin `/data` + `/tiles` endpoint is
  the v1 seam; compilers repoint when the swap happens (§5.3).
- **pgSTAC + orthophoto change detection** — additive milestone (§7); WMS ortofoto reference
  layers already flow through map specs today.
- **Origo interactive page** — v1 ships the compiler (`/v/<id>/origo.json`, EPSG:3014 config
  with proj4 def + GeoJSON/WMS layers); pointing an Origo deployment at it is config-level.
- **Auth** — capability URLs now; signed tokens at the Caddy chokepoint later (§5.2).
- **PgBouncer / K8s** — single-host compose now; all services stateless, state only in
  Postgres + MinIO (§9).

## Repository layout

```
CONTRACTS.md               implementation contract (binding)
geodata-mcp-architecture.md  target architecture (background)
docker-compose.yml         the whole system, one command
db/                        Postgres 17 image (PGDG postgis/pgvector/pgaudit) + init SQL
services/mcp/              FastMCP server — 6 tools
services/worker/           job runner: WFS/WMS harvest, ogr2ogr ingest, PDF, embeddings, export
services/viewer/           map pages + style compilers + /data + /tiles endpoints
deploy/Caddyfile           one-origin reverse proxy
scripts/                   bootstrap_sundsvall.py, e2e_test.py, security_test.py, mcp_client.py
```

## Operational notes

- arm64 (Apple Silicon) friendly: Postgres is built from `postgres:17-bookworm` + PGDG
  packages because `postgis/postgis` publishes no arm64; the worker uses
  `ghcr.io/osgeo/gdal:ubuntu-full` (multi-arch).
- First embedding run downloads ~1.2 GB of weights into the `hf-cache` volume; the first
  `/embed` call after a worker restart can exceed the search tool's 6 s embed timeout, in
  which case search falls back to trigram-only for that call (`embedding_used: false`).
- GML curve geometries from WFS are linearized at ingest (`-nlt CONVERT_TO_LINEAR`) —
  `ST_AsMVTGeom` and most GIS consumers cannot handle MULTICURVE/MULTISURFACE.
- Loads are idempotent where it matters: re-registering the same endpoint re-harvests the
  existing source instead of forking the catalog, and ingest refuses to silently overwrite a
  `ref` table claimed by another dataset. A worker restart requeues jobs left mid-flight.
- Map pages keep a `preserveDrawingBuffer` WebGL context so tab captures and future
  server-side PNG snapshots see the rendered map.
