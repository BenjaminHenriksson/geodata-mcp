# Deploy — Geodata MCP v2

> **SV:** Driftsättning på Kubernetes / Red Hat OpenShift via Helm-charten i
> `deploy/helm/geodata-mcp/`. Detta dokument beskriver CI/CD-flödet,
> `helm install/upgrade`, OpenShift-målet, kapacitetsdimensionering, hemligheter,
> TLS, horisontell skalning och Prometheus-övervakning.
>
> **EN:** Deployment to Kubernetes / Red Hat OpenShift via the Helm chart in
> `deploy/helm/geodata-mcp/`. This document covers the CI/CD flow,
> `helm install/upgrade`, the OpenShift target, capacity sizing, secrets, TLS,
> horizontal scaling, and Prometheus monitoring.

Uppfyller ska-krav / satisfies ska-krav **#51, #79, #84** och stödjer / supports
**#49, #89, #81, #80, #59, #34, #40**.

---

## 1. Översikt / Overview

**SV:** Plattformen består av sex tjänster som speglar `docker-compose.yml`. Caddy
är den enda publika ingången (single origin); alla andra tjänster nås enbart inom
klustret. Charten renderar en `StatefulSet` för Postgres och `Deployment` för
övriga. Statslösa komponenter (`mcp`, `viewer`, `worker`, `caddy`) körs med flera
repliker, `RollingUpdate` och liveness/readiness-probar.

**EN:** The platform is six services mirroring `docker-compose.yml`. Caddy is the
single public origin; every other service is reachable only inside the cluster.
The chart renders a `StatefulSet` for Postgres and `Deployment`s for the rest.
Stateless components (`mcp`, `viewer`, `worker`, `caddy`) run with multiple
replicas, `RollingUpdate`, and liveness/readiness probes.

| Tjänst / Service | Kind | Port (intern) | Roll / Role |
|---|---|---|---|
| postgres | StatefulSet + Service + PVC | 5432 | PostGIS 17 + pgvector + pg_trgm + pgaudit |
| minio | Deployment + Service + PVC | 9000 (api), 9001 (console) | Objektlager, `exports`-bucket / object store |
| mcp | Deployment + Service | 8000 | FastMCP streamable HTTP `/mcp`, bearer + OAuth |
| worker | Deployment + Service | 8100 | Jobbkörning + `/embed` (EmbeddingGemma-300M) |
| viewer | Deployment + Service | 8001 | Kartsidor (MapLibre/Origo) + workspace-UI |
| caddy | Deployment + Service | 80 | Reverse proxy, enda publika origin / single origin |

Portar och miljövariabler är exakt de som `docker-compose.yml` och `CONTRACTS.md`
anger. Kartan bygger `S3_ENDPOINT`, `EMBED_URL` och `DATABASE_URL_*` från de
renderade Service-namnen så att de alltid resolvar i klustret.

---

## 2. Förutsättningar / Prerequisites

**SV:**
- Ett Kubernetes- eller OpenShift-kluster med en `StorageClass` som stödjer
  `ReadWriteOnce`-volymer, samt en ingress-controller (K8s) eller OpenShift Router.
- Helm 3.12+.
- Sex container-images byggda och pushade till ett register som klustret når
  (bygg med `docker build` per tjänst — se respektive `Dockerfile`). På OpenShift
  (ofta luftgapat) — spegla **alla** images, inklusive `minio/minio`, `caddy` och
  Postgres-basen, till köparens interna register och peka `image.registry` dit.
- **Postgres-init:** `geodata-mcp/db`-imagen måste innehålla `db/init/*` under
  `/docker-entrypoint-initdb.d` (lägg `COPY init /docker-entrypoint-initdb.d` i
  `db/Dockerfile` vid bygget), **eller** lägg skripten i en ConfigMap och sätt
  `postgres.initScriptsConfigMap`. Utan detta skapas inte schema/roller på en tom
  datavolym (i compose är katalogen en bind-mount; ett kluster har ingen sådan).

**EN:**
- A Kubernetes/OpenShift cluster with a `StorageClass` for `ReadWriteOnce`
  volumes and an ingress controller (K8s) or the OpenShift Router.
- Helm 3.12+.
- Six container images built and pushed to a registry the cluster can reach. On
  OpenShift (often air-gapped) mirror **all** images — including `minio/minio`,
  `caddy`, and the Postgres base — into the buyer's internal registry and set
  `image.registry` accordingly.
- **Postgres init:** the `geodata-mcp/db` image must carry `db/init/*` in
  `/docker-entrypoint-initdb.d`, or provide them via a ConfigMap referenced by
  `postgres.initScriptsConfigMap`.

---

## 3. CI/CD-flöde / CI/CD flow

**SV:** Rekommenderat flöde (ska-krav #51 automatiserad driftsättning, #79):

```
  git push  ──▶  CI (build & test)  ──▶  bygg + pusha images  ──▶  helm upgrade
```

1. **Bygg & testa** — kör enhetstester och `python -m py_compile` per tjänst.
2. **Bygg images** — en image per tjänst (`services/mcp`, `services/worker`,
   `services/viewer`, `db`), taggade med git-SHA eller release-version.
   Pusha till registret (`image.registry`).
3. **Rendera & granska** — `helm template` + `helm lint` i pipelinen; valfritt
   `helm diff upgrade` för att visa ändringen innan den appliceras.
4. **Driftsätt** — `helm upgrade --install` mot mål-namespace. Statslösa tjänster
   rullas om utan avbrott (`RollingUpdate`, `maxUnavailable: 0`).
5. **Verifiera** — `helm test`/`kubectl rollout status`; readiness-probar håller
   trafik borta tills `/healthz` svarar.

Hemligheter injiceras aldrig i pipelinen som klartext — de bor i köparens
OpenShift Secrets (se §6). Pipelinen refererar enbart namnet.

**EN:** Recommended flow (ska-krav #51 automated deployment, #79):

```
  git push  ──▶  CI (build & test)  ──▶  build + push images  ──▶  helm upgrade
```

Same steps as above: build & test, build one image per service tagged with the
git SHA / release, push to `image.registry`, `helm template` + `helm lint` (and
optionally `helm diff upgrade`) in the pipeline, then `helm upgrade --install`.
Stateless services roll without downtime (`RollingUpdate`, `maxUnavailable: 0`).
Secrets are never carried in the pipeline as plaintext — they live in the buyer's
OpenShift Secrets (§6); the pipeline references only the name.

Exempel GitLab CI / example GitLab CI stage:

```yaml
deploy:
  stage: deploy
  script:
    - helm lint deploy/helm/geodata-mcp
    - helm upgrade --install geodata-mcp deploy/helm/geodata-mcp
        --namespace geodata --create-namespace
        --set image.registry=$REGISTRY
        --set-string mcp.image.tag=$CI_COMMIT_SHORT_SHA
        --set-string worker.image.tag=$CI_COMMIT_SHORT_SHA
        --set-string viewer.image.tag=$CI_COMMIT_SHORT_SHA
        --set-string postgres.image.tag=$CI_COMMIT_SHORT_SHA
        --set secrets.create=false
        --set secrets.existingSecret=geodata-mcp-secret
        --set openshift.enabled=true
        --set ingress.enabled=false
        --wait --timeout 10m
```

---

## 4. Helm install / upgrade

**SV:** Grundinstallation (plain Kubernetes, Ingress + TLS):

**EN:** Base install (plain Kubernetes, Ingress + TLS):

```bash
helm install geodata-mcp deploy/helm/geodata-mcp \
  --namespace geodata --create-namespace \
  --set image.registry=registry.example.com \
  --set ingress.host=geodata.sundsvall.example.se \
  --set config.publicBaseUrl=https://geodata.sundsvall.example.se \
  --set config.s3PublicEndpoint=https://s3.geodata.sundsvall.example.se
```

Uppgradering / upgrade:

```bash
helm upgrade geodata-mcp deploy/helm/geodata-mcp -f my-values.yaml
```

Avinstallation / uninstall (PVC:er för Postgres/MinIO behålls / PVCs are kept):

```bash
helm uninstall geodata-mcp -n geodata
```

Viktiga värden / key values (`values.yaml` har fullständiga kommentarer / is
fully commented):

| Värde / Value | Standard / Default | Betydelse / Meaning |
|---|---|---|
| `image.registry` | `registry.example.com` | Register för alla images / registry for all images |
| `openshift.enabled` | `false` | Rendera OpenShift Route (#84) / render OpenShift Route |
| `ingress.enabled` / `ingress.host` | `true` / `geodata…` | K8s Ingress + värdnamn / host |
| `ingress.tls.*` | på / on | TLS-secret för Ingress (#34) |
| `secrets.create` / `secrets.existingSecret` | `true` / `""` | Chart-secret vs köparens Secret (#59) |
| `config.publicBaseUrl` | placeholder | Publik origin till agenten / public origin |
| `config.s3PublicEndpoint` | placeholder | Signeras in i export-URL:er / signed into export URLs |
| `<tjänst>.replicaCount` | 2 (worker 1) | Horisontell skalning (#40) |
| `metrics.enabled` | `true` | Prometheus scrape-annoteringar (#81) |

---

## 5. OpenShift-mål / OpenShift target (#84)

**SV:** Sätt `openshift.enabled=true` för att rendera en native `Route`
(`route.openshift.io/v1`) mot caddy-tjänsten i stället för en Ingress. TLS
termineras i OpenShift-routern (`termination: edge`, plattformen sköter
certifikatet); `insecureEdgeTerminationPolicy: Redirect` tvingar HTTPS.

Säkerhetskontext lämnas tom som standard så att OpenShifts `restricted-v2` SCC
kan tilldela UID/fsGroup ur namespace-intervallet (ingen hårdkodad `runAsUser`).
Behöver Postgres-basimagen ett fast UID kan köparen antingen använda en
OpenShift-anpassad Postgres-image eller bevilja lämplig SCC — sätt då
`podSecurityContext`/`securityContext` i ett values-override.

Typisk OpenShift-profil / typical OpenShift profile:

```bash
helm upgrade --install geodata-mcp deploy/helm/geodata-mcp \
  -n geodata --create-namespace \
  --set openshift.enabled=true \
  --set ingress.enabled=false \
  --set secrets.create=false --set secrets.existingSecret=geodata-mcp-secret \
  --set image.registry=<intern-registry>/geodata-mcp
```

**EN:** Set `openshift.enabled=true` to render a native `Route` to the caddy
Service instead of an Ingress. TLS terminates at the router (`termination: edge`;
the platform manages the certificate), and `insecureEdgeTerminationPolicy:
Redirect` forces HTTPS. Security contexts are empty by default so the
`restricted-v2` SCC assigns UID/fsGroup from the namespace range; override
`podSecurityContext`/`securityContext` if a component needs a fixed UID.

---

## 6. Hemligheter / Secrets (#59)

**SV:** Riktiga credentials kommer från **köparens OpenShift Secrets**, inte från
charten. Två lägen:

- `secrets.existingSecret: <namn>` — charten refererar en Secret som köparen
  förvaltar utanför Helm (rekommenderas i produktion). Den måste innehålla exakt
  nycklarna i `templates/secret.yaml`, inklusive de sammansatta
  `DATABASE_URL_APP/RO/WS`.
- `secrets.create: true` — charten renderar en Secret från platshållarvärdena i
  `values.yaml` (endast dev/CI; committa aldrig riktiga lösenord).

Nycklar / keys: `POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `AGENT_RO_PASSWORD`,
`AGENT_WS_PASSWORD`, `DATABASE_URL_APP`, `DATABASE_URL_RO`, `DATABASE_URL_WS`,
`MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `GEODATA_API_KEYS`,
`GEODATA_INVITE_CODE`, `VIEWER_SECRET`, `LANTMATERIET_CREDENTIALS`,
`GEODATA_HTTP_CREDENTIALS`.

Lösenord skrivs verbatim in i connection-URL:er, så de måste vara URL-säkra
(alfanumeriska) om de inte är förkodade. Privat register: lägg
imagePullSecret-namn i `image.pullSecrets`.

**EN:** Real credentials come from the **buyer's OpenShift Secrets**, not the
chart. Use `secrets.existingSecret: <name>` in production (the Secret must carry
every key listed in `templates/secret.yaml`, including the assembled
`DATABASE_URL_*`); `secrets.create: true` renders a placeholder Secret for
dev/CI only. Passwords go verbatim into connection URLs, so keep them URL-safe.
Private registry: put pull-secret names in `image.pullSecrets`.

Skapa en Secret utanför Helm / create a Secret out-of-band:

```bash
kubectl -n geodata create secret generic geodata-mcp-secret \
  --from-literal=POSTGRES_PASSWORD=... \
  --from-literal=APP_DB_PASSWORD=... \
  --from-literal=AGENT_RO_PASSWORD=... \
  --from-literal=AGENT_WS_PASSWORD=... \
  --from-literal=DATABASE_URL_APP='postgresql://geodata_app:...@geodata-mcp-postgres:5432/geodata' \
  --from-literal=DATABASE_URL_RO='postgresql://agent_ro:...@geodata-mcp-postgres:5432/geodata' \
  --from-literal=DATABASE_URL_WS='postgresql://agent_ws:...@geodata-mcp-postgres:5432/geodata' \
  --from-literal=MINIO_ROOT_USER=... --from-literal=MINIO_ROOT_PASSWORD=... \
  --from-literal=GEODATA_API_KEYS=... --from-literal=GEODATA_INVITE_CODE= \
  --from-literal=VIEWER_SECRET=... \
  --from-literal=LANTMATERIET_CREDENTIALS= --from-literal=GEODATA_HTTP_CREDENTIALS=
```

---

## 7. TLS (#34)

**SV:** TLS termineras vid kanten:
- **Kubernetes Ingress** — `ingress.tls.enabled=true` med `ingress.tls.secretName`
  (en TLS-Secret med cert/nyckel; kan utfärdas av cert-manager).
- **OpenShift Route** — `openshift.route.tls.termination: edge` (standard);
  routern sköter certifikatet. `reencrypt`/`passthrough` stöds via samma värde.

All extern trafik går genom caddy (enda origin). MinIO-export-URL:er är
presignerade och går **inte** genom Caddy (de ingår i signaturen) — behöver de
nås externt, aktivera `ingress.minio.enabled` (egen TLS-host) och sätt
`config.s3PublicEndpoint` till den hosten.

**EN:** TLS terminates at the edge: Ingress via `ingress.tls.secretName`, or the
OpenShift Route via `openshift.route.tls.termination: edge` (the router manages
the cert; `reencrypt`/`passthrough` supported). All external traffic goes through
caddy. MinIO presigned export URLs bypass Caddy by design (they are part of the
signature); enable `ingress.minio.enabled` and point `config.s3PublicEndpoint`
at that host if exports must be reachable externally.

---

## 8. Horisontell skalning / Horizontal scaling (#40, #89)

**SV:** Statslösa tjänster kör flera repliker med `RollingUpdate`
(`maxSurge: 1`, `maxUnavailable: 0` → noll avbrott) och liveness/readiness-probar
mot `/healthz`. Skala via `--set <tjänst>.replicaCount=N` eller en HPA (CPU-mått
kräver metrics-server / körs mot `resources.requests`).

- **mcp** — statslös per request (workspace-tillstånd ligger i Postgres). Servicen
  använder `sessionAffinity: ClientIP` så att en återansluten klient hamnar på
  samma pod som håller dess in-memory MCP-session; workspace/lager överlever ändå
  ett podbyte eftersom de är i databasen.
- **worker** — jobbloopen använder `FOR UPDATE SKIP LOCKED`, så flera workers
  plockar jobb säkert parallellt. Varje pod har sin egen HF-modellcache.
- **viewer** / **caddy** — helt statslösa, skala fritt.
- **postgres** / **minio** — enkelinstans med beständig volym (skala inte
  replikerna; för HA krävs Postgres-replikering / MinIO distributed, utanför denna
  charts omfattning).

**EN:** Stateless services run multiple replicas with `RollingUpdate`
(`maxSurge: 1`, `maxUnavailable: 0` → zero downtime) and `/healthz`
liveness/readiness probes. Scale with `--set <service>.replicaCount=N` or an HPA.
`mcp` uses `sessionAffinity: ClientIP` so a reconnecting client lands on the pod
holding its in-memory session (durable state is in Postgres regardless); `worker`
scales via `FOR UPDATE SKIP LOCKED`; `viewer`/`caddy` are fully stateless.
`postgres`/`minio` are single-instance with persistent volumes — do not scale
their replicas (HA is out of scope for this chart).

---

## 9. Kapacitet / Capacity (#80)

**SV:** Per-pod resursförfrågningar/gränser, beständig lagring och nätprofil.
Requests är garanterad tilldelning (schemaläggning + #80-planering); limits är tak.

**EN:** Per-pod resource requests/limits, persistent storage, and network profile.
Requests are the guaranteed reservation (scheduling + #80 planning); limits cap.

| Tjänst / Service | Repliker / Replicas | CPU req → limit | RAM req → limit | Lagring / Storage | Nät / Network |
|---|---|---|---|---|---|
| postgres | 1 | 250m → 2 | 1Gi → 2Gi | 20Gi PVC (RWO) | Intern (5432) / internal |
| minio | 1 | 100m → 1 | 256Mi → 1Gi | 50Gi PVC (RWO) | Intern + export-egress |
| mcp | 2 | 100m → 1 | 256Mi → 512Mi | — | Låg / low |
| worker | 1 | 500m → 2 | 2Gi → 4Gi | HF-cache 8Gi emptyDir (ell. 10Gi PVC) | Hög: harvest/ingest-egress / high |
| viewer | 2 | 250m → 1 | 512Mi → 1Gi | — | Medel: tiles/geojson / medium |
| caddy | 2 | 50m → 200m | 64Mi → 128Mi | — | All ingång / all ingress |

**Summa vid standardrepliker / totals at default replicas:**

| | CPU | RAM | Beständig lagring / Persistent storage |
|---|---|---|---|
| Requests (reserverat / reserved) | ≈ 1.65 vCPU | ≈ 4.9 GiB | 70 GiB (Postgres 20 + MinIO 50) |
| Limits (tak / ceiling) | ≈ 9.4 vCPU | ≈ 10.25 GiB | + worker HF-cache 8–10 GiB (efemär om emptyDir) |

**SV:** Dimensionera `StorageClass` och lagringsstorlekar efter datamängden:
Sundsvall-katalogen (~900 dataset), ingesterade `ref`-lager och pgvector-index
växer Postgres-volymen; exporter och ortofoto-underlag växer MinIO. Justera
`postgres.persistence.size` / `minio.persistence.size`. Modellcachen kan göras
beständig (`worker.hfCache.persistence.enabled=true`) för att slippa
omnedladdning vid omstart. Alla värden justeras i `values.yaml`.

**EN:** Size the `StorageClass` and volumes to the data: the Sundsvall catalog
(~900 datasets), ingested `ref` layers and pgvector indexes grow the Postgres
volume; exports and orthophoto backdrops grow MinIO. Tune
`postgres.persistence.size` / `minio.persistence.size`. Persist the model cache
(`worker.hfCache.persistence.enabled=true`) to avoid re-download on restart.

---

## 10. Övervakning / Monitoring — Prometheus (#81)

**SV:** När `metrics.enabled=true` (standard) sätter charten
`prometheus.io/scrape: "true"`, `prometheus.io/path: /metrics` och
`prometheus.io/port: <port>` på pod-annoteringarna för `mcp`, `worker` och
`viewer`, så att en Prometheus med pod-annotation-discovery skrapar `/metrics`.
`/healthz` finns alltid för probarna. Kör Prometheus Operator? Skapa en
`PodMonitor`/`ServiceMonitor` som väljer `app.kubernetes.io/part-of: geodata-mcp`.
`metrics.path` kan ändras i values.

**EN:** With `metrics.enabled=true` (default) the chart stamps
`prometheus.io/scrape`, `prometheus.io/path: /metrics` and `prometheus.io/port`
on the `mcp`, `worker` and `viewer` pod annotations, so an annotation-discovery
Prometheus scrapes `/metrics`. `/healthz` is always available for probes. Under
the Prometheus Operator, add a `PodMonitor`/`ServiceMonitor` selecting
`app.kubernetes.io/part-of: geodata-mcp`. Adjust `metrics.path` in values.

---

## 11. Verifiering / Verification

```bash
helm lint deploy/helm/geodata-mcp
helm template geodata-mcp deploy/helm/geodata-mcp \
  --set openshift.enabled=true --set ingress.enabled=false | less
kubectl -n geodata get pods,svc,statefulset,deploy,pvc,ingress
kubectl -n geodata rollout status deploy/geodata-mcp-mcp
```

**SV:** Redo att ta emot trafik när alla pods är `Ready` och Ingress/Route
svarar på `https://<host>/healthz`. Bearer-nyckel krävs för `/mcp`
(401 utan giltig nyckel).

**EN:** Ready when all pods are `Ready` and the Ingress/Route answers
`https://<host>/healthz`. A bearer key is required for `/mcp` (401 without one).
