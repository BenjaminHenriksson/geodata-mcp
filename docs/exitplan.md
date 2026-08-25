# Exitplan och övertagbarhet (ska-krav #56, #57, #97, #65)

Detta dokument beskriver hur Sundsvalls kommun kan **ta över, driva vidare och
avveckla** Geodata MCP v2 helt utan den ursprungliga leverantören, hur all data
och konfiguration exporteras i **öppna, leverantörsneutrala format**, samt en
**tidplan** för ett kontrollerat överlämnande. Målet är att det aldrig ska
uppstå någon inlåsning (*lock-in*) – varken tekniskt, avtalsmässigt eller
kompetensmässigt.

| Krav | Innebörd | Var det uppfylls |
| --- | --- | --- |
| **#56** | Exitplan för övertagande utan ursprunglig leverantör | Avsnitt 1–4 |
| **#57** | Dataexport i öppet, dokumenterat format | Avsnitt 2 |
| **#97** | Inga inlåsningar (öppna standarder, öppen källkod) | Avsnitt 3 |
| **#65** | Beställaren äger och kontrollerar data och lösning | Avsnitt 2 + `docs/dataskydd-gdpr.md` |

## 1. Utgångsläge: allt beställaren behöver finns i leveransen

Hela lösningen levereras som **öppen källkod under AGPL-3.0-only** (se `LICENSE`
och `docs/adr/0010-agpl-3-0-only-pmpc.md`). Det innebär i praktiken att det inte
finns någon komponent som kommunen inte får läsa, ändra, bygga om och drifta i
egen regi. Källkod, byggrecept (`Dockerfile` per tjänst), infrastruktur som kod
(`docker-compose.yml`, `deploy/Caddyfile`), databasschema och migrationer
(`db/init`, `db/migrations`), samt fullständig dokumentation (`README.md`,
`CONTRACTS.md`, `geodata-mcp-architecture.md`, `docs/`, arkitekturbeslut i
`docs/adr/`) ingår i samma repo.

En **programvarukatalog (SBOM)** i CycloneDX-format finns i
`sbom/geodata-mcp.cdx.json` och genereras om med `scripts/gen_sbom.sh`. Den ger
kommunen en komplett förteckning över alla tredjepartsberoenden och deras
licenser, vilket är en förutsättning för att kunna underhålla lösningen självt.

Hela systemet startas med **ett kommando** (`docker compose up`) på valfri värd
med Docker/Podman. Ingen del kräver ett konto hos den ursprungliga leverantören,
ingen molntjänst och ingen proprietär licensnyckel.

## 2. Dataexport i öppet format (#57, #65)

Beställaren äger all data. Den kan lämna systemet i öppna, dokumenterade format
på tre nivåer, från finkornig till fullständig:

### 2.1 Per lager – via `export`-verktyget

MCP-verktyget `export` (se `CONTRACTS.md`, `services/mcp/export_ops.py`) skriver
valfritt lager eller frågeresultat till något av följande **öppna format**:

| Format | Standard | Användning |
| --- | --- | --- |
| **GeoPackage** (`.gpkg`) | OGC GeoPackage – SQLite-baserad, öppen ISO-standard | Fullständig geodata med attribut och geometri, direkt läsbar i QGIS |
| **GeoJSON** (`.geojson`) | RFC 7946 | Utbyte, webb, versionshantering |
| **CSV** | RFC 4180 | Attributdata, kalkylark |
| **Parquet** (`.parquet`) | Apache Parquet – öppen kolumnär standard | Stora datamängder, analys |

Med flaggan `cite=True` bifogas dessutom en **proveniens-markdown** som
dokumenterar varje lagers ursprung (källtjänst, hämtningstidpunkt, licens) – se
`services/mcp/provenance.py` och `app.query_log`. Exporten läggs i objektlagret
(MinIO/S3) och nås via en tidsbegränsad länk.

### 2.2 Hela databasen – standard PostgreSQL/PostGIS-dump

All beständig data ligger i **en enda PostGIS-databas** (arkitekturbeslut
`docs/adr/0001-postgis-enda-tillstandslager.md`). Ingen data finns låst i
applikationsminne eller proprietära filformat. Fullständig export sker med
standardverktyg:

```sh
# Logisk dump av hela databasen (schema + data), portabelt textformat
pg_dump --format=custom --dbname="$DATABASE_URL_APP" --file=geodata.dump

# Eller ren SQL som kan läsas av vilken PostgreSQL-installation som helst
pg_dump --dbname="$DATABASE_URL_APP" --file=geodata.sql
```

PostGIS är i sig öppen källkod (samma sak som databasen), så dumpen kan
återläsas i valfri egen PostgreSQL-instans utan leverantörsberoende. Geometrier
lagras i standard `geometry`-typ med EPSG-koder (SWEREF 99 / EPSG:3006, 3011,
3014 m.fl.) enligt OGC Simple Features – inget eget koordinatformat.

### 2.3 Exportfiler i objektlagret

Genererade exporter och rasterartefakter ligger i **MinIO**, ett S3-kompatibelt
objektlager (arkitekturbeslut `docs/adr/0008-minio-s3-exportlagring.md`). De kan
speglas ut med vilket S3-kompatibelt verktyg som helst (`mc mirror`, `aws s3
sync`, `rclone`) till kommunens egen lagring.

## 3. Inga inlåsningar – öppna standarder rakt igenom (#97)

Lösningen är byggd så att varje lager kan bytas ut mot en likvärdig öppen
komponent. Inget steg förutsätter en specifik leverantör.

| Skikt | Komponent | Öppen standard / substituerbarhet |
| --- | --- | --- |
| Licens | AGPL-3.0-only | Public Money, Public Code – fri att drifta, ändra, dela |
| Körning | Docker Compose | OCI-images; kör lika gärna på Podman, Kubernetes eller OpenShift |
| Databas | PostgreSQL 17 + PostGIS + pgvector | Öppen källkod; standard SQL och OGC-geometri |
| Objektlager | MinIO | S3-API – utbytbart mot vilket S3-kompatibelt lager som helst |
| Reverse proxy | Caddy | Utbytbart mot nginx/Traefik; konfig är läsbar text |
| Agentgränssnitt | MCP (Model Context Protocol) över streamable HTTP | Öppet protokoll (`docs/adr/0002-mcp-granssnitt-fastmcp.md`) |
| Auth | OAuth 2.1 + PKCE och bearer-nycklar | RFC 8414/7591/9728 (`services/mcp/oauth.py`) |
| Kartrendering | MapLibre GL och Origo/OpenLayers | Två öppna renderare från samma vy-spec (`docs/adr/0004-tva-renderare-via-compilers.md`) |
| Dataendpunkter | GeoJSON (RFC 7946) + Mapbox Vector Tiles | Öppna format; dokumenterad uppgraderingsväg till OGC API Features/Tiles (§5.3) |
| Observerbarhet | JSON-loggar, Prometheus, OpenTelemetry (OTLP) | Öppna standarder, inga proprietära agenter (`docs/observability.md`) |
| Embeddings | EmbeddingGemma-300M (öppen vikt, körs lokalt) | Ingen extern API-tjänst; modellen kan bytas |

**Den enda komponenten med icke-öppen licens** är SAM 3-vikterna (Meta SAM
License), som används för ortofoto-förändringsdetektering. Den är avsiktligt
isolerad bakom ett HTTP-kontrakt (`SAM3_URL`) som en **separat, valfri tjänst**
(`services/segmenter`) och krävs inte för kärnfunktionen. Den kan avaktiveras
utan att övriga systemet påverkas, och kontraktet är utformat så att en annan
segmenteringsmodell kan svappas in (se
`docs/adr/0007-sam3-ortofoto-forandringsdetektering.md`). Licensen granskas
separat före produktionssättning.

## 4. Övertagande utan ursprunglig leverantör (#56)

Följande krävs för att kommunen (eller en tredje part som kommunen anlitar) ska
kunna ta över drift och vidareutveckling:

1. **Källkod och historik** – hela git-repot, inklusive `docs/adr/` som
   förklarar *varför* varje central designbeslut togs.
2. **Kör-recept** – `.env.example` (mall för konfiguration), `docker-compose.yml`
   och `deploy/Caddyfile`. Kommunen skapar sitt eget `.env` med egna lösenord och
   nycklar; inget hemligt värde är inbäddat i koden.
3. **Data** – enligt avsnitt 2.
4. **Kompetens** – strukturerad kunskapsöverföring enligt
   `docs/kunskapsoverforing.md` (ska-krav #58).
5. **Kvalitetsnät** – de medföljande testerna (`scripts/e2e_test.py`,
   `scripts/security_test.py`, `scripts/connector_test.py`,
   `scripts/validate_data.py`, `scripts/loadtest.js`) låter en ny förvaltare
   verifiera att en egen driftmiljö beter sig korrekt.

Ingen av dessa punkter förutsätter medverkan från den ursprungliga leverantören
efter överlämnandet.

## 5. Tidplan för överlämnande

Tidplanen är indikativ och anpassas efter kommunens förvaltningsorganisation.
Den förutsätter att en mottagande förvaltare (intern IT eller anlitad part) är
utsedd.

| Fas | Tidsram | Innehåll | Resultat |
| --- | --- | --- | --- |
| **0. Förberedelse** | Vecka 0 | Mottagare utses; åtkomst till repo, SBOM och dokumentation | Mottagaren har allt material |
| **1. Miljöuppsättning** | Vecka 1–2 | Kommunen bygger och startar systemet i egen miljö från `docker-compose.yml`; kör testsviterna | Fungerande egen instans, gröna tester |
| **2. Dataöverföring** | Vecka 2–3 | `pg_dump` + S3-spegling till kommunens miljö; verifiering med `scripts/validate_data.py` | All data i kommunens kontroll |
| **3. Kunskapsöverföring** | Vecka 2–4 | Genomgångar enligt `docs/kunskapsoverforing.md`; skuggdrift | Förvaltaren driftar självständigt |
| **4. Parallelldrift** | Vecka 4–6 | Kommunens instans körs parallellt; incident- och SLA-rutiner (`docs/sla.md`) etableras | Verifierad driftförmåga |
| **5. Formellt övertagande** | Vecka 6 | Leverantörens instans kan stängas; åtkomster återkallas | Kommunen ensam driftansvarig |
| **6. Avveckling (vid behov)** | Löpande | Säker radering: `docker compose down -v`, tömning av S3-bucket, nyckelrotation | Kontrollerad avveckling utan kvarlämnad data |

**Säker avveckling.** När systemet ska avvecklas raderas all beständig data
genom att ta bort databasvolymen (`docker compose down -v`) och tömma
objektlagret. API-nycklar och `VIEWER_SECRET` roteras/ogiltigförklaras. Eftersom
ingen data ligger hos en tredje part krävs ingen samordning med extern part för
att garantera radering – detta stödjer även GDPR-kravet på gallring (se
`docs/dataskydd-gdpr.md`).

## 6. Sammanfattning

Övertagbarheten vilar på tre pelare: **öppen källkod** (AGPL, hela systemet),
**öppna dataformat** (GeoPackage/GeoJSON/CSV/Parquet + standard PostGIS-dump) och
**öppna driftstandarder** (OCI, S3, OGC, OTLP). Det finns ingen teknisk,
avtalsmässig eller kompetensmässig punkt där kommunen är beroende av den
ursprungliga leverantören för att fortsätta driva, vidareutveckla eller avveckla
lösningen.
