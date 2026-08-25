# Regelefterlevnad — Geodata MCP v2

Detta dokument är en samlad **regelefterlevnadskartläggning** som anbudet kan
hänvisa till. För varje relevant regelverk anges kortfattat *hur lösningen och
leverantören uppfyller kravet* samt en **status**. Underlaget är avsett att
läsas av beställarens jurister, informationssäkerhets- och dataskyddsfunktioner
(Sundsvalls kommun, upphandling UH-2026-159, Govtech4all Pilot 3 "AI och
geodata").

Leveransen släpps under **AGPL-3.0-only** (överenskommet med beställaren;
ersätter anbudsunderlagets ursprungliga EUPL/GPLv3-skrivning — se
[`LICENSE`](../LICENSE) och [`docs/adr/0010-agpl-3-0-only-pmpc.md`](adr/0010-agpl-3-0-only-pmpc.md)).

## Ansvarslinje: vad leverantören svarar för

Flera av regelverken nedan (NIS2, Cybersäkerhetslagen, GDPR, DORA, ISO/IEC
27001) riktar sig i första hand mot en **verksamhetsutövare/personuppgifts­ansvarig
organisation**, inte mot en enskild programvara. Ansvaret för efterlevnad *som
helhet* — riskanalys, klassning, incidentrapportering till tillsynsmyndighet,
registerförteckning m.m. — åligger därför **Sundsvalls kommun** som
verksamhetsutövare och personuppgiftsansvarig.

Leverantörens åtagande är att tillhandahålla en lösning vars **tekniska och
organisatoriska kontroller, dokumentation och processer** ger kommunen konkret
stöd att uppfylla dessa krav, samt att leverantören som **IKT-tjänste-/produkt­leverantör**
följer god praxis för säker utveckling och leveranskedja. Statuskolumnen ska
läsas i det ljuset.

### Så läser du statuskolumnen

| Status | Innebörd |
| --- | --- |
| **Uppfylls** | Kravet uppfylls direkt av lösningen med tekniska kontroller som kan verifieras i leveransen. |
| **Uppfylls (tekniskt stöd)** | Lösningen tillhandahåller de tekniska/organisatoriska åtgärder regelverket kräver; efterlevnad som helhet ligger hos kommunen som verksamhetsutövare. |
| **Stöds** | Regelverket är inte ett direkt produktkrav, men lösningen är utformad så att beställarens efterlevnad (eller framtida certifiering) underlättas. |
| **Ej tillämpligt (noterat)** | Regelverket träffar inte kommunal verksamhet eller den valda driftmodellen; ändå relevanta principer noteras. |

---

## Driftmodell — förutsättning för bedömningen

Regelefterlevnaden nedan utgår från den **föreslagna driftmodellen**:

> **Lösningen driftsätts on-prem/i egen regi, containeriserad, på kommunens
> egen containerplattform (Red Hat OpenShift).** Leverantören levererar inte en
> leverantörshostad SaaS-tjänst. All beständig data (Postgres/PostGIS och MinIO)
> och alla containeravbildningar körs inom kommunens egen infrastruktur och
> kontroll.

Detta val är avgörande för flera krav:

- **Krav #84–#90 (drift på containerplattform/OpenShift) adresseras.** En
  Helm-chart under [`deploy/helm/geodata-mcp/`](../deploy/helm/geodata-mcp)
  paketerar samtliga sex tjänster för OpenShift/Kubernetes: native
  OpenShift `Route` med TLS-terminering (#34), hemligheter från kommunens egen
  secret store i stället för `.env` (#59), horisontell skalning och
  zero-downtime-utrullning (#40/#89), Prometheus-mätvärden via `ServiceMonitor`
  (#81) och resursförfrågningar/-gränser för kapacitetsplanering (#80). Se
  `values.yaml` (som uttryckligen refererar dessa krav-id), samt
  [`docs/drifthandbok.md`](drifthandbok.md), [`docs/kapacitet.md`](kapacitet.md),
  [`docs/observability.md`](observability.md) och `deploy/README.md`.
- **Krav #91–#99 (som avser en leverantörshostad SaaS-modell) är i huvudsak
  ej tillämpliga** eftersom lösningen inte driftas som SaaS av leverantören.
  De noteras ändå, och de två materiellt viktiga i det spannet — **DORA (#94)**
  och **ISO/IEC 27001 (#95)** — behandlas separat i tabellen nedan.

Att köra i egen regi under öppen källkod (AGPL-3.0-only) ger också en viktig
efterlevnadsfördel: kommunen äger och kontrollerar hela stacken, vilket direkt
stödjer suveränitet, exit/övertagbarhet och undvikande av inlåsning (se
[`docs/exitplan.md`](exitplan.md)).

---

## Sammanfattande efterlevnadstabell

| # | Regelverk | Hur lösningen/leverantören uppfyller detta | Status | Underlag |
| --- | --- | --- | --- | --- |
| **#27** | **NIS2** (Direktiv (EU) 2022/2555) | Levererar de tekniska riskhanteringsåtgärder art. 21 efterfrågar: åtkomstkontroll (bearer/OAuth2, roller), kryptering i transit (TLS via Caddy/OpenShift Route), central loggning/övervakning, incidenthanteringsunderlag, säker utveckling (SAST/beroendegranskning) och leveranskedjesäkerhet (SBOM). | Uppfylls (tekniskt stöd) | `SECURITY.md`, `docs/observability.md`, `docs/beroenden.md`, `sbom/`, `.github/workflows/` |
| **#28** | **Cybersäkerhetslagen** (SFS 2018:1174) | Sveriges genomförande av NIS. Samma tekniska kontroller som för #27 stödjer kommunens skyldigheter som leverantör av samhällsviktig tjänst; kontrollerna är utformade för att också bära den kommande cybersäkerhetslag som genomför NIS2. | Uppfylls (tekniskt stöd) | `SECURITY.md`, `docs/drifthandbok.md`, `docs/observability.md` |
| **#29** | **EU Cybersecurity Act** (Förordning (EU) 2019/881) | Ingen obligatorisk certifiering (t.ex. EUCC) gäller idag för denna produktkategori. Lösningen är förberedd för framtida certifiering genom SBOM, spårbar leveranskedja, dokumenterade kontroller och säker-utvecklingspipeline. | Stöds | `sbom/`, `docs/beroenden.md`, `.github/workflows/codeql.yml`, `SECURITY.md` |
| **#30** | **OWASP Top 10** (ref: 2021 års lista) | Konkreta applikationskontroller mot de vanligaste kategorierna (åtkomstkontroll, injektion, kryptografi, felkonfiguration, sårbara komponenter, autentisering, loggning) plus automatiserad SAST, beroende- och hemlighetsskanning i CI. | Uppfylls | `SECURITY.md`, `scripts/security_test.py`, `.github/workflows/ci.yml`, `.github/workflows/codeql.yml` |
| **#63** | **GDPR** (Förordning (EU) 2016/679) | Plattformen behandlar i allt väsentligt öppen geodata; personuppgiftsminimering, ändamålsbegränsning, åtkomststyrning, loggning och exportbarhet är inbyggda. Fullständig dataskyddsanalys i separat dokument. | Uppfylls | `docs/dataskydd-gdpr.md`, `SECURITY.md`, `docs/observability.md` |
| **#64** | **Data Governance Act** (Förordning (EU) 2022/868) | Underlättar regelrätt återanvändning av offentlig data: varje lager bär proveniens (källa, URL, licens), exporter genereras med citat-/proveniensbilaga, och öppna licenser (AGPL + öppna ortofoton) möjliggör vidareutnyttjande. | Stöds | `README.md` (proveniensmodell), `docs/exitplan.md`, `docs/beroenden.md` |
| **#65** | **Data Act** (Förordning (EU) 2023/2854) | Öppna standarder (OGC WFS/WMTS/OGC API/STAC) och öppna exportformat (GPKG/GeoJSON/CSV/Parquet), full portabilitet och dokumenterad exit gör data åtkomlig och byte av leverantör/plattform friktionsfritt — kärnan i Data Acts interoperabilitets- och anti-inlåsningsbestämmelser. | Uppfylls | `docs/exitplan.md`, `README.md`, `CONTRACTS.md` |
| **#94** | **DORA** (Förordning (EU) 2022/2554) | DORA träffar finanssektorn och dess IKT-leverantörer, inte kommunal verksamhet — därav ej tillämpligt. Resiliensprinciperna (IKT-riskhantering, backup/PITR, hälsokontroller, incidenthantering, leverantörskedjekontroll) stöds ändå. | Ej tillämpligt (noterat) | `docs/drifthandbok.md` (backup/HA), `docs/sla.md`, `SECURITY.md` |
| **#95** | **ISO/IEC 27001** (27001:2022) | Certifiering avser en organisations ledningssystem (LIS/ISMS), inte en enskild produkt. Lösningens tekniska kontroller är kartlagda mot Annex A och kan drivas inom beställarens befintliga ISMS; leverantören arbetar enligt motsvarande rutiner. | Stöds | `SECURITY.md`, `docs/drifthandbok.md`, `docs/observability.md` |

---

## Per regelverk

### NIS2 (#27) och Cybersäkerhetslagen SFS 2018:1174 (#28)

NIS2-direktivet (EU 2022/2555) och dess svenska genomförande styr
riskhanterings- och rapporteringsskyldigheter för väsentliga och viktiga
entiteter. SFS 2018:1174 (*Lag om informationssäkerhet för samhällsviktiga och
digitala tjänster*) är dagens svenska NIS-lag; en ny cybersäkerhetslag som
genomför NIS2 är under införande. Kontrollerna nedan är utformade för att bära
båda.

Ansvaret för klassning som samhällsviktig verksamhet, riskanalys och
incidentrapportering till tillsynsmyndighet ligger på **kommunen som
verksamhetsutövare**. Lösningen levererar de tekniska riskhanteringsåtgärder som
art. 21 i NIS2 efterfrågar:

- **Åtkomstkontroll och autentisering** — `/mcp` avvisar varje anrop utan känd,
  aktiverad API-nyckel (401); OAuth 2.1 + PKCE med invite-code för webbinloggning;
  separata databasroller (`agent_ro` läs, `agent_ws` skriv) med minsta
  behörighet; API-nycklar lagras endast som SHA-256-hashar.
- **Kryptografi i transit** — TLS termineras i Caddy (compose) eller i
  OpenShift-routern (produktion). Se `docs/drifthandbok.md` §8 (#34).
- **Loggning och övervakning** — strukturerad JSON-logg till stdout,
  Prometheus-mätvärden och OpenTelemetry-spårning via öppna standarder, plus ett
  revisionsspår i databasen (`app.query_log`, den append-only-ledgern
  `app.provenance`, DDL-event-triggers och pgAudit). Se `docs/observability.md`
  (#81).
- **Kontinuitet och återhämtning** — dokumenterade backup-/PITR-rutiner och
  hälsokontroller per tjänst (`docs/drifthandbok.md` §6–§7).
- **Säkerhet i leveranskedjan** — SBOM (CycloneDX), beroendeförteckning med
  ersättare, automatisk beroende- och hemlighetsskanning samt SAST (se #29/#30).
- **Säker utveckling och incidenthantering** — se `SECURITY.md` för hela
  kontrollistan och rutin för sårbarhetsrapportering.

**Status: Uppfylls (tekniskt stöd).**

### EU Cybersecurity Act (#29)

Förordning (EU) 2019/881 inrättar ENISA och EU:s ramverk för
cybersäkerhetscertifiering (t.ex. EUCC). Certifiering enligt ramverket är i
dagsläget **frivillig** och det finns inget obligatoriskt certifieringskrav för
denna produktkategori. Lösningen är förberedd för att kunna genomgå en framtida
certifiering: leveranskedjan är spårbar via SBOM och beroendeförteckning,
kontrollerna är dokumenterade i `SECURITY.md`, och säker-utvecklingspipelinen
(SAST via CodeQL, beroende- och hemlighetsskanning) ger det underlag en
certifieringsprocess efterfrågar.

**Status: Stöds.**

### OWASP Top 10 (#30)

Applikationssäkerheten adresserar OWASP Top 10 (referens: 2021 års lista) med
verifierbara kontroller:

- **A01 Broken Access Control** — bearer/OAuth-grind på `/mcp`, ägarkontroll och
  per-principal CSRF-token i workspace-hanteraren, workspace-scopad skrivväg.
- **A02 Cryptographic Failures** — TLS i transit; API-nycklar aldrig i klartext
  (SHA-256).
- **A03 Injection** — agent-SQL körs som skrivskyddad roll (`agent_ro`) med
  `SET TRANSACTION READ ONLY`, statement-kind-guard, timeout och radtak; poolade
  anslutningar rensas vid återlämning.
- **A05 Security Misconfiguration** — CSP utan `'unsafe-inline'` på varje
  HTML-sida (skript via per-svars-nonce); hemligheter enbart via `.env`/secret
  store, aldrig i repo.
- **A06 Vulnerable & Outdated Components** — `pip-audit` per tjänst i CI,
  veckovis Dependabot, SBOM.
- **A07 Identification & Authentication Failures** — obligatorisk autentisering
  före varje verktyg; okända nycklar/sessioner avvisas.
- **A08 Software & Data Integrity Failures** — hemlighetsskanning (gitleaks),
  SBOM och spårad leveranskedja.
- **A09 Security Logging & Monitoring Failures** — strukturerad loggning,
  proveniens-ledger och pgAudit (se #81).

Attackregressioner är fastnaglade i `scripts/security_test.py` och statisk
säkerhetsanalys körs vid varje PR (`.github/workflows/codeql.yml`, #36/#49). Den
fullständiga kartläggningen finns i `SECURITY.md`.

**Status: Uppfylls.**

### GDPR (#63)

Plattformen behandlar i allt väsentligt **öppen geodata** (kartlager, planer,
byggnader, fastighetsgränser) och inte kategorier av känsliga personuppgifter.
Principerna om uppgiftsminimering, ändamålsbegränsning, åtkomststyrning,
loggning och rätt till dataportabilitet stöds av arkitekturen (roller med minsta
behörighet, revisionsspår, öppna exportformat). Personuppgiftsansvaret ligger
hos kommunen; leverantörens roll och eventuellt personuppgiftsbiträdesavtal,
rättslig grund, lagringsminimering och hantering av registrerades rättigheter
behandlas i sin helhet i **`docs/dataskydd-gdpr.md`**.

**Status: Uppfylls** (detaljerad analys i `docs/dataskydd-gdpr.md`).

### Data Governance Act (#64)

DGA (Förordning (EU) 2022/868) främjar tillförlitlig och regelrätt
återanvändning av data som innehas av offentlig sektor. Lösningen stödjer detta
genom sin **proveniensmodell**: varje ingesterat lager bär källa, URL och licens
i katalogen, `export`-verktyget genererar en citat-/proveniensbilaga som följer
härstamningskedjan till källorna, och hela verket (inklusive öppna ortofoton)
distribueras under öppna licenser. Det gör det möjligt för kommunen att dela och
låta vidareutnyttja data på ett spårbart och villkorsstyrt sätt.

**Status: Stöds.**

### Data Act (#65)

Data Act (Förordning (EU) 2023/2854) betonar dataåtkomst, interoperabilitet,
portabilitet och undvikande av inlåsning (bl.a. byte av databehandlingstjänst).
Lösningen svarar direkt mot detta:

- **Öppna standarder in** — WFS/WMS/WMTS/OGC API/STAC via dokumenterade
  connectorer.
- **Öppna format ut** — `export` producerar GPKG/GeoJSON/CSV/Parquet.
- **Ingen inlåsning** — öppen källkod (AGPL-3.0-only), all data i öppna,
  dokumenterade format, och en fullständig **exitplan** för övertagande utan
  ursprunglig leverantör (`docs/exitplan.md`).

**Status: Uppfylls.**

### DORA (#94)

DORA (Förordning (EU) 2022/2554) reglerar digital operativ motståndskraft för
**finansiella entiteter** och deras kritiska IKT-tredjepartsleverantörer.
Sundsvalls kommuns geodataverksamhet är inte en finansiell entitet, varför DORA
**inte är tillämpligt** på denna leverans. Kravet ligger dessutom i det
SaaS-orienterade spannet #91–#99 som i huvudsak inte gäller den valda on-prem-
modellen.

De resiliensprinciper DORA bygger på stöds ändå av lösningen: IKT-riskhantering
och åtkomstkontroll, backup med point-in-time recovery, hälsokontroller och
larm, incidenthanteringsrutin samt kontroll av leverantörskedjan via SBOM.
Driftsäkerhet och åtaganden om tillgänglighet beskrivs i `docs/sla.md` och
`docs/drifthandbok.md`.

**Status: Ej tillämpligt (noterat).**

### ISO/IEC 27001 (#95)

ISO/IEC 27001:2022 certifierar en **organisations ledningssystem för
informationssäkerhet (LIS/ISMS)** — inte en enskild programvara. En produkt kan
därför inte i sig vara "27001-certifierad". Lösningens tekniska och
organisatoriska kontroller (åtkomststyrning, kryptografi, loggning och
övervakning, säker utveckling, leverantörskedjesäkerhet, kontinuitet) är
utformade för att kartläggas mot **Annex A** och kan drivas som en del av
beställarens befintliga ISMS. Leverantören tillämpar motsvarande rutiner i sin
utveckling och förvaltning. Även detta krav ligger i det SaaS-orienterade
spannet #91–#99 men behandlas här eftersom det är materiellt relevant.

**Status: Stöds.**

---

## Underlag och spårbarhet

Kartläggningen ovan vilar på följande leveransartefakter:

| Underlag | Innehåll |
| --- | --- |
| [`SECURITY.md`](../SECURITY.md) | Samlad säkerhetskartläggning, kontrollista och rutin för sårbarhetsrapportering. |
| [`docs/dataskydd-gdpr.md`](dataskydd-gdpr.md) | Dataskydds-/GDPR-analys (#63): roller, rättslig grund, minimering, registrerades rättigheter. |
| [`docs/exitplan.md`](exitplan.md) | Exit, övertagbarhet och anti-inlåsning (#56/#57/#65/#97) — bär Data Act- och DGA-argumenten. |
| [`docs/sla.md`](sla.md) | Tillgänglighet, driftåtaganden och incidenthantering (resiliens). |
| [`deploy/README.md`](../deploy/README.md) | Driftsättningsanvisning för compose och OpenShift/Helm. |
| [`docs/drifthandbok.md`](drifthandbok.md) | Drift, hemlighetshantering (#59), backup/PITR, TLS (#34), uppgradering, felsökning. |
| [`docs/observability.md`](observability.md) | Central loggning, mätvärden och spårning via öppna standarder (#81). |
| [`docs/beroenden.md`](beroenden.md) + [`sbom/geodata-mcp.cdx.json`](../sbom/geodata-mcp.cdx.json) | Tredjepartsberoenden med licens/ersättare (#23) och maskinläsbar SBOM (#4). |
| [`.github/workflows/ci.yml`](../.github/workflows/ci.yml), [`codeql.yml`](../.github/workflows/codeql.yml), [`dependabot.yml`](../.github/dependabot.yml) | Automatiserad SAST, beroende- och hemlighetsskanning (#36/#49). |
| [`scripts/security_test.py`](../scripts/security_test.py) | Attackregressionstester (verifierbara säkerhetsgränser). |
| [`deploy/helm/geodata-mcp/`](../deploy/helm/geodata-mcp) | OpenShift/Kubernetes-paketering (#84/#34/#59/#40/#89/#80/#81). |
| [`LICENSE`](../LICENSE) + [`docs/adr/0010-agpl-3-0-only-pmpc.md`](adr/0010-agpl-3-0-only-pmpc.md) | AGPL-3.0-only, öppen källkod (Public Money, Public Code). |

## Ansvarsfördelning i korthet

| Område | Leverantör | Beställaren (Sundsvalls kommun) |
| --- | --- | --- |
| Tekniska säkerhetskontroller i lösningen | Levererar och underhåller | Konfigurerar/driftar i egen OpenShift-miljö |
| Klassning, riskanalys, incidentrapportering (NIS2/Cybersäkerhetslagen) | Ger tekniskt underlag och stöd | Ansvarig verksamhetsutövare |
| Personuppgiftsansvar (GDPR) | Personuppgiftsbiträde vid ev. behandling; se `docs/dataskydd-gdpr.md` | Personuppgiftsansvarig |
| ISMS/LIS (ISO/IEC 27001) | Kontroller kartlagda mot Annex A; egna rutiner | Äger och certifierar sitt ledningssystem |
| Hemligheter och nyckelrotation | Mekanism och rutin (#59) | Äger secret store och nycklar |
| Drift, backup, tillgänglighet | Anvisningar och SLA-underlag | Utför drift i egen regi (on-prem) |

Denna kartläggning uppdateras när ett regelverk ändras väsentligt eller när
driftmodellen ändras. Vid varje förändring ska motsvarande underlag i tabellen
ovan hållas i synk.
