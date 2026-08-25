# Geodata MCP v2 — Target Architecture

*Re-architecture proposal for the municipal geodata MCP server (urban planning, municipal errands, auditing). Prepared 2026-08-03.*

---

## 1. Design goals and constraints

From the current prototype (13 MCP tools, session-scoped DuckDB + Spatial, static `catalog.json`, single server-rendered map) the redesign must deliver:

1. **PostgreSQL/PostGIS as the data layer** (hard requirement, replacing DuckDB).
2. **A dynamic, multimodal source layer** — WFS, WMS/WMTS, files, PDFs, free text, orthophoto imagery — replacing the static `catalog.json`, while keeping the philosophy that the agent does as much as possible itself (web search, reasoning) and the server only contributes *trusted* sources.
3. **Map-renderer interoperability** — Origo and MapLibre today, others later — with the map opened in its own web page via a URL returned to the client.
4. **Minimal MCP tool surface** — as few and as simple tools as possible.
5. **Containerized now, scalable later** — clean Docker containers today; the design must not block scaling to ~1 000 parallel users if the continuation contract lands, but no scaling infrastructure is built yet.
6. **Orthophoto change detection** as a supported workload.
7. **Export to standard GIS applications** (QGIS, ArcGIS, GeoPackage-based workflows).

The single architectural idea that buys almost all of this: **stop connecting the three components to each other directly, and connect each of them to PostGIS instead.** Loaders only write to PostGIS. Map clients only read from serving endpoints published off PostGIS — a thin GeoJSON/MVT endpoint in v1; standard OGC services remain the documented upgrade, deferred by decision (§5). The MCP server orchestrates and never sits in the data path. Every pairwise coupling in the prototype (loader→tool schema, tool→DuckDB session, map→render pipeline) becomes a coupling to one shared contract, which is what makes the components swappable.

```
   loaders ──▶ PostGIS ──▶ serving endpoints ──▶ any map client / any GIS app
                  ▲
              MCP server (control plane only)
```

## 2. Component overview

The inventory — everything except Postgres and the object store is stateless:

| # | Service | Role | Suggested implementation |
|---|---------|------|--------------------------|
| 1 | **MCP server** | Control plane: the agent-facing tool surface (streamable HTTP) | Python (FastMCP) or TypeScript SDK |
| 2 | **Ingestion workers** | Execute load jobs: WFS harvest, file/PDF/text extraction, COG ingest | Python + GDAL/OGR, queue-driven |
| 3 | **Data endpoint** | Thin read API off PostGIS: GeoJSON + MVT for map clients (OGC services deferred, §5.3) | Part of the viewer service; TiTiler-pgSTAC joins with the imagery milestone |
| 4 | **Viewer service** | Serves the map web page; compiles map specs to renderer configs | Thin stateless web app |
| 5 | **Change-detection workers** | Async raster analytics over orthophoto COGs | Python, GPU-optional |
| — | **PostGIS** | Single source of truth: catalog, vector data, workspaces, STAC, map specs | Postgres 17 + PostGIS + pgvector + pgSTAC |
| — | **Object store** | COGs, exports, large artifacts | S3-compatible (MinIO on-prem / cloud S3) |

See `diagram-1-components.mermaid` for the full component diagram.

## 3. The data layer (PostGIS)

### 3.1 Schema model

```
catalog.*      -- source & dataset registry (replaces catalog.json), pgvector embeddings
ref.*          -- shared reference datasets: loaded once, read-only for everyone
                  (DeSO, Stadskartan, addresses, admin boundaries, …)
ws_<session>.* -- per-session workspaces: derived layers, agent-created tables
doc.*          -- extracted documents: text chunks, tables, georeferences, embeddings
pgstac.*       -- STAC catalog for imagery (orthophoto vintages as COG references)
app.*          -- map_views, jobs, sessions, provenance ledger
```

**Tenancy model — schema-per-session (decided).** Shared reference data + schema-per-session workspaces. Users mostly *read* the same `ref` tables; each session's private state is a lightweight schema (`ws_a1b2.*`) created lazily on first write and garbage-collected by TTL. This replaces DuckDB's session isolation without duplicating data per user. Workspaces are durable across reconnects (an improvement over the prototype's in-memory sessions), and the `checkpoint` concept is retired: durability plus `export` covers it.

Why schemas and not row-level security: isolation rests on ordinary grants (nothing to misconfigure per table — an RLS policy mistake silently leaks rows across sessions), ownership is visible in every table's address, cleanup is a single `DROP SCHEMA … CASCADE` instead of bulk DELETEs, and provenance stays legible because each derived table is a distinct named object. RLS remains the fallback only if long-lived per-*user* tenancy on shared tables is ever needed.

**Roles:** the agent's SQL runs as `agent_ro` (read-everything, statement timeout, row limit, no DDL/DML) except through the `layer` tool, which runs a validated `CREATE TABLE ws_x.… AS SELECT …` as `agent_ws` (write access restricted to that session's workspace schema). This gives the same safety envelope as the prototype's read-only DuckDB wrapper while making every "derive/edit/classify" operation plain SQL.

**CRS policy:** store everything in **SWEREF 99 17 15 (EPSG:3014)**. `ST_Transform` happens only at the serving edge: native 3014 for analysis, export, and Origo; 4326/3857 for MapLibre GeoJSON/MVT. Dual-CRS is handled inside the data endpoint, never scattered across tools (see §5.1).

### 3.2 The dynamic catalog

`catalog.sources` and `catalog.datasets` replace `catalog.json`:

- **sources**: a registered endpoint or corpus — type ∈ {`wfs`, `wms`, `wmts`, `ogcapi`, `file`, `pdf`, `text`, `stac`, `inline`}, connection info, trust/license metadata, refresh policy.
- **datasets**: the discoverable units inside a source (WFS feature types, PDF documents, COG collections), each with title, description, schema summary, extent, and a pgvector embedding of its description.

Registering a WFS endpoint triggers a *harvest* job that reads `GetCapabilities` and inserts one dataset row per feature type — the catalog grows dynamically instead of being hand-edited JSON. Search combines trigram fuzzy matching with vector similarity, so "var finns bullerdata?" finds a noise-mapping WFS layer even without keyword overlap. PDFs and text corpora appear in the same catalog with `kind = document`, so one search surface covers tables, imagery, and documents.

## 4. The source layer (loaders)

One ingestion framework, plugin connectors, one output target (PostGIS), all asynchronous.

| Source type | Connector behaviour |
|-------------|--------------------|
| WFS / OGC API Features | `ogr2ogr`-based harvest into `ref` (admin-registered, shared) or `ws_x` (agent-initiated, private). GDAL handles paging, CRS, schema mapping. |
| WMS / WMTS | **Not loaded.** Registered as a *reference layer*: catalog entry carrying the endpoint + layer name, passed through to map configs. Imagery-as-a-service stays where it is. |
| Files (GPKG, Shapefile, GeoJSON, CSV, XLSX) | GDAL/OGR import with schema inference; same path as WFS after fetch. |
| PDF / text | Extraction pipeline: text + tables + any georeferenceable content → `doc.*` with embeddings; optionally structured extraction into a workspace table ("pull the parking-count table out of this PDF into SQL"). |
| Orthophotos / rasters | Convert to COG → object store; STAC item in pgstac with acquisition datetime, footprint, resolution. |
| Inline | Agent-provided rows (as today), written to the session workspace with mandatory `source` attribution. |

Loads are **jobs**: the MCP `load` tool enqueues and returns a job id immediately; the agent polls (or the tool blocks briefly for fast loads). Queue lives in Postgres (e.g. River/pg-boss/procrastinate) — no Redis/RabbitMQ dependency, one fewer stateful service, and job state is transactional with the data it produces.

Provenance is non-negotiable for the auditing use case: every dataset row and every workspace table records its lineage (source id, load job, SQL that derived it, timestamp), append-only in `app.provenance`. The prototype's `sources`/`cite` behaviour becomes a database property instead of a tool feature.

## 5. Serving data to maps — thin endpoint now, OGC services later

Map clients cannot read Postgres directly; something must serve layers over HTTP. Per the decision to hold off on OGC infrastructure as the internal seam, v1 keeps this as small as possible while preserving the upgrade path.

### 5.1 v1: one thin data endpoint

The viewer service owns a minimal read-only API straight off Postgres:

- `/data/{layer}.geojson` — `ST_AsGeoJSON` with row caps; native EPSG:3014 for Origo, `ST_Transform`ed to 4326/3857 for MapLibre,
- `/tiles/{layer}/{z}/{x}/{y}.mvt` — one parameterized `ST_AsMVT` query, for layers too large to ship as a single GeoJSON.

Both Origo and MapLibre natively consume GeoJSON and vector tiles, so map interoperability does **not** depend on OGC infrastructure — this is on the order of a hundred lines of code and zero extra containers. Registered WMS/WMTS reference layers pass straight through to map configs untouched. [TiTiler-pgSTAC](https://github.com/stac-utils/titiler-pgstac) joins later with the orthophoto milestone (§7), since browsers need a raster tiler to display COGs.

### 5.2 Access control (deliberately minimal for now)

All services sit behind one reverse proxy (needed anyway so browser, tiles, and features share an origin). Map views get long, unguessable IDs — capability URLs: knowing the link is the permission, which matches how the agent hands links to users today. Reference layers are open data and served plainly. Real authentication (signed, expiring tokens validated at the proxy) is a later config-level addition at the same chokepoint, not an architectural change — the seam is already in place because everything routes through the proxy.

### 5.3 The deferred OGC upgrade

When live GIS interop or tile volume eventually calls for it, the thin endpoint swaps for off-the-shelf standard services behind the *same proxy paths* — [Martin](https://maplibre.org/martin/) (MVT, Rust, built for heavy traffic), [TiPg](https://developmentseed.org/tipg/)/[pg_featureserv](https://github.com/CrunchyData/pg_featureserv) (OGC API Features, which QGIS consumes live), TiTiler-pgSTAC (rasters) — and the renderer compilers simply repoint. Held off by decision, not designed out: nothing in v1 assumes the thin endpoint is permanent, and the map-spec contract (§6) never mentions which service fills a URL.

### 5.4 Export

`export` produces GPKG / GeoJSON / CSV / (Geo)Parquet server-side (ogr2ogr from PostGIS), drops the file in the object store, and returns a signed URL — with a provenance/citation sidecar generated from `app.provenance`. In v1 this file route *is* the QGIS/ArcGIS story; the deferred OGC endpoints later turn "export to my GIS" into "add this URL in QGIS".

## 6. The map layer

### 6.1 Map state as data: the map spec

The agent never touches renderer configuration. It writes a **map view** — a small renderer-agnostic JSON document in `app.map_views`:

```jsonc
{
  "view_id": "v_8f3a…",
  "title": "Bullernivåer vs. planerade förskolor",
  "extent_3014": [143000, 6543000, 155000, 6553000],
  "basemap": "positron",          // or a registered WMTS reference layer id
  "layers": [
    {"ref": "ws_x.noise_zones",   "style": {"fill": "#e31a1c", "opacity": 0.5},
     "popup": ["ldén", "källa"], "visible": true},
    {"ref": "ref.forskolor",      "style": {"circle": 6, "color": "#1f78b4"}},
    {"ref": "wms:ortofoto-2025",  "visible": false}
  ],
  "legend": true
}
```

The spec is the third contract of the system (after the catalog schema and the OGC endpoints). It is deliberately small: layers-by-reference, cartographic hints, extent, basemap. Anything a specific renderer can't express is dropped gracefully.

### 6.2 The viewer service

A stateless web app at `https://maps.example.se/v/{view_id}?renderer=maplibre|origo`:

1. Reads the map view (validating the signed token),
2. Runs the **renderer compiler** for the requested client — `map_view → MapLibre style JSON` or `map_view → Origo JSON config` — pointing layers at the data endpoint (GeoJSON/MVT in 3857 for MapLibre; GeoJSON in native 3014 plus WMS pass-through for Origo, which consumes WMS, WFS, GeoJSON and vector tiles per its [JSON-configured layer model](https://origo-map.github.io/origo-documentation/latest/)),
3. Serves the page, which then keeps itself current via **ETag polling (decided)**: the ETag header is a version fingerprint of the view spec, and every few seconds the page sends `GET /v/{view_id}` with `If-None-Match: <last fingerprint>`. Unchanged → `304 Not Modified`, a few bytes. Changed (the agent updated the view) → the full new spec returns and the map re-renders. A near-free heartbeat with a few seconds' refresh latency, zero realtime infrastructure, and nothing that breaks when service replicas multiply later.

Adding a third renderer later (OpenLayers, ArcGIS JS, Kepler) = writing one compiler. Nothing else changes.

### 6.3 Feeding the map back to the agent

The MCP `map` tool returns `{url, view_id}` for the client to open in a separate window (as required — no injection into the client UI), plus, on request, a **server-rendered PNG snapshot** (maplibre-native headless, or the prototype's existing static renderer) so the LLM can inspect what the user sees. Data summaries continue to flow to the client through `query` — the map is presentation, never the data channel.

## 7. Orthophoto change detection

Change detection is deliberately *not* an architectural special case — it is one worker type whose inputs and outputs already exist in the system. And it is treated honestly for what it is at municipal orthophoto resolution: **an approximate screening instrument that produces candidates for human review, never assertions.**

### 7.1 The job contract (how the agent drives it)

The agent initiates a run through `analyze(op:"run", id:"change_detect", …)` with four parameters, each of which it can derive itself:

- **area** — any workspace geometry or bbox (e.g. "areas without building permits" from a prior `query`),
- **vintages** — two acquisitions picked by querying pgSTAC (dates, resolution, coverage are ordinary SQL),
- **concepts** — free-text prompts for SAM 3's promptable concept segmentation ("byggnad", "uppfart", "pool", "upplag"). This is what makes the capability *agent-driven*: the LLM chooses what to look for per errand, no per-class model training,
- **method** — `mask_compare` (SAM 3, default), `raster_diff` (cheap screening), or `dsm_diff` (see below).

### 7.2 The worker pipeline (mask_compare)

Window both COGs over the area → tile at native GSD → run [SAM 3](https://blog.roboflow.com/what-is-sam3/) with the concept prompts on each vintage independently → polygonize masks with per-mask confidence → match masks across vintages by IoU → classify each as *appeared / disappeared / changed* → filter by a minimum-area threshold tied to the imagery's GSD (at 0.16 m/px, nothing under ~15 m² is reportable) → write a normal workspace layer with `concept, change_class, confidence_a, confidence_b, iou, area_m2`, plus per-tile **coverage records** so "no change found" is distinguishable from "not analyzed". SAM 3 is ~840 M parameters, runs on a 16 GB GPU (~3.4 GB weights) — one worker container, GPU-optional but strongly preferred.

### 7.3 Guardrails for an unreliable signal

- **Candidates, not conclusions**: every output row carries confidence and method; the provenance ledger records model version, prompts, and vintage pair.
- **Misregistration**: orthos misalign by pixels; the worker estimates per-tile offset and suppresses sliver changes below the registration error.
- **Season/lighting flags**: STAC metadata flags cross-season pairs (deciduous shadow ≠ demolition).
- **The robust upgrade is height, not pixels**: where Lantmäteriet's ytmodell (photogrammetric DSM) is available for both vintages, `dsm_diff` gives the physically reliable building signal, and SAM 3 masks provide the *semantics* on top. Worth designing in from the start as the credible path for auditing use.
- **Agent-in-the-loop verification**: results are an ordinary layer, so the agent immediately `query`s them, builds a `map` view with both vintages as swipeable raster layers + flagged polygons, pulls PNG snapshots zoomed to individual candidates to inspect before/after with its own vision, and re-runs with refined concepts on subareas. The screening loop is the product; the model is just one stage in it.

Audit workflow example: "show me buildings that appeared between the 2023 and 2025 orthophotos in areas without building permits" = change-detection job → `query` joining change polygons against the permits layer → `map` view with swipe + flagged candidates → agent inspects snapshots → shortlist for the human case handler.

One flag: SAM 3 ships under a **custom Meta license** (SAM 1/2 were Apache-2.0; 3 is not) — have it reviewed before it goes into a municipal contract deliverable.

## 8. MCP tool surface: 13 → 6

Guiding rule: the agent's intelligence lives in the client; tools are dumb, orthogonal capabilities. SQL is the workhorse — everything the prototype did with `derive`, `edit_field`, `write_attributes`, and `inspect` is expressible as SQL against Postgres, so those tools disappear rather than getting redesigned.

| v2 tool | Purpose | Absorbs (v1) |
|---------|---------|--------------|
| `search` | Fuzzy + semantic search over the catalog (datasets, sources, documents); `id` → full schema & provenance | `catalog`, `sources` |
| `load` | Register a source and/or ingest a dataset (any connector type, incl. inline rows and change-detection jobs); returns job id + status; `op:"status"` polls | `load`, part of `derive` |
| `query` | Read-only SQL (PostGIS + pgvector over doc chunks); result rows/summaries back to the client | `execute_sql`, `inspect`, `geocode`*, `derive`, `edit_field`, `write_attributes` |
| `layer` | Materialize a SELECT as a workspace layer; set style hints, notes, visibility; rename/drop | `layer`, remainder of `derive`/`edit_field` |
| `map` | Upsert a map view; returns `{url, view_id}`, optional PNG snapshot | `render_map` |
| `export` | GPKG/GeoJSON/CSV/Parquet + provenance sidecar → signed URL | `export` |

\* Geocoding becomes SQL functions (`app.geocode(text)`, `app.reverse_geocode(geom)`) callable through `query` — trigram search over the loaded address layer, no separate tool. `checkpoint` is retired (durable workspaces + export). If a seventh tool earns its place, it's a standalone `jobs` tool — but `load(op:"status")` covers it.

### 8.1 How provenance survives the tool collapse

Collapsing `derive`/`edit_field`/`write_attributes`/`inspect` into SQL raises the obvious question: free-form SQL sounds like the end of lineage tracking. It isn't, because of one design rule: **reads need no lineage, and writes only happen through one tool.**

- `query` runs as a read-only role — it *cannot* create or modify persistent state, so there is nothing to track beyond which sources informed an answer (the tool annotates results with the source layers touched, for citations).
- New data is only ever *born* through `layer`. Its operations — `create` (CTAS from a SELECT), `update` (per-row writes via `UPDATE … FROM (VALUES …)`, the `write_attributes` successor, with the values dict logged), `drop`, `rename` — each append a record to `app.provenance`: the exact SQL text, the input tables **extracted server-side from Postgres's query parse** (never self-reported by the agent, so it cannot be wrong or forged), session, and timestamp. Lineage is therefore a *chain*: every workspace table points at the statements and inputs that produced it, all the way back to catalog sources and their load jobs.
- `inspect`'s operations (sampling, pagination, spatial "what's here") are plain SELECTs through `query` — reads, so trivially safe.
- For the auditing use case, Postgres adds something DuckDB couldn't: **pgAudit** (or `log_statement`) records every statement the agent ever executed, giving a complete audit log underneath the curated provenance ledger.

**Database monitors make this enforced, not promised.** The legacy prototype's known gap is that SQL-only operations escape provenance — the tool layer records what `load`/`derive` did, but nothing tracks what a free SQL query actually touched, and the numbers the agent quotes to a user are exactly the outputs with no lineage. v2 closes this at the database level, in three layers:

1. **Read logging with `query_id`**: every `query` call is logged (SQL text, referenced tables extracted from Postgres's parse, session, timestamp) and returns its `query_id` with the results. An answer quoted to a user is traceable to the exact statement and inputs — SQL-only operations are now *inside* provenance, not outside it.
2. **Event triggers as the write backstop**: `ddl_command_end`/`sql_drop` event triggers scoped to `ws_*` schemas append to `app.provenance` on any table creation/alteration/drop — even if some future code path bypasses the `layer` tool. Per-transaction `SET LOCAL app.session_id / app.tool_call_id` (pool-safe) stamps every trigger record with the MCP call that caused it.
3. **pgAudit underneath**: the complete statement log, independent of application code.

So the collapse is safe *because* mutating SQL is unavailable through `query`, the one write path is the provenance chokepoint, and the database itself witnesses everything regardless.

Every tool is a thin wrapper over state in Postgres. That keeps the MCP server itself stateless — which is also where the MCP spec is heading (the [2026 MCP roadmap](https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/) puts stateless streamable-HTTP at the top of the transport work) — and is precisely what leaves the scaling door open (§9).

## 9. Deployment: containerized now, scalable when needed

### 9.1 Today: one host, Docker Compose

The deliverable for now is a single `docker-compose.yml` that brings up the whole system on one machine: postgres (+ PostGIS, pgvector, pgSTAC), the MCP server, one ingestion worker, the viewer (with its data endpoint), MinIO, and a reverse proxy (Caddy/nginx) giving everything one origin. Six containers, one command, no orchestrator; TiTiler and the change-detection worker join with the imagery milestone. This is the dev, demo, and initial-production environment.

What matters for the future contract is not any scaling infrastructure but a **discipline** the design already enforces: every service is a stateless container; all state lives in Postgres or the object store; configuration comes from environment variables; services find each other by hostname. Hold that line and "scale up" later means running more copies of the same images behind a load balancer — a deployment change, not a rewrite. Break it (state in process memory, files on local disk) and no orchestrator will save you.

Two cheap habits worth keeping from day one, because they're painful to retrofit: **PgBouncer** between services and Postgres (connection pooling — the first thing that breaks under concurrency), and **statement timeouts + row caps** on every agent-facing query path (already required for safety anyway).

### 9.2 Later, if the continuation contract lands: Kubernetes

When real scale arrives, the same images move onto Kubernetes — for a Swedish public-sector product the likely constraint is data residency (Schrems-II caution), and K8s runs identically on sovereign clouds (Safespring, Cleura, Binero), on-prem municipal infrastructure, or a hyperscaler if permitted. The upgrade path, none of which needs to be designed in detail now: Postgres under the [CloudNativePG](https://cloudnative-pg.io/) operator with read replicas for the serving path; autoscaling on the stateless tier (no session affinity needed — no service holds session state); workers scaled on queue depth, change-detection on GPU nodes; an nginx/CDN cache in front of tile endpoints (tile fan-out during map panning, not agent SQL, is where load actually concentrates — agent tool calls are bursty and think-time dominated).

Sequence and deployment diagrams: `diagram-2-sequence.mermaid`, `diagram-3-deployment.mermaid`.

## 10. Migration path from the prototype

1. **Stand up PostGIS + the schema model**; port the 65 datasets by re-running loads through the new WFS/file connectors (the loaders are the migration tool — if they can't reproduce the current catalog, they aren't general enough yet).
2. **Port `execute_sql` → `query`** (DuckDB spatial SQL → PostGIS is mostly mechanical: `ST_*` names largely match; rewrite the few DuckDB-isms).
3. **Build the data endpoint against `ref`** (GeoJSON + MVT) and verify the same layers open cleanly in QGIS via an `export` GPKG — proving serving and export before any map UI exists.
4. **Build the map-spec + viewer with the MapLibre compiler first** (simplest), then the Origo compiler.
5. **Collapse the tool surface** to the six tools; keep the old server running side-by-side until parity.
6. **Add pgSTAC + TiTiler + the change-detection worker** last — it's additive.

## 11. Design decisions worth recording (and their trade-offs)

- **Thin serving endpoint now; OGC services held off by decision.** v1 serves GeoJSON/MVT from ~a hundred lines inside the viewer; Martin/TiPg/TiTiler remain the documented swap-in when live GIS interop or tile volume demands it. What makes maps swappable in the meantime is the map-spec + compiler contract, which never cares which service fills a URL.
- **Postgres as the only stateful service** (queue in Postgres, sessions in Postgres, STAC in Postgres, map specs in Postgres). Max simplicity per your requirement; the cost is that Postgres must be operated well — hence the operator + pooling emphasis.
- **Schema-per-session workspaces** over row-level security: simpler to reason about, trivially garbage-collected, matches the agent's mental model ("my tables"). Cost: schema count churn at high concurrency — mitigated by lazy creation and TTL cleanup; RLS remains an option if you later need per-*user* long-lived tenancy instead of per-session.
- **Six tools with SQL as the workhorse** trades a little tool-level guidance for a lot of generality. The prototype's `derive`-style verbs can return as *documentation* (prompt examples in tool descriptions) rather than as API surface.
- **ETag-polling map refresh** over websockets: refresh latency of a few seconds in exchange for zero realtime infrastructure — and nothing that breaks when replicas multiply later.
- **Capability-URL map access now, token auth later**: unguessable view IDs behind the one reverse proxy; real authentication becomes a proxy-level addition when a customer requires it.

## 12. Model stack

Constraint: models must be Western-origin (no Qwen-derived weights — which quietly eliminates several leading open embedders/rerankers: jina-embeddings-v4, nomic-embed-multimodal, and mxbai-rerank-v2 are all Qwen-based) and must run locally.

| Role | Recommendation | Notes / alternatives |
|------|----------------|----------------------|
| Text embeddings (catalog + doc chunks) | **[EmbeddingGemma-300M](https://huggingface.co/google/embeddinggemma-300m)** (Google, open weights) | 100+ languages incl. Swedish, Matryoshka dims 768→128 (pick 256 for pgvector index size), runs on CPU. Note: "Gemini Embedding" proper is API-only — EmbeddingGemma is its local sibling. Heavier/stronger alternative: NVIDIA [llama-nemotron-embed-1b-v2](https://build.nvidia.com/nvidia/llama-nemotron-embed-1b-v2/modelcard) or [Nemotron 3 Embed](https://huggingface.co/blog/nvidia/nemotron-3-embed-wins-rteb) (open weights, GPU). Solid fallback: multilingual-e5-large (Microsoft). |
| Reranker | **None in v1** | At catalog scale (hundreds–low thousands of datasets), hybrid trigram+vector top-20 → *the client LLM is the reranker* (it reads descriptions and picks). Google ships no local reranker (Vertex API only). If the document-chunk corpus grows large: NVIDIA [llama-nemotron-rerank-1b-v2](https://huggingface.co/nvidia/llama-nemotron-rerank-1b-v2) (open weights, local) drops in behind `search` with no API change. |
| Imagery segmentation | **SAM 3** (Meta) | Text-prompted concept segmentation drives §7. Custom Meta license — legal review before contract use. |
| PDF / scan OCR | **[LightOnOCR-2-1B](https://huggingface.co/lightonai/LightOnOCR-2-1B)** (LightOn, FR — open weights) | 1B end-to-end multilingual OCR VLM: page images → clean markdown incl. tables; light enough to run per-document at ingest on modest GPU. Its output is what gets chunked and embedded. |
| Geocoding | No model | Trigram SQL over the address layer. |

Multimodal embeddings are deliberately *not* in v1: modality is handled at ingestion (PDF→text chunks, imagery→SAM masks + STAC metadata), and the client LLM supplies vision where judgment is needed. Operationally, store `embedding_model` + version on every embedded row — re-embedding the catalog on a model swap is cheap, and mixed-model states must be detectable, not silent.

---

*Diagrams: `diagram-1-components.mermaid` (components & contracts), `diagram-2-sequence.mermaid` (end-to-end flow), `diagram-3-deployment.mermaid` (single-host Compose topology, K8s later).*
