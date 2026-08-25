# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While the platform is pre-1.0 the public surface (MCP tool contracts, database
schema, serving endpoints) may still change between minor versions; breaking
changes are called out under **Changed** and, until 1.0.0, do not force a major
bump.

## [Unreleased]

### Added
- **OAuth login for the workspace-manager UI** (`/workspaces`), alongside the existing
  API-key login. The viewer is now an OAuth 2.1 + PKCE client of the MCP authorization
  server (`services/mcp/oauth.py`): "Logga in med OAuth" runs the invite-code flow and
  resolves to the same `app.api_keys` principal the MCP derives, so both login paths
  share workspaces. New `services/viewer/oauth_client.py`; routes `/auth/login` and
  `/auth/callback`; gated by `GEODATA_INVITE_CODE`. (ska-krav #17)

### Security
- Viewer auth cookies (`gdw_auth` session + `gdw_oauth` OAuth state) now set the
  `Secure` flag by default when the public origin is HTTPS (`COOKIE_SECURE` overrides).
- Domain-separated the OAuth state-cookie HMAC from the session-cookie HMAC, and made
  all cookie/CSRF signature comparisons byte-safe (no `TypeError` on non-ASCII input).

## [0.1.0] - 2026-08-25

Pilot bid baseline for **Sundsvalls kommun** — GovTech4All Pilot 3 "AI och
geodata" (UH-2026-159). First tagged release of Geodata MCP v2: a PostGIS-backed
municipal geodata platform for LLM-driven analysis, delivered as six containers
(postgres, minio, mcp, worker, viewer, caddy).

### Added

- **PostGIS-backed platform architecture.** Loaders write only to PostGIS; map
  clients read only from serving endpoints (`/data`, `/tiles`) off PostGIS; the
  MCP server is a bearer-authenticated control plane (streamable HTTP) that never
  sits in the data path.
- **MCP control plane** exposing the agent toolset (`workspace`, `search`,
  `load`, `query`, `layer`, `map`, `analyze`, `export`) over streamable HTTP with
  `Authorization: Bearer` API-key auth.
- **Durable workspaces.** Identity is the API key rather than the connection, so
  reconnecting lands back in the same named workspace with its tables intact. A
  key owns up to 20 named workspaces, exactly one active, each backed by a lazily
  created `ws_<hex>` schema. Includes a human-facing workspace manager UI
  (`/workspaces`).
- **Connectors for all register kinds** — WFS, WMTS, OGC API, STAC, PDF,
  text documents, file uploads and inline rows — verified against the official
  Sundsvall source list via `scripts/connector_test.py`.
- **OAuth2 invite-code login** for the MCP server, alongside bearer API keys.
- **MapLibre and Origo (OpenLayers) map renderers**, each served from the same
  renderer-agnostic map view with per-renderer basemaps; MapLibre pages pick up
  changes in ≤ 5 s via ETag polling and every page links to its counterpart.
- **Feature-info popups on by default** in the viewer, and an **orthophoto
  before/after inspector** for comparing imagery vintages.
- **SAM3 orthophoto change detection.** A dedicated `segmenter` service (SAM3)
  and a `change_detect` processor that segments differences between two
  orthophoto vintages, fronted by a WMS auth proxy.
- **`analyze` tool with a processor registry** (`list` / `describe` / `run` /
  `status` / `cancel`); long-running analyses run as cancellable jobs and land
  their results as workspace layers.
- **Data validation and geometry repair at ingest**, plus `scripts/validate_data.py`
  reconciling ingested row counts against each source's own reported count.
- **`export` tool** producing GPKG / GeoJSON / CSV / Parquet with a provenance
  citation sidecar.
- **Provenance and audit model**: read logging via `app.query_log`, an
  append-only `app.provenance` write ledger with planner-extracted input tables,
  and DDL event-trigger backstops over the `ws_*`/`ref` schemas.

### Changed

- **`change_detect` moved out of `load`** and into the `analyze` processor
  registry, making change detection one of a general family of analysis
  processors rather than a bespoke load path.
- **`change_detect` now accepts public WMS orthophoto vintages** as imagery
  sources, not only pre-registered layers.
- **Workspaces are no longer expired on an idle TTL.** An hourly worker sweep now
  drops only *orphaned* `ws_*` schemas (ones no `app.workspaces` row owns),
  keeping active workspaces durable across reconnects.
- **Viewer runs three uvicorn workers** and busts the style cache on code
  changes so clients no longer serve a stale map style.
- Corrected ETag handling on serving endpoints and tightened container restart
  policies while closing audit gaps.

### Security

- **Credentials moved out of the repository** into a gitignored `.env`
  (`.env.example` ships placeholders only); the MCP service refuses to start
  without at least one configured API key.
- **API keys are never stored in the clear** — only SHA-256 digests live in
  `app.api_keys`, so a leaked row is not a replayable credential.
- **Read-only query isolation**: `query` runs as `agent_ro` with
  `SET TRANSACTION READ ONLY` re-asserted per transaction, a statement-kind
  guard, a 15 s timeout and a 1 000-row cap; pooled connections are scrubbed on
  release.
- **Cross-principal isolation**: writes are confined to the calling key's active
  `ws_<hex>` schema; `app.api_keys`/`app.workspaces` are unreadable by the agent
  roles, so one principal cannot enumerate another's keys, workspaces or map
  views.
- **Web hardening**: every HTML page ships a Content-Security-Policy without
  `'unsafe-inline'`, and the workspace manager UI enforces per-principal CSRF
  tokens with ownership checks on every action.
- Attack regressions are pinned in `scripts/security_test.py`.
