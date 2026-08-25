# Plan för oberoende säkerhetsgranskning (ska-krav #33, #30)

Detta dokument beskriver dels **planen för en oberoende säkerhetsgranskning**
(penetrationstest och kodgranskning) innan produktionssättning (#33), dels en
**kartläggning mot OWASP Top 10 (2021)** av vilka säkerhetskontroller lösningen
redan har (#30). Kartläggningen är grundad i den kod som faktiskt implementerar
kontrollerna – framför allt `services/mcp/sqlguard.py`,
`services/mcp/oauth.py`, `services/viewer/main.py` och regressionstesterna i
`scripts/security_test.py`.

| Krav | Innebörd | Var det uppfylls |
| --- | --- | --- |
| **#33** | Oberoende säkerhetsgranskning (pentest/kodgranskning) före produktion | Avsnitt 1–3 |
| **#30** | OWASP Top 10 beaktat och avbildat mot kontroller | Avsnitt 4 |

## 1. Syfte och omfattning

Innan lösningen tas i produktionsdrift ska en **oberoende part** (en aktör utan
koppling till utvecklingen) genomföra en säkerhetsgranskning i två delar:

1. **Kodgranskning (white-box)** av källkoden – möjliggjord av att hela systemet
   är öppen källkod (AGPL-3.0-only), inklusive SBOM (`sbom/geodata-mcp.cdx.json`)
   för beroendeanalys.
2. **Penetrationstest (grey-/black-box)** mot en driftsatt instans, med fokus på
   den agent-exponerade `/mcp`-ytan, den publika `viewer`-ytan och
   autentiseringsflödena.

### I omfattning

- MCP-kontrollplanet (`services/mcp`): bearer- och OAuth-autentisering, SQL-
  validering (`sqlguard.py`), verktygens behörighetskontroller, workspace-
  isolering.
- `viewer` (`services/viewer`): kartsidor, `/data`/`/tiles`/`/wmsref`-endpoints,
  workspace-manager-UI:t, CSP och XSS-skydd.
- Reverse proxy och TLS-terminering (`deploy/Caddyfile`).
- Databasroller och rättigheter (`db/init`), objektlager (MinIO) och presignerade
  URL:er.
- Hemlighetshantering (`.env`) och nyckelrotation.
- Beroenden och kända sårbarheter (via SBOM).

### Utanför omfattning (men noteras)

- SAM 3-vikternas licens (hanteras separat, ADR 0007) – inte en säkerhetsfråga
  men noteras för fullständighet.
- Kommunens egen kringinfrastruktur (nät, DNS) om den drivs separat.

## 2. Metod och referensramar

Granskningen ska följa etablerade, öppna ramverk:

| Ramverk | Användning |
| --- | --- |
| **OWASP Top 10 (2021)** | Checklista för webb-/API-risker (avsnitt 4) |
| **OWASP ASVS** | Verifieringsnivå för autentisering, åtkomst, indata |
| **OWASP API Security Top 10** | Relevant för `/mcp`- och `/data`-API:erna |
| **OWASP WSTG** | Testmetodik för penetrationstestet |

Automatiserade hjälpmedel (SAST/DAST/dependency-scanning) får användas som stöd,
men fynd ska verifieras manuellt. De medföljande regressionstesterna
(`scripts/security_test.py`) är en **utgångspunkt, inte en ersättning** för den
oberoende granskningen – de bevisar att kända gränser hålls, men granskaren ska
söka det de inte täcker.

## 3. Process, klassning och åtgärd

1. **Planering** – granskaren får repo, SBOM, arkitekturdokument (`docs/adr/`,
   `geodata-mcp-architecture.md`) och en testinstans med två API-nycklar (så
   cross-principal-tester kan köras, jfr `security_test.py`).
2. **Genomförande** – kodgranskning + pentest enligt avsnitt 2.
3. **Rapportering** – fynd klassas efter allvarlighetsgrad (t.ex. CVSS):
   Kritisk / Hög / Medel / Låg / Informativ.
4. **Åtgärd** – leverantören åtgärdar enligt överenskomna tider:

   | Allvarlighetsgrad | Åtgärd före produktion |
   | --- | --- |
   | Kritisk / Hög | **Ska åtgärdas** innan produktionssättning |
   | Medel | Åtgärdas eller får en dokumenterad, tidsatt plan |
   | Låg / Informativ | Backlog, riskaccepteras med motivering |

5. **Omtest** – granskaren verifierar att Kritiska/Höga fynd är stängda.
6. **Utlåtande** – ett granskningsintyg utfärdas som underlag för
   produktionsbeslut. Granskningen upprepas vid större förändringar och
   återkommande enligt avtal.

**Kända, dokumenterade gränser** (se README "Security boundaries") ska tas upp
uttryckligen med beställaren före driftsättning, särskilt: workspaces är
namnrymd, inte tenancy (delad `agent_ro`-roll ⇒ autentiserade principaler kan
*läsa* varandras workspaces – skrivning är dock isolerad); statiska API-nycklar
utan automatisk rotation; kartvyer som capability-URL:er. Dessa är avsiktliga
designval med dokumenterade uppgraderingsvägar (RLS/roll-per-nyckel), inte
förbisedda buggar.

## 4. OWASP Top 10 (2021) → kontroller

Kartläggning av varje kategori mot de kontroller som finns i koden idag.
"Test" hänvisar till en kontroll i `scripts/security_test.py`.

### A01:2021 – Broken Access Control

- `agent_ws` får endast `CREATE` i den anropande nyckelns aktiva
  `ws_<8 hex>`-schema; `WS_SCHEMA_RE` (`sqlguard.py`) validerar schemanamnet.
- `app.api_keys` och `app.workspaces` är inte läsbara för agent-rollerna (grants
  per tabell, aldrig schema-brett) – *Test:* "app.api_keys not readable",
  "app.workspaces not readable".
- En principal kan inte skriva över en annans workspace eller kartvy – *Test:*
  "cross-principal view upsert rejected", "cross-principal workspace write
  rejected".
- `map(op='get')` returnerar aldrig ägar-id (kartvyer är delbara) – *Test:* "map
  get does not expose workspace_id".
- `/data`, `/tiles` och `/wmsref` är capability-kontrollerade: lagret måste ingå
  i den namngivna vyn (`_checked_layer`, `services/viewer/main.py`).
- Workspace-manager-UI:t kontrollerar ägarskap på varje åtgärd och kräver en
  per-principal **CSRF-token** (`viewer_auth.csrf_ok`).
- **Dokumenterad gräns:** läsisolering mellan principaler kräver RLS/roll-per-
  nyckel (avsnitt 3).

### A02:2021 – Cryptographic Failures

- API-nycklar lagras **endast som SHA-256-digest**, aldrig i klartext
  (`sqlguard`/sessions; README "Security boundaries").
- Manager-cookien är signerad med `VIEWER_SECRET` och sätts `HttpOnly` +
  `SameSite=Lax` (`services/viewer/main.py`, `viewer_auth.py`).
- TLS termineras i reverse proxy (Caddy) före produktion; DB- och MinIO-portar är
  host-only (`docker-compose.yml`).
- Exportlänkar är presignerade S3-URL:er med tidsbegränsad signatur.

### A03:2021 – Injection

Kärnkontrollen finns i `services/mcp/sqlguard.py`:

- **Identifierare valideras och citeras** via `psycopg.sql.Identifier`/`Composed`
  (`ident()`, `qualified()`); reguljäruttryck (`IDENT_RE`, `LAYER_NAME_RE`,
  `LAYER_REF_RE`) släpper bara igenom förväntade namn.
- **En sats i taget** – `clean_single_statement()` avvisar allt som innehåller
  `;` (även i stränglitteraler), vilket stoppar staplade satser.
- **Endast läsning på query-vägen** – `validate_readonly()` kräver att satsen
  börjar med `SELECT/WITH/EXPLAIN/SHOW/TABLE/VALUES`; skrivningar går via
  `layer`-verktyget. Transaktionen sätts dessutom `READ ONLY`. *Test:* "SELECT
  INTO TEMP rejected", "query transaction is read-only".
- **EXPLAIN-plan-analys** (`explain_input_tables()`) härleder vilka tabeller en
  fråga rör för proveniens/behörighet.
- **XSS (injection i webbklienten):** CSP utan `'unsafe-inline'` på varje
  HTML-sida (egna skript via per-response-nonce), plus escaping av agent-levererade
  etiketter i Origo-konfigen. *Test:* "origo config escapes agent-supplied layer
  labels", "origo page sends a script-src CSP without unsafe-inline".

### A04:2021 – Insecure Design

- **Försvar på djupet:** perimeter-auth (`/mcp` 401), read-only-roll,
  sats-guard, transaktions-read-only och radgräns verkar tillsammans.
- Avsiktliga designgränser är **dokumenterade** (README "Security boundaries")
  snarare än dolda: capability-URL:er, namnrymd vs tenancy. Arkitekturbesluten
  ligger i `docs/adr/`.

### A05:2021 – Security Misconfiguration

- Säkerhetsheaders på HTML-svar: CSP, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer` (`_html()` i `services/viewer/main.py`).
- Databas och MinIO publiceras **endast på `127.0.0.1`** – Dockers iptables
  kringgår annars värdens brandvägg (kommentar i `docker-compose.yml`).
- Inga hemligheter i koden: allt kommer från `.env` (gitignorerad;
  `.env.example` är mallen). `mcp` vägrar starta utan `GEODATA_API_KEYS`.

### A06:2021 – Vulnerable and Outdated Components

- **SBOM i CycloneDX** (`sbom/geodata-mcp.cdx.json`), regenereras med
  `scripts/gen_sbom.sh`, ger komplett beroende- och licensförteckning för
  sårbarhetsbevakning.
- Alla beroenden ska vara AGPL-kompatibla och licensgranskas innan de tas in
  (ADR 0010).

### A07:2021 – Identification and Authentication Failures

- `/mcp` avvisar okänd/avstängd nyckel med `401` + `WWW-Authenticate` – *Test:*
  "unauthenticated request gets 401", "bad api key gets 401".
- OAuth 2.1 + **PKCE** för browser-login (`services/mcp/oauth.py`), med RFC
  8414/7591/9728-metadata.
- Digest-cache på 30 s ⇒ en avstängd nyckel slås ut inom det fönstret.
- Transportens `mcp-session-id` bär ingen identitet; en förfalskad id avvisas –
  *Test:* "forged mcp-session-id rejected by transport".

### A08:2021 – Software and Data Integrity Failures

- Frontend-bundlar (MapLibre/Origo) är **vendrade i imagen** – inget hämtas från
  CDN vid körning (README "Two renderers").
- Ingest vägrar skriva över en `ref`-tabell som redan ägs av ett annat dataset
  (README; ingest-lagret).
- Presignerade URL:er har integritetsskyddad signatur; SBOM stödjer verifiering
  av leveranskedjan.

### A09:2021 – Security Logging and Monitoring Failures

- **pgAudit** förladdas i Postgres (`docker-compose.yml`) och loggar agent-
  rollernas satser.
- Provenienstabellen `app.query_log` spårar frågor (`services/mcp/provenance.py`).
- Strukturerade JSON-loggar, Prometheus `/metrics` och OpenTelemetry-spår – se
  `docs/observability.md`.
- **Känd begränsning:** pgAudit täcker agent-rollerna, inte `geodata_app`
  (README "What's left") – tas upp i granskningen.

### A10:2021 – Server-Side Request Forgery (SSRF)

- `/wmsref`-proxyn (`services/viewer/main.py`) proxar **endast `GetMap`** och
  **endast mot en katalogförd dataset-URL** som ingår i vyn – klienten kan inte
  styra måladressen fritt; behörighet kontrolleras mot vyn innan uppström anropas.
- Connectors i `worker` hämtar enbart från katalogförda källor
  (`data_sources.xlsx`); autentiserade källor använder host-scopade
  credentials (`LANTMATERIET_CREDENTIALS`/`GEODATA_HTTP_CREDENTIALS`).

## 5. Sammanfattning

Lösningen har redan konkreta, testade kontroller mot samtliga tio OWASP-
kategorier, med regressionstester (`scripts/security_test.py`) som bevisar de
centrala gränserna. Den oberoende granskningen enligt avsnitt 1–3 ska verifiera
dessa kontroller under verkliga förhållanden, pröva det som testerna inte täcker
och bekräfta att alla Kritiska/Höga fynd är åtgärdade **innan produktionssättning**.
Den regulatoriska hållningen (NIS2, Cybersäkerhetslagen, EU Cybersecurity Act)
redovisas i `SECURITY.md`.
