# Deployment and operations

## Docker Compose

From the repository root:

```sh
cp .env.example .env
# Replace placeholders; set PUBLIC_BASE_URL, S3_PUBLIC_ENDPOINT and EMBED_MODEL.
# Configure VISION_BASE_URL and VISION_MODEL for image analysis/OCR.
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 mcp viewer worker
```

The stack contains PostgreSQL/PostGIS, MinIO, MCP, viewer, worker and Caddy.
Caddy routes `/mcp`, OAuth and discovery to MCP, and other requests to the
viewer. Source credentials belong in the environment, never the catalog.
SAM3 runs separately; configure `SAM3_URL` when selecting its segmentation backend.
The default vision backend uses the worker's `VISION_*` settings from `.env.example`.

The reference Compose file publishes Caddy on port 8080. Bind the proxy to
loopback or the tailnet interface for a private deployment. PostgreSQL and
MinIO host ports are already bound to loopback. Map and export URLs must be
reachable by their consumers; the S3 host is part of presigned signatures.
For remote access, terminate HTTPS at the deployment proxy.

Configuration is documented in [`.env.example`](../.env.example) and
[`CONTRACTS.md`](../CONTRACTS.md). Role passwords are initialized only once;
changing `.env` does not rotate passwords in an existing database.

## Database upgrades

Fresh databases bootstrap extensions and roles, then run the same versioned SQL
sequence used for upgrades. The database image includes the scripts. For an
existing volume, back up, update the database image, then run as its superuser:

```sh
docker compose build postgres
docker compose up -d postgres
docker compose exec -T postgres sh -c \
  '/opt/geodata-db/migrate.sh -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

The first run on an older, untracked database preserves existing tables and data
while applying any missing changes from migrations 000–005. It also establishes
`public.geodata_schema_migrations`. Existing change-detection and cancelled jobs
remain valid. The runner serializes concurrent upgrades with a database lock,
commits each migration with its history entry, and skips recorded migrations.
A failed migration rolls back; rerun after resolving the failure. Applied SQL is
checksum-checked: append a new numbered file instead of editing recorded files.
Do not run individual SQL files directly or put BEGIN/COMMIT in new migrations.

Run migrations before restarting the app services with their new images. MCP
startup no longer creates tables. These migrations update Geodata's own catalog,
workspace and service database; they never alter an upstream data provider.
For Kubernetes, run the same command inside the updated PostgreSQL pod before
rolling out the application images. Keep a database backup for rollback.

## Backups and restore

Back up both PostgreSQL and MinIO exports. A database dump preserves catalog,
workspaces, maps, jobs, authentication state and provenance; it does not include
objects in MinIO.

```sh
docker compose exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > geodata.dump
```

Test restores against a separate empty database using `pg_restore`; copy MinIO
objects using `mc mirror` with the appropriate source and destination aliases.
Keep backups and credentials outside the repository. `docker compose down`
keeps volumes; adding `-v` deletes database, object-store and model-cache data.

## Kubernetes / OpenShift

The Helm chart is in [`helm/geodata-mcp`](helm/geodata-mcp). Build and publish
service images to your registry. Build the database from `db/`; build app images
from the repository root, e.g. `docker build -f services/viewer/Dockerfile .`,
so each image includes `geodata_common/`.
The database image includes bootstrap scripts and versioned migrations.
`postgres.initScriptsConfigMap` is an optional bootstrap override.

Use a deployment-specific values file:

```yaml
image:
  registry: registry.example.com
secrets:
  create: false
  existingSecret: geodata-credentials
config:
  publicBaseUrl: https://geodata.example.com
  s3PublicEndpoint: https://exports.example.com
mcp:
  replicaCount: 1
worker:
  replicaCount: 1
```

The existing Secret must contain every key in
[`templates/secret.yaml`](helm/geodata-mcp/templates/secret.yaml), including the
assembled database URLs. Configure image tags, storage, ingress hosts and TLS
in the values file; the full options are in
[`values.yaml`](helm/geodata-mcp/values.yaml).

```sh
helm lint deploy/helm/geodata-mcp
helm template geodata deploy/helm/geodata-mcp -f my-values.yaml
helm upgrade --install geodata deploy/helm/geodata-mcp \
  --namespace geodata --create-namespace -f my-values.yaml --wait
```

Create the namespace before supplying an existing Secret. For OpenShift, use
`openshift.enabled: true` and `ingress.enabled: false` to render a Route.
Review the rendered resources against the cluster's security and storage policy.

## Scaling and diagnostics

The worker's startup recovery requeues all running jobs. Keep one worker until
job leases and heartbeats are implemented. MCP transports are held in process
memory; multiple MCP replicas need reliable session routing. The viewer can
run multiple workers. Database and MinIO state must persist across restarts.

- Check `/healthz` and service logs when startup fails.
- A missing `GEODATA_API_KEYS` intentionally prevents MCP startup.
- Search can fall back to text matching while the embedding model downloads or loads.
- Check job `status`, `error` and ingestion warnings before interpreting empty layers.
- Check map layer references, geometry/SRID and backdrop availability for blank maps.
- Compiler source fingerprints and layer metadata participate in cache invalidation.
- Inspect change-detection coverage alongside candidates; failed tiles are not negative results.

See [observability](../docs/observability.md) for the viewer's JSON logs and
Prometheus endpoint. External source availability, upstream credentials and
model availability affect the live connector and change-detection tests.
