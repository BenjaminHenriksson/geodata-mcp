# Geodata MCP

Geospatial analysis through eight MCP tools, with durable workspaces, PostGIS,
MapLibre and Origo maps, and downloadable GIS exports. The pilot source list is
[`data_sources.xlsx`](data_sources.xlsx); the native CRS is EPSG:3014
(SWEREF 99 17 15).

```text
MCP tools → PostgreSQL/PostGIS ← ingestion worker
                  ↓                    ↓
            viewer API           MinIO exports
                  ↓
          MapLibre / Origo
```

## Run locally

```sh
cp .env.example .env
# Replace the placeholder credentials and configure public URLs.
docker compose up -d --build
uv venv
uv pip install -r services/mcp/requirements.txt
.venv/bin/python scripts/bootstrap_sundsvall.py
```

The bootstrap registers sources, harvests their catalogs, ingests the pilot
layers, and embeds the catalog. Initial model downloads and ingestion take time.
`GEODATA_API_KEYS` must contain at least one key; use two for the security tests.
Generate keys with `openssl rand -hex 24`.

| Endpoint | Purpose |
| --- | --- |
| `http://localhost:8080/mcp` | Streamable HTTP MCP, bearer API key or OAuth token |
| `http://localhost:8080/workspaces` | Workspace manager, sign in with an API key |
| `http://localhost:8080/v/<view_id>` | MapLibre map; append `?renderer=origo` for Origo |
| `http://localhost:8080/docs` | Interactive viewer API reference |
| `localhost:5433` | PostgreSQL, credentials from `.env` |
| `http://localhost:9001` | MinIO console, credentials from `.env` |

For Eneo, configure an MCP server with the `/mcp` URL and an
`Authorization: Bearer <key>` header. Set `PUBLIC_BASE_URL` to the viewer's
reachable origin and `S3_PUBLIC_ENDPOINT` to the reachable S3 endpoint so tool
results contain usable map and download URLs.

## Tools

| Tool | Purpose |
| --- | --- |
| `workspace` | Create, list, switch, rename and delete durable workspaces |
| `search` | Search the catalog and document chunks; fetch dataset details by ID |
| `load` | Register sources, harvest metadata, ingest data, load inline rows, refresh embeddings and inspect jobs |
| `query` | Read-only SQL with PostGIS, a 15-second timeout and a result-row cap |
| `layer` | Create derived tables, update attributes, change styling and manage layers |
| `map` | Save a map specification and return its viewer URL |
| `analyze` | Discover, start, inspect and cancel analysis jobs; currently SAM3 change detection |
| `export` | Export GPKG, GeoJSON, CSV or Parquet with a provenance sidecar |

A key owns named workspaces and one active workspace. Reconnecting preserves the
active workspace and its layers. Switching through the tool or manager affects
the next tool call. Workspace deletion is explicit; there is no idle expiry.

Both map renderers consume the same saved specification. MapLibre uses Web
Mercator with CARTO Positron; Origo uses EPSG:3014 and the configured municipal
WMS backdrop. MapLibre uses GeoJSON for smaller layers and vector tiles above
20,000 features. Origo reports incompatible WMTS layers instead of drawing
misaligned tiles. Style changes invalidate the viewer cache.

## Data and models

WFS, WMS, WMTS, OGC API Features, STAC, files, PDF, text and inline sources are
supported. Authenticated upstreams use `LANTMATERIET_CREDENTIALS` or per-host
`GEODATA_HTTP_CREDENTIALS`; credentials stay server-side.

Some official layers cover more than Sundsvall. Filter against
`ref.kommungrans` when answering municipal questions, and consult layer notes.
A zero-row layer can reflect an empty upstream source. Use
`scripts/validate_data.py` to compare ingestion with source counts and check
SRIDs, extents and geometry validity.

The worker uses EmbeddingGemma-300M with 256-dimensional embeddings. Search can
fall back to trigram matching while the model is unavailable. Scanned PDFs
without a text layer require OCR, which is not implemented.

SAM3 runs as a separate service at `SAM3_URL`. See the
[segmenter setup](services/segmenter/README.md) for MLX and GPU backends.
Change detection produces candidate and coverage layers: missing coverage is
not evidence of no change. Inspect the imagery before interpreting candidates.

## Verification

Run the local regression suite without services or credentials:

```sh
uv venv
uv pip install -r services/mcp/requirements.txt -r services/viewer/requirements.txt pytest
.venv/bin/python -m pytest -q tests
```

The live scripts create and modify test workspaces. Run them against a test
stack with the pilot data loaded:

```sh
.venv/bin/python scripts/e2e_test.py
.venv/bin/python scripts/security_test.py
.venv/bin/python scripts/connector_test.py
.venv/bin/python scripts/validate_data.py
```

The change-detection scripts additionally need a running segmenter and imagery
access. `scripts/loadtest.js` provides the k6 load test.

## Repository

- [`CONTRACTS.md`](CONTRACTS.md): schemas, tool arguments, job payloads and rendering contracts.
- [`deploy/README.md`](deploy/README.md): deployment, migrations, backups and operational limits.
- [`docs/api.md`](docs/api.md): HTTP and MCP interfaces.
- [`docs/observability.md`](docs/observability.md): logs and metrics.
- [`SECURITY.md`](SECURITY.md): authentication and access boundaries.
- `services/`: MCP server, viewer, ingestion worker and segmenter.
- `db/`: database image, initialization SQL and migrations.
- `scripts/`: bootstrap, live checks, schema and dependency tooling.
- `tests/`: offline regression tests.

Licensed under [AGPL-3.0-only](LICENSE).
