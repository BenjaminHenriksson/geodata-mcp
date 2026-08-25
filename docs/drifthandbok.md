# Drifthandbok — Geodata MCP v2

Denna handbok beskriver hur Geodata MCP v2 driftsätts, konfigureras, säkerhetskopieras,
övervakas och uppgraderas, samt hur de vanligaste driftstörningarna felsöks. Den vänder sig
till den som ansvarar för drift och förvaltning av plattformen hos beställaren (Sundsvalls
kommun, upphandling UH-2026-159, Govtech4all Pilot 3).

Handboken är avsedd att läsas tillsammans med:

- [`README.md`](../README.md) — snabbstart och funktionsöversikt.
- [`CONTRACTS.md`](../CONTRACTS.md) — bindande implementationsspecifikation (topologi,
  miljövariabler, databasschema, API-kontrakt).
- [`geodata-mcp-architecture.md`](../geodata-mcp-architecture.md) — målarkitektur och bakgrund.
- [`kapacitet.md`](kapacitet.md) — dimensionering (CPU/RAM/lagring/nät) för pilot och produktion.
- `docs/observability.md` (#81) — detaljerad specifikation av strukturerad JSON-loggning och
  Prometheus-mätvärden (`/metrics`).

Licens: **AGPL-3.0-only** (överenskommen med beställaren; ersätter anbudsunderlagets
ursprungliga EUPL/GPLv3-skrivning). Se [`LICENSE`](../LICENSE).

---

## 1. Systemöversikt

Plattformen körs som en samling containrar via `docker compose` bakom en gemensam ingång
(Caddy). Arkitekturens kärna: **laddare skriver enbart till PostGIS, kartklienter läser enbart
från serverande endpoints ovanpå PostGIS, och MCP-servern är ett styrplan (control plane) som
aldrig ligger i dataflödet.**

### Tjänster (6 containrar + 1 värdprocess)

| Tjänst | Container | Intern port | Värdport | Roll |
|--------|-----------|-------------|----------|------|
| **caddy** | `caddy` | 80 | **8080** | Omvänd proxy — en enda ingång/origin, TLS-terminering |
| **postgres** | `postgres` | 5432 | 5433 (endast `127.0.0.1`) | PostGIS 17 + pgvector + pg_trgm + pgaudit — all beständig tillståndsdata |
| **mcp** | `mcp` | 8000 | — | FastMCP streamable HTTP på `/mcp`, bearer-auth + OAuth2 (styrplan, 8 verktyg) |
| **viewer** | `viewer` | 8001 | — | Kartsidor (MapLibre + Origo), `/data`+`/tiles`-endpoints, workspace-hanterare, WMS-proxy |
| **worker** | `worker` | 8100 | — | Jobbkörning (WFS/WMS/WMTS/OGC-API/STAC-skörd, ogr2ogr-ingest, PDF/text, export, förändringsdetektering) + `/embed` (EmbeddingGemma) |
| **minio** | `minio` | 9000 (API), 9001 (konsol) | 9000/9001 (endast `127.0.0.1`) | Objektlager för exporter; presignerade URL:er använder värdporten |

**Utanför compose:** **segmenter** (`services/segmenter/`, port **8200**) — SAM 3
konceptsegmentering för förändringsdetektering. Körs som en **native värdprocess** eftersom MLX
inte kan köras i Linux-containrar (kräver Apple Silicon). Worker når den via `SAM3_URL`. Samma
HTTP-kontrakt betjänas av `transformers`-backenden (`SAM3_BACKEND=transformers`) för en
Linux/GPU-driftsättning. Tjänsten behövs endast när `analyze`-verktygets `change_detect`-processor
körs.

### Dataflöde

```
laddare (worker) ──▶ PostGIS ──▶ /data /tiles endpoints (viewer) ──▶ MapLibre / Origo / QGIS (GPKG)
                        ▲
                    MCP-server (styrplan, bearer/OAuth, aldrig i dataflödet)
```

Allt beständigt tillstånd finns i **två** ställen: **Postgres** (kataloger, ref-tabeller,
workspaces, provenance, API-nyckel-hashar, OAuth-tokens) och **MinIO** (exportfiler). Alla
övriga tjänster är i princip tillståndslösa; MCP-servern håller dock streamable-HTTP-sessioner
i processminnet (se avsnitt 11, uppskalning).

Detaljerad tjänstespecifikation finns i [`CONTRACTS.md`](../CONTRACTS.md) (Topology,
MCP server, Worker, Viewer).

---

## 2. Förutsättningar

- **Docker Engine** ≥ 24 och **Docker Compose v2** (`docker compose`, inte `docker-compose`).
- **Arkitektur:** arm64 (Apple Silicon) eller amd64. Postgres byggs från `postgres:17-bookworm`
  + PGDG-paket eftersom `postgis/postgis` saknar arm64; worker använder den multiarkitektur-baserade
  `ghcr.io/osgeo/gdal:ubuntu-full`.
- **Diskutrymme:** se [`kapacitet.md`](kapacitet.md). Räkna med minst 60 GB fritt för pilot
  (databas, MinIO, HuggingFace-modellcache ~1,2 GB, byggcache).
- **Python + uv** på driftvärden för att köra bootstrap- och testskripten i `scripts/`
  (`uv venv && uv pip install "mcp>=1.9" httpx`). Följer beställarens och teamets standard:
  `uv` framför `pip`, alltid i en `.venv`.
- **Förändringsdetektering (valfritt):** en Apple Silicon-värd för SAM 3 via MLX, alternativt en
  Linux/GPU-värd för `transformers`-backenden (`facebook/sam3` är HF-gated — begär åtkomst i god tid).
- **Nätåtkomst** till källorna: `karta.sundsvall.se/geoserver`, `isyroad.isy.se` och
  (för autentiserade tjänster) `*.lantmateriet.se`. Se [`kapacitet.md`](kapacitet.md), avsnitt om nät.

---

## 3. Driftsättning

### 3.1 Docker Compose (referensdriftsättning)

Detta är den driftsättning som ingår i leveransen och som testerna verifierar.

```sh
# 1. Konfigurera hemligheter (se avsnitt 4)
cp .env.example .env
$EDITOR .env                       # byt VARJE lösenord — alla värden är platshållare

# 2. Starta hela systemet (6 containrar byggs och startar)
docker compose up -d --build

# 3. Verifiera att allt är friskt
docker compose ps                  # alla tjänster ska vara "healthy"

# 4. Registrera, skörda och ingesta pilotdata (Sundsvall), ca 10 min
uv venv && uv pip install "mcp>=1.9" httpx
.venv/bin/python scripts/bootstrap_sundsvall.py

# 5. Rök-/regressionstester
.venv/bin/python scripts/e2e_test.py          # full agentflöde; skriver ut en kart-URL
.venv/bin/python scripts/security_test.py     # attackregressioner
.venv/bin/python scripts/validate_data.py     # radantal mot varje källas eget antal
.venv/bin/python scripts/connector_test.py    # alla connector-typer mot officiella källor
```

Åtkomstpunkter efter driftsättning (via Caddy på `http://localhost:8080`):

- **MCP-endpoint:** `http://localhost:8080/mcp` — kräver `Authorization: Bearer <api-nyckel>`.
- **Workspace-hanterare (människa):** `http://localhost:8080/workspaces` — logga in med samma nyckel.
- **Kartvyer:** `http://localhost:8080/v/<view_id>` (lägg till `?renderer=origo` för Origo).
- **Postgres:** `127.0.0.1:5433`, **MinIO-konsol:** `127.0.0.1:9001` (endast lokalt/via tunnel).

### 3.2 Kubernetes / Helm (produktion)

För en driftsättning med redundans, horisontell skalning och hanterade backuper hänvisas till
Helm-charten under `deploy/helm/` (levereras separat). Den paketerar samma sex tjänster som
Deployments/StatefulSets och kapslar in den konfiguration som här beskrivs för compose:

- Postgres som StatefulSet med persistent volym (eller en hanterad PostGIS-tjänst) — se
  dimensionering i [`kapacitet.md`](kapacitet.md).
- MinIO som StatefulSet eller ersatt av hanterad S3-kompatibel lagring.
- `mcp`, `viewer`, `worker` som Deployments; observera skalningsförbehållen i avsnitt 11
  (mcp kräver sticky sessions, worker kräver lease-baserad jobbåterhämtning innan flera repliker).
- Ingress/TLS motsvarande Caddy-konfigurationen i avsnitt 8.
- Hemligheter injiceras från beställarens secret store (avsnitt 4), inte från en `.env`-fil.

Compose-driftsättningen i 3.1 är alltid den auktoritativa beskrivningen av vilka miljövariabler
och portar varje tjänst behöver; Helm-charten speglar den.

---

## 4. Konfiguration och hemligheter

All konfiguration sker via miljövariabler. Vid compose-driftsättning läses de från en
`.env`-fil (gitignorerad); `.env.example` är den incheckade mallen med enbart platshållare.
Compose vägrar att starta om en obligatorisk variabel saknas.

### 4.1 Miljövariabler

| Variabel | Tjänst | Syfte |
|----------|--------|-------|
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | postgres | Superanvändare och databasnamn |
| `APP_DB_PASSWORD` | postgres/alla | Lösenord för rollen `geodata_app` (äger app-scheman) |
| `AGENT_RO_PASSWORD` | postgres/mcp | Lösenord för `agent_ro` (agentens läs-SQL via `query`) |
| `AGENT_WS_PASSWORD` | postgres/mcp | Lösenord för `agent_ws` (`layer`-verktygets skrivväg) |
| `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` | minio/worker | Objektlagrets root-uppgifter |
| `PUBLIC_BASE_URL` | mcp/viewer | Publik bas-URL som verktygen lämnar tillbaka |
| `S3_PUBLIC_ENDPOINT` | worker/mcp | Publik MinIO-endpoint; **ingår i presignerade URL:ers signatur** |
| `GEODATA_API_KEYS` | mcp | Kommaseparerade råa bearer-nycklar; **mcp startar inte utan minst en** |
| `GEODATA_INVITE_CODE` | mcp | Delad inbjudningskod för OAuth-webbinloggning (tom = OAuth avstängt) |
| `VIEWER_SECRET` | viewer | Signerar workspace-hanterarens inloggningscookie |
| `LANTMATERIET_CREDENTIALS` | worker/viewer | `user:password` för Basic auth mot alla `*.lantmateriet.se` |
| `GEODATA_HTTP_CREDENTIALS` | worker/viewer | Per-värd-uppgifter `host=user:pw,...` (överstyr ovanstående) |
| `SAM3_URL` | worker | Adress till segmenter-tjänsten (default `host.docker.internal:8200`) |
| `EMBED_MODEL`, `EMBED_DIM` | worker/mcp | Embeddingmodell (`unsloth/embeddinggemma-300m`) och dimension (256) |

Fullständig lista och exakta connection-URL:er finns i [`CONTRACTS.md`](../CONTRACTS.md)
(Environment variables) och i [`.env.example`](../.env.example).

### 4.2 Nyckelhantering och känsliga aspekter

- **API-nycklar lagras aldrig i klartext** i databasen — endast SHA-256-hashar finns i
  `app.api_keys`. En läckt rad är alltså inte en användbar autentiseringsuppgift.
- **Lösenord tolkas ordagrant** in i connection-URL:er och måste vara URL-säkra (alfanumeriska)
  om de inte förkodas.
- **Rollösenord tillämpas endast vid första start** (tom datavolym). Att ändra dem senare kräver
  `ALTER ROLE ... PASSWORD` eller `docker compose down -v` (raderar all laddad data).
- **Nyckelrotation** sker idag genom att redigera `GEODATA_API_KEYS` och starta om `mcp`
  (eller `UPDATE app.api_keys SET disabled = true`); giltiga hashar cachas i 30 s, så en
  avstängning slår igenom inom det fönstret. Det finns ingen inbyggd utgång/rotationsflöde
  ännu (perimetergrind + beständig identitet, inte en IdP).

### 4.3 Beställarens secret store (#59)

I produktion ska hemligheter **inte** ligga i en `.env`-fil på disk utan hämtas från
beställarens secret store (t.ex. Azure Key Vault, HashiCorp Vault eller Kubernetes Secrets,
enligt beställarens standard — #59). Praktiskt innebär det:

- Varje variabel i tabellen ovan mappas till en post i secret store; ingen hemlighet checkas in
  eller loggas.
- Vid compose kan hemligheterna materialiseras till `.env` vid drifttillfället av secret
  store-integrationen och rensas efteråt; vid Kubernetes injiceras de som `Secret`-monterade
  miljövariabler.
- `.env` (och motsvarande materialiserade filer) ska ha restriktiva filrättigheter (`chmod 600`)
  och aldrig committas — den är gitignorerad.
- Rotationsrutin: uppdatera värdet i secret store, rulla om (`mcp` för `GEODATA_API_KEYS`,
  respektive tjänst för övriga). Notera rollösenordsbegränsningen i 4.2.

---

## 5. Start och stopp

```sh
# Starta / bygg om och starta
docker compose up -d --build

# Stoppa (behåller volymer/data)
docker compose stop

# Stoppa och ta bort containrar (behåller volymer/data)
docker compose down

# VARNING: ta bort containrar OCH volymer (raderar databas, MinIO, modellcache)
docker compose down -v

# Starta om en enskild tjänst (t.ex. efter ändrad .env-variabel för mcp)
docker compose restart mcp

# Segmenter (native värdprocess — startas separat)
cd services/segmenter && uv sync && uv run uvicorn app:app --host 0.0.0.0 --port 8200
```

Alla tjänster har `restart: unless-stopped` och startar automatiskt om efter en krasch eller
omstart av värden. Startordning styrs av `depends_on` med hälsokontroller: `postgres` och
`minio` måste vara `healthy` innan `mcp`, `worker` och `viewer` startar.

Kontrollera status och hälsa:

```sh
docker compose ps                  # översikt + health-status
docker compose logs -f mcp         # följ loggen för en tjänst
curl -fsS http://localhost:8080/healthz          # via Caddy → viewer
```

---

## 6. Backup och återställning

Allt beständigt tillstånd finns i **Postgres** och **MinIO**. En fullständig säkerhetskopia
omfattar båda. Ingen annan tjänst behöver säkerhetskopieras (kod och konfiguration ligger i
repo respektive secret store; HuggingFace-modellcachen återhämtas automatiskt).

### 6.1 Postgres

Logisk säkerhetskopiering (rekommenderas för portabilitet):

```sh
# Full logisk dump (custom-format, komprimerad)
docker compose exec -T postgres pg_dump -U postgres -d geodata -Fc \
  > backup/geodata_$(date +%F).dump

# Återställning till en ren databas
docker compose exec -T postgres pg_restore -U postgres -d geodata --clean --if-exists \
  < backup/geodata_2026-08-25.dump
```

Rekommendationer:

- **Schemalägg dagliga dumpar** och behåll enligt beställarens retentionpolicy. I produktion,
  komplettera med kontinuerlig WAL-arkivering / point-in-time recovery (PITR) eller en hanterad
  Postgres med automatiska backuper.
- Provenance-tabellen `app.provenance` är append-only (UPDATE/DELETE återkallade) och utgör
  revisionsspåret — säkerställ att den ingår i varje backup.
- Notera att `app.api_keys` innehåller nyckel-**hashar**, inte nycklarna själva; för
  disaster recovery måste råa nycklar återställas från secret store (avsnitt 4).

### 6.2 MinIO (exporter)

Exportfilerna i bucketen `exports` är återskapbara (de kan regenereras via `export`-verktyget)
men den presignerade länken förfaller efter 24 h medan objektet ligger kvar. Säkerhetskopiera vid
behov med `mc mirror`:

```sh
# Spegla exports-bucketen till en lokal katalog (kräver mc-konfigurerad alias)
mc mirror geodata/exports backup/minio-exports/

# Återställ
mc mirror backup/minio-exports/ geodata/exports/
```

Notera (housekeeping): exporter ackumuleras för närvarande utan livscykelregel — sätt upp en
MinIO-lifecycle-regel för att åldra ut gamla exportobjekt i produktion.

### 6.3 Volymer

Compose-volymerna är `pgdata` (Postgres), `minio-data` (MinIO) och `hf-cache` (modellvikter,
återskapbar). Vid en fullständig värdmigrering kan volymerna kopieras direkt, men en logisk
`pg_dump` + `mc mirror` är att föredra eftersom den är versions- och arkitekturoberoende.

---

## 7. Loggning och övervakning

### 7.1 Loggning

Alla tjänster loggar **strukturerat till stdout** och fångas av Docker; ingen tjänst skriver
egna loggfiler. Läs och följ loggarna med:

```sh
docker compose logs -f                 # alla tjänster
docker compose logs -f worker mcp      # utvalda tjänster
docker compose logs --since 15m viewer # senaste 15 min
```

I produktion ska stdout samlas in av beställarens loggplattform (t.ex. via en logg-driver eller
en sidecar-collector) för central sökning och larm. Det **strukturerade JSON-loggformatet**
(fält, korrelationsnycklar såsom `query_id`/`job_id`/`workspace_id`, samt hur PostgreSQL:s och
pgAudit:s statementström vidarebefordras) specificeras i detalj i **`docs/observability.md`
(#81)** — se den för fältdefinitioner och exempel.

Revisionsrelevanta spår ligger i databasen och kompletterar applikationsloggen:

- `app.query_log` — varje `query`-anrop (SQL, refererade tabeller, workspace, varaktighet, `query_id`).
- `app.provenance` — varje skrivning (`layer`/`load`), append-only.
- Event-triggers loggar all DDL mot `ws_*`/`ref`, och **pgAudit** loggar den fullständiga
  statementströmmen för agentrollerna under.

### 7.2 Övervakning och hälsokontroller

Varje tjänst exponerar en `/healthz`-endpoint som Docker-hälsokontrollerna redan använder:

| Tjänst | Hälsokontroll |
|--------|---------------|
| postgres | `pg_isready` |
| minio | `mc ready local` |
| mcp | `GET /healthz` → `{"ok": true}` |
| viewer | `GET /healthz` |
| worker | `GET /healthz` → `{"ok": true, "model_loaded": bool}` |
| segmenter | `GET /healthz` → `{"ok": true, "backend": ..., "model_loaded": bool}` (utlöser aldrig modelladdning) |

Snabb hälsokoll:

```sh
docker compose ps                                  # health-status per container
curl -fsS http://localhost:8080/healthz            # publik ingång (viewer via Caddy)
```

**Mätvärden (Prometheus `/metrics`)** — jobbkö-djup, ingest-/embed-latens, tile-serveringstid,
felräknare per tjänst m.m. — samt scrape-konfiguration och rekommenderade larm beskrivs i
**`docs/observability.md` (#81)**. Konfigurera larm åtminstone på: tjänst nere/ohälsosam,
växande jobbkö eller upprepade `error`-jobb i `app.jobs`, låg disk för `pgdata`/`minio-data`,
och embed-tidsgränser (search faller då tillbaka till enbart trigram).

---

## 8. TLS via Caddy (#34)

All extern trafik går genom **en enda ingång (origin)**: Caddy. I referensdriftsättningen
lyssnar Caddy på port 80 (mappad till värdens 8080) och proxar:

- `/mcp*` → `mcp:8000` (svar får inte buffras — `flush_interval -1` för streamable HTTP).
- `/oauth/*` och `/.well-known/oauth-*` → `mcp:8000` (OAuth-auktoriseringsserverns endpoints).
- allt övrigt (`/v/*`, `/data/*`, `/tiles/*`, `/static/*`, `/workspaces`, `/healthz`) → `viewer:8001`.

Se [`deploy/Caddyfile`](../deploy/Caddyfile).

### 8.1 Aktivera HTTPS i produktion (#34)

Caddy kan skaffa och förnya Let's Encrypt-certifikat automatiskt. Ändra platsblocket från `:80`
till det publika värdnamnet, så terminerar Caddy TLS och omdirigerar HTTP→HTTPS utan vidare
konfiguration:

```caddyfile
govtech.exempel-sundsvall.se {
    # ... samma handle-block som i deploy/Caddyfile ...
}
```

Att tänka på vid TLS-aktivering:

- Port **443** (och 80 för ACME/omdirigering) måste vara publikt nåbar; Caddy behöver en
  persistent volym för `/data` (certifikat och nycklar) så de överlever omstart.
- Sätt `PUBLIC_BASE_URL` och `S3_PUBLIC_ENDPOINT` till `https://...`-adresserna. `PUBLIC_BASE_URL`
  är även OAuth-utfärdare (`ISSUER`) och den bas verktygen returnerar; `S3_PUBLIC_ENDPOINT`
  ingår i presignerade export-URL:ers signatur.
- **MinIO exponeras direkt på `:9000`**, inte via Caddy, eftersom den publika endpointen är del
  av den presignerade signaturen. Lägg MinIO bakom TLS (egen Caddy-site eller lastbalanserare)
  innan någon icke-lokal användning.
- Bakom en extern lastbalanserare: behåll `flush_interval -1` för `/mcp` hela vägen så att
  streamable-HTTP-svaren inte buffras.

---

## 9. Uppgradering och databasmigrationer

Migrationerna i [`db/migrations/`](../db/migrations) är idempotent uppgraderings-SQL för en
**befintlig** databas (en ren `docker compose up` mot en tom volym skapar redan det senaste
schemat via `db/init/*`). Kör dem i ordning mot en installation som startades före respektive
funktion:

```sh
docker compose exec -T postgres psql -U postgres -d geodata < db/migrations/001_durable_workspaces.sql
docker compose exec -T postgres psql -U postgres -d geodata < db/migrations/002_connector_job_kinds.sql
docker compose exec -T postgres psql -U postgres -d geodata < db/migrations/003_change_detection.sql
docker compose exec -T postgres psql -U postgres -d geodata < db/migrations/004_oauth.sql
docker compose exec -T postgres psql -U postgres -d geodata < db/migrations/005_job_cancel.sql
```

| Migration | Inför |
|-----------|-------|
| `001_durable_workspaces.sql` | Beständiga workspaces + API-nyckelidentitet (`app.api_keys`, `app.workspaces`) |
| `002_connector_job_kinds.sql` | Jobbtyper för OGC-API/WMTS/STAC/text-connectorerna |
| `003_change_detection.sql` | Jobbtyp och stöd för SAM 3-förändringsdetektering |
| `004_oauth.sql` | OAuth-klienter och -tokens (`app.oauth_clients`, `app.oauth_tokens`) |
| `005_job_cancel.sql` | Status `cancelled` för jobb (avbrott av köade analyser) |

Rekommenderad uppgraderingsrutin:

1. Ta en färsk `pg_dump` (avsnitt 6.1) innan migration.
2. `git pull` senaste koden och `docker compose up -d --build` för att bygga om avbildningar.
3. Kör de migrationer som saknas (ovan) — de är idempotenta och säkra att köra om.
4. Verifiera med `scripts/e2e_test.py` och `scripts/security_test.py`.

Migrationerna är additiva; ingen data raderas. Rollback görs vid behov genom återställning av
`pg_dump` från steg 1.

---

## 10. Felsökningsguide

### 10.1 `mcp` startar inte — saknar `GEODATA_API_KEYS`

**Symtom:** `mcp`-containern startar inte / hälsokontrollen misslyckas; compose-loggen visar att
variabeln `GEODATA_API_KEYS` saknas.

**Orsak:** Autentisering är obligatorisk. `docker-compose.yml` deklarerar
`GEODATA_API_KEYS: ${GEODATA_API_KEYS:?set in .env — auth is not optional}`, och MCP-servern
vägrar starta utan minst en nyckel.

**Åtgärd:** Sätt minst en nyckel i `.env` (två låter `scripts/security_test.py` köra):

```sh
GEODATA_API_KEYS=$(openssl rand -hex 24),$(openssl rand -hex 24)
```

Starta sedan om: `docker compose up -d mcp`. Kontrollera:
`docker compose logs mcp | tail` ska visa att servern startat.

### 10.2 OAuth-inloggning fungerar inte

**Symtom:** Klienter (claude.ai, Claude Code, ChatGPT) kan inte logga in via webbläsare; eller
inbjudningskodsformuläret avvisar koden; eller `/.well-known/oauth-*` svarar inte.

**Kontrollera i tur och ordning:**

- **OAuth avstängt:** `GEODATA_INVITE_CODE` är tom → OAuth är avsiktligt av (bearer-nycklar
  fungerar fortfarande). Sätt en inbjudningskod i `.env` och starta om `mcp` för att aktivera.
- **Fel inbjudningskod:** användaren måste ange exakt värdet i `GEODATA_INVITE_CODE`.
- **Routing:** Caddy måste proxa `/oauth/*` och `/.well-known/oauth-*` till `mcp:8000` (inte till
  viewer). Detta ligger redan i [`deploy/Caddyfile`](../deploy/Caddyfile) — verifiera att en
  egen/ändrad Caddy-konfiguration behållit dessa `handle`-block.
- **Utfärdare (issuer):** OAuth-metadata använder `PUBLIC_BASE_URL` som issuer. Bakom TLS/proxy
  måste `PUBLIC_BASE_URL` vara den publika `https://`-adressen, annars pekar
  discovery-endpointerna fel.
- **Tokens/klienter:** persisteras i `app.oauth_clients`/`app.oauth_tokens` (migration
  `004_oauth.sql`). Har migrationen inte körts på en äldre installation misslyckas inloggningen —
  se avsnitt 9. Auktoriseringskoder är kortlivade (5 min, engångs) och hålls i minnet; en omstart
  av `mcp` mitt i ett inloggningsflöde tvingar användaren att börja om (redan utfärdade tokens
  överlever).

### 10.3 Connector-fel (skörd/ingest misslyckas)

**Symtom:** Ett `load`- eller `analyze`-jobb hamnar i status `error`; `load(op='jobs')` eller
`app.jobs` visar felet.

**Diagnos:**

```sh
docker compose logs -f worker                      # följ jobbkörningen
docker compose exec -T postgres psql -U postgres -d geodata \
  -c "SELECT id, kind, status, error, attempts FROM app.jobs ORDER BY id DESC LIMIT 20;"
```

**Vanliga orsaker och åtgärder:**

- **Autentiserad källa utan uppgifter:** Lantmäteriet-tjänster (`*.lantmateriet.se`) kräver
  `LANTMATERIET_CREDENTIALS=user:password` i `.env`; andra värdar via `GEODATA_HTTP_CREDENTIALS`.
  Uppgifterna är endast worker/viewer-miljö och loggas aldrig.
- **Tom källa (0 features):** vissa lager är genuint tomma vid källan (t.ex. `ovriga_byggnader`,
  `numberMatched=0`) — jobbet lyckas men bär en `warning`. Detta är korrekt, inte ett fel.
- **Namnkollision:** ingest vägrar skriva över en `ref`-tabell som redan ägs av ett annat dataset.
  Välj ett annat `table_name` eller ta bort det gamla datasetet först.
- **Källa nere/timeout:** kontrollera nätåtkomst till `karta.sundsvall.se`, `isyroad.isy.se`
  eller Lantmäteriet. Jobbet försöker igen (max 2 attempts) och kan sedan köas om.
- **Krasch mitt i jobb:** vid omstart sveper worker jobb som fastnat i `running` och köar om dem
  (eller failar när attempts är slut). Kör inte fler än en worker-replik utan lease-baserad
  återhämtning (se avsnitt 11).

Kör `scripts/connector_test.py` för att verifiera alla connector-typer mot de officiella källorna.

### 10.4 Tomma kartlager (kartan renderar inget)

**Symtom:** En kartvy öppnas men ett eller flera lager syns inte.

**Kontrollera:**

- **Origo saknar positron/OSM-bakgrund — avsiktligt.** Origo tvingar varje källa till kartans
  egen projektion (EPSG:3014), så en Web-Mercator XYZ-bakgrund skulle rendera tomt. Kompilatorn
  utelämnar den och sidan visar en förklarande notis. Använd `map(basemap='wms:<dataset id>')`
  med ett WMS-lager (t.ex. `SundsvallsKommun:Kartbakgrund_yta`) för bakgrund i Origo.
- **Länsomfattande data.** Kartverket publicerar länsdata (Västernorrland), inte enbart
  Sundsvall. Ett lager kan se "tomt" ut i kommunen om det ägs nationellt/regionalt — se
  README:s tabell och klipp mot `ref.kommungrans` vid behov.
- **Capability-check:** `/data`- och `/tiles`-endpoints kräver att lagret refereras av just den
  vyn (`view`-parametern). Ett lager som inte ingår i vyns spec serveras inte.
- **Stora lager:** över 20 000 features serveras som vektortiles (MVT); kontrollera att
  `/tiles/...`-anropen returnerar 200 (tom tile → 204 är normalt utanför datats utbredning).
- **Geometrityp:** GML-kurvgeometrier lineariseras vid ingest; MULTICURVE/MULTISURFACE som inte
  linjäriserats renderas inte. Kontrollera att ingest kördes med `-nlt CONVERT_TO_LINEAR`.
- **Kontroll av utbredning:** verifiera att vyns `extent_3014` faktiskt omsluter datat, annars
  hamnar kartan utanför lagren.

### 10.5 ETag-cache / kartan uppdateras inte i webbläsaren

**Symtom:** En ändring via `layer`/`map` syns inte i en öppen kartsida.

**Bakgrund:** MapLibre-sidan pollar `/v/<id>/style.json` var 4:e sekund med `If-None-Match` och
tillämpar ändringar via `setStyle({diff:true})` inom ~5 s. Servern svarar `ETag: W/"<version>"`
och `304 Not Modified` när inget ändrats.

**Kontrollera:**

- **Versionen måste ha ökat:** varje `map(op='upsert')` höjer `version`. Syns inte ändringen,
  bekräfta att en ny version faktiskt skrevs (`map(op='get')` eller `app.map_views.version`).
- **Origo hot-uppdaterar inte:** OpenLayers saknar MapLibres style-diff, så Origo-sidan visar i
  stället en "Kartan uppdaterad — ladda om"-knapp. Detta är förväntat beteende, inte ett fel.
- **Mellanliggande cache:** en proxy eller CDN som ignorerar/normaliserar `ETag`/`If-None-Match`
  kan bryta pollningen. Säkerställ att svarshuvudena `ETag` och `Cache-Control` passerar oförändrade
  genom eventuell extern lastbalanserare (Caddy vidarebefordrar dem korrekt).
- **Ladda om hårt** (`Cmd/Ctrl+Shift+R`) för att verifiera att den senaste stilen faktiskt
  serveras om webbläsarens egen cache misstänks.

### 10.6 Sök faller tillbaka till enbart trigram (inga semantiska träffar)

**Symtom:** `search` returnerar `embedding_used: false`.

**Orsak:** Första embedding-körningen laddar ner ~1,2 GB modellvikter till `hf-cache`. Första
`/embed`-anropet efter en worker-omstart kan överskrida sökverktygets embed-timeout (~6 s), och
search faller då tillbaka till enbart trigram för det anropet.

**Åtgärd:** Vänta tills modellen laddats (`worker`-loggen; `/healthz` → `model_loaded: true`) och
sök igen. Efterföljande anrop använder embeddings normalt.

### 10.7 Förändringsdetektering misslyckas

**Symtom:** `analyze(op='run', id='change_detect')` failar eller returnerar inget.

**Kontrollera:** att segmenter-tjänsten körs på värden (`curl http://localhost:8200/healthz`),
att `SAM3_URL` pekar rätt (default `http://host.docker.internal:8200`), och att pilotdata finns.
Ett 5xx-svar från segmentern avbryter jobbet avsiktligt (en trasig backend får inte ge ett
"rent" nollresultat). På en Linux/GPU-värd används `SAM3_BACKEND=transformers` (kräver
HF-åtkomst till `facebook/sam3`). Se [`services/segmenter/README.md`](../services/segmenter/README.md).

---

## 11. Kända begränsningar som påverkar drift

Följande är designval som är viktiga att känna till före en flerpersoners-/produktionsdriftsättning
(fördjupning i README:s "What's left" och arkitekturdokumentets §11):

- **Workspaces är namnrymd, inte tenancy.** All agent-SQL körs som en delad läsroll (`agent_ro`);
  varje autentiserad principal kan läsa varje workspace. Skrivningar är workspace-scopade, läsningar
  inte. Läsisolering mellan principaler kräver RLS eller en databasroll per nyckel.
- **Uppskalning av `mcp`:** streamable-HTTP-sessioner hålls i processminnet. Flera repliker kräver
  sticky sessions (eller SDK:ns tillståndslösa läge). Alla övriga tjänster är tillståndslösa.
- **Flera workers:** krascháterhämtningen är byggd för en enda worker (startsvepet köar om alla
  `running`-jobb utan lease/worker-id). En andra replik skulle stjäla sina kollegers jobb — inför
  lease-baserad återhämtning först.
- **API-nyckelhantering är manuell** (avsnitt 4.2): ingen utgång/rotationsautomatik ännu.
- **Exporter och workspaces har ingen storleks-/åldringsgräns** (housekeeping): sätt upp
  MinIO-livscykelregel och bevaka schematillväxt.

För dimensionering vid pilot respektive produktion, se [`kapacitet.md`](kapacitet.md).
