# Kravuppfyllnad – Obligatoriska krav (Geodata MCP v2)

Detta dokument är leverantörens samlade **kravuppfyllnadsmatris (compliance matrix)**
för de obligatoriska ska-kraven i upphandlingen **Sundsvalls kommun UH-2026-159,
"Govtech4all Pilot 3 – AI och geodata"**. Matrisen täcker samtliga
**Icke-funktionella krav #1–#99** och **Funktionella krav #1–#3** i
anbudsunderlagets *Obligatoriska krav*.

Leveransen släpps under **AGPL-3.0-only** (överenskommet med beställaren; ersätter
anbudsunderlagets ursprungliga EUPL/GPLv3-skrivning – se `LICENSE` och
`docs/adr/0010-agpl-3-0-only-pmpc.md`). e-Avrop-svaren (Ja + motivering per krav)
finns i `docs/obligatoriska-krav-svar.md`.

## Sammanfattning

**78 Uppfyllt / 6 Delvis / 17 Dokumenterat åtagande / 1 Ej tillämpligt** (av 99
icke-funktionella + 3 funktionella krav = 102).

| Status | Innebörd |
| --- | --- |
| **Uppfyllt** | Kravet uppfylls i den levererade lösningen och kan verifieras mot en artefakt (kod, konfiguration, dokument) i repot. |
| **Delvis** | Grunden är levererad och verifierbar, men något återstår (t.ex. formell mätning/granskning i beställarens miljö, eller ett steg som utförs vid/inför anbud eller drift). |
| **Dokumenterat åtagande** | Kravet är i huvudsak ett avtals-/attestationskrav eller en åtgärd som utförs vid kontraktstillfället; leverantören åtar sig det skriftligt och underlaget finns i angivet dokument. |
| **Ej tillämpligt** | Kravet träffar inte kommunal verksamhet eller den valda driftmodellen; noteras ändå. |

## Metod och läsanvisning

- **Bevis/var** anger en verklig sökväg i repot, eller `bilaga till anbud` /
  `kontraktsvillkor` när beviset ligger utanför kodbasen.
- Kravnumreringen följer anbudsunderlagets *Obligatoriska krav*. Där ett kravs
  exakta lydelse inte återges ordagrant är **Kort beskrivning** leverantörens
  sammanfattning av kravets innebörd; statusen speglar de artefakter som
  faktiskt levereras. Vid avvikelse mellan denna sammanfattning och
  anbudsunderlagets ordalydelse gäller anbudsunderlaget, och matrisen stäms av
  mot den exakta lydelsen i anbudsutvärderingen.
- Regelefterlevnad (NIS2, GDPR, Data Act m.fl.) redovisas i sin helhet i
  `docs/regelefterlevnad.md`, `docs/dataskydd-gdpr.md` och `SECURITY.md`;
  ansvarslinjen leverantör/beställare framgår där.

---

## Icke-funktionella krav (#1–#99)

| Krav-nr | Kort beskrivning | Status | Bevis/var |
| --- | --- | --- | --- |
| #1 | Hela lösningen levereras som öppen källkod | Uppfyllt | `LICENSE`, hela repot, `README.md` |
| #2 | Öppen/fri licens för programvaran | Uppfyllt | `LICENSE` (AGPL-3.0-only) [not 1] |
| #3 | Svenskspråkigt användargränssnitt | Uppfyllt | `services/viewer/page.py`, `docs/tillganglighet-wcag.md` |
| #4 | Maskinläsbar programvarukatalog (SBOM) | Uppfyllt | `sbom/geodata-mcp.cdx.json`, `docs/beroenden.md` |
| #5 | Byggbar från källkod (byggrecept per tjänst) | Uppfyllt | `services/*/Dockerfile`, `db/Dockerfile`, `docker-compose.yml` |
| #6 | Publikt/åtkomligt kodrepositorium | Delvis | repot / `bilaga till anbud` [not 2] |
| #7 | Spårbar, löpande commit-historik | Delvis | git-historik i repot |
| #8 | Ändringslogg | Uppfyllt | `CHANGELOG.md` |
| #9 | Semantisk versionshantering | Uppfyllt | `CHANGELOG.md`, `SECURITY.md` |
| #10 | Snabbstart och installationsanvisning | Uppfyllt | `README.md`, `deploy/README.md` |
| #11 | Bindande implementations-/utvecklardokumentation | Uppfyllt | `CONTRACTS.md` |
| #12 | Kompilerings-/kvalitetskontroller i CI | Uppfyllt | `.github/workflows/ci.yml` |
| #13 | Automatiserade tester (e2e/connector/validering) | Uppfyllt | `scripts/e2e_test.py`, `scripts/connector_test.py`, `scripts/validate_data.py` |
| #14 | Öppna standarder för datainhämtning (WFS/WMS/WMTS/OGC API/STAC) | Uppfyllt | `services/worker/connectors/`, `CONTRACTS.md` |
| #15 | Dokumenterat öppet API (OpenAPI) | Uppfyllt | `services/viewer/openapi.yaml`, `docs/api.md` |
| #16 | Versionerat och bakåtkompatibelt API (≥12 mån stöd) | Uppfyllt | `services/viewer/openapi.yaml` (§Versionshantering), `docs/api.md` |
| #17 | OAuth2-autentisering (jämte bearer) | Uppfyllt | `services/mcp/oauth.py`, `docs/adr/0006-oauth2-invite-code-legacy-bearer.md` |
| #18 | Inga avgifter per anrop/transaktion | Dokumenterat åtagande | `services/viewer/openapi.yaml` (§Inga avgifter), `docs/api.md` |
| #19 | Inga avgifter per datavolym | Dokumenterat åtagande | `services/viewer/openapi.yaml` (§Inga avgifter), `docs/api.md` |
| #20 | Öppna, standardiserade exportformat (GPKG/GeoJSON/CSV/Parquet) | Uppfyllt | `services/mcp/export_ops.py`, `docs/exitplan.md` §2 |
| #21 | Proveniens/spårbarhet på data (källa, URL, licens) | Uppfyllt | `services/mcp/provenance.py`, `README.md` (Provenance model) |
| #22 | Dataexport via verktyg | Uppfyllt | `services/mcp/export_ops.py`, `services/worker/exporter.py` |
| #23 | Beroendeförteckning med licens och möjlig ersättare | Uppfyllt | `docs/beroenden.md` |
| #24 | Standardkoordinatsystem (SWEREF 99 / EPSG) | Uppfyllt | `README.md`, `CONTRACTS.md`, `db/init/` |
| #25 | Interoperabilitet med GIS-verktyg (QGIS via GPKG/WMS) | Uppfyllt | `README.md`, `docs/exitplan.md` §3 |
| #26 | Öppet agent-/integrationsprotokoll (MCP över HTTP) | Uppfyllt | `services/mcp/server.py`, `docs/adr/0002-mcp-granssnitt-fastmcp.md` |
| #27 | NIS2 – tekniska riskhanteringsåtgärder | Dokumenterat åtagande | `docs/regelefterlevnad.md`, `SECURITY.md` |
| #28 | Cybersäkerhetslagen (SFS 2018:1174) | Dokumenterat åtagande | `docs/regelefterlevnad.md`, `SECURITY.md` |
| #29 | EU Cybersecurity Act (2019/881) | Dokumenterat åtagande | `docs/regelefterlevnad.md`, `SECURITY.md` |
| #30 | OWASP Top 10 – kartläggning mot kontroller | Uppfyllt | `docs/sakerhetsgranskning.md`, `scripts/security_test.py` |
| #31 | Autentisering/åtkomstkontroll på alla gränssnitt | Uppfyllt | `services/mcp/oauth.py`, `services/mcp/sqlguard.py`, `README.md` (Security boundaries) |
| #32 | Skydd mot injektion och XSS | Uppfyllt | `services/mcp/sqlguard.py`, `services/viewer/main.py` |
| #33 | Oberoende säkerhetsgranskning före produktion | Dokumenterat åtagande | `docs/sakerhetsgranskning.md`, `SECURITY.md` |
| #34 | TLS/kryptering i transit | Uppfyllt | `deploy/Caddyfile`, `deploy/helm/geodata-mcp/templates/openshift-route.yaml`, `docs/drifthandbok.md` §8 |
| #35 | Säkerhetsregressionstester | Uppfyllt | `scripts/security_test.py` |
| #36 | CI med statisk säkerhetsanalys (SAST) | Uppfyllt | `.github/workflows/codeql.yml`, `.github/workflows/ci.yml` |
| #37 | Säker hemlighetshantering – inga hemligheter i repo | Uppfyllt | `.env.example`, `.gitignore`, `docs/sakerhetsgranskning.md` (A05) |
| #38 | Svarstid p95 < 500 ms under last | Delvis | `docs/lasttest.md`, `scripts/loadtest.js` [not 3] |
| #39 | Minst 1 000 samtidiga användare | Delvis | `docs/lasttest.md`, `scripts/loadtest.js` [not 3] |
| #40 | Horisontell skalning | Uppfyllt | `deploy/helm/geodata-mcp/`, `deploy/README.md` §8 |
| #41 | Automatiserat, repeterbart lasttest med pass/fail | Uppfyllt | `scripts/loadtest.js`, `docs/lasttest.md` |
| #42 | Hälsokontroller (liveness/readiness) | Uppfyllt | `docker-compose.yml` (`healthcheck`), `deploy/helm/geodata-mcp/`, `/healthz` |
| #43 | Tillgänglighet WCAG 2.1 AA | Delvis | `docs/tillganglighet-wcag.md` |
| #44 | Responsiv layout och bibehållen zoombarhet | Uppfyllt | `services/viewer/page.py`, `docs/tillganglighet-wcag.md` |
| #45 | Svenska etiketter, meddelanden och sidtitlar | Uppfyllt | `services/viewer/page.py` |
| #46 | Robusthet och kontrollerad degradering vid källfel | Uppfyllt | `docs/sla.md` §6, `README.md` (Data scope) |
| #47 | Revisionsspår/loggning av frågor och åtkomst | Uppfyllt | `services/mcp/provenance.py`, `db/init/05_event_triggers.sql`, `docs/observability.md` |
| #48 | Dataintegritet vid ingest (geometrireparation, ingen tyst överskrivning) | Uppfyllt | `scripts/validate_data.py`, `services/worker/dbutil.py`, `README.md` |
| #49 | Automatisk beroende-/sårbarhets- och hemlighetsskanning | Uppfyllt | `.github/dependabot.yml`, `.github/workflows/ci.yml` |
| #50 | Containeriserad leverans (Docker/OCI) | Uppfyllt | `docker-compose.yml`, `services/*/Dockerfile` |
| #51 | Automatiserad driftsättning (Helm/CI-CD) | Uppfyllt | `deploy/helm/geodata-mcp/`, `deploy/README.md` §3 |
| #52 | En-kommandos, reproducerbar uppstart | Uppfyllt | `docker-compose.yml`, `README.md` (Quickstart) |
| #53 | Arkitekturdokumentation | Uppfyllt | `geodata-mcp-architecture.md` |
| #54 | Drifthandbok | Uppfyllt | `docs/drifthandbok.md` |
| #55 | Arkitekturbeslut (ADR) | Uppfyllt | `docs/adr/` |
| #56 | Exitplan – övertagande utan ursprunglig leverantör | Uppfyllt | `docs/exitplan.md` |
| #57 | Dataexport i öppet, dokumenterat format | Uppfyllt | `docs/exitplan.md` §2, `services/mcp/export_ops.py` |
| #58 | Strukturerad kunskapsöverföring | Dokumenterat åtagande | `docs/kunskapsoverforing.md` |
| #59 | Hemligheter från extern secret store (ej `.env` i produktion) | Uppfyllt | `deploy/helm/geodata-mcp/templates/secret.yaml`, `docs/drifthandbok.md` §10 |
| #60 | Backup- och återställningsrutiner (PITR) | Dokumenterat åtagande | `docs/sla.md` §4, `docs/drifthandbok.md` |
| #61 | Migrationshantering (idempotenta db-migrationer) | Uppfyllt | `db/migrations/` |
| #62 | Medföljande testsvit som kvalitetsnät vid övertagande | Uppfyllt | `scripts/`, `docs/exitplan.md` §4 |
| #63 | Inbyggt dataskydd (privacy by design) + registrerades rättigheter | Uppfyllt | `docs/dataskydd-gdpr.md` §2, §4 |
| #64 | Data Governance Act (2022/868) | Dokumenterat åtagande | `docs/dataskydd-gdpr.md` §7, `docs/regelefterlevnad.md` |
| #65 | Beställaren äger och kontrollerar data och lösning (Data Act) | Uppfyllt | `docs/exitplan.md`, `docs/regelefterlevnad.md` |
| #66 | DPIA-behovsbedömning (art. 35 GDPR) | Dokumenterat åtagande | `docs/dataskydd-gdpr.md` §4.1 |
| #67 | Beställaren äger sin data | Uppfyllt | `docs/dataskydd-gdpr.md` §5 |
| #68 | Ingen modellträning på beställarens data | Uppfyllt | `docs/dataskydd-gdpr.md` §6 |
| #69 | Export/dataportabilitet | Uppfyllt | `services/mcp/export_ops.py`, `docs/exitplan.md` §2 |
| #70 | Behandling inom EU/EES (dataresidens) | Dokumenterat åtagande | `docs/dataskydd-gdpr.md`, `docs/energi-co2.md` §5 |
| #71 | Energieffektiv drift och optimeringar | Uppfyllt | `docs/energi-co2.md` §4, `docker-compose.yml` |
| #72 | Redovisad energi- och klimatpåverkan | Uppfyllt | `docs/energi-co2.md` |
| #73 | Datacenter inom EU/EES | Dokumenterat åtagande | `docs/energi-co2.md` §5 |
| #74 | Resurseffektiv, rätt-dimensionerad drift | Uppfyllt | `docs/kapacitet.md`, `docs/energi-co2.md` §4 |
| #75 | Händelsestyrd tung beräkning (GPU endast vid behov) | Uppfyllt | `services/segmenter/`, `docs/energi-co2.md` §3 |
| #76 | Lokala modeller utan externa AI-API:er | Uppfyllt | `docs/dataskydd-gdpr.md` §6, `README.md` (Model stack) |
| #77 | Minimerad extern nätverkstrafik (vendrade frontend-bundlar) | Uppfyllt | `README.md` (Two renderers), `docs/beroenden.md` §3 |
| #78 | Container-images enligt OCI-standard | Uppfyllt | `services/*/Dockerfile`, `docker-compose.yml` |
| #79 | Paketering för Kubernetes/OpenShift (Helm) | Uppfyllt | `deploy/helm/geodata-mcp/`, `deploy/README.md` |
| #80 | Kapacitets- och dimensioneringsunderlag | Uppfyllt | `docs/kapacitet.md`, `deploy/helm/geodata-mcp/values.yaml` |
| #81 | Central loggning/övervakning via standardprotokoll (Prometheus/OTLP) | Uppfyllt | `docs/observability.md`, `services/viewer/obs.py` |
| #82 | Strukturerad JSON-loggning | Uppfyllt | `services/viewer/obs.py`, `docs/observability.md` §1 |
| #83 | Mätvärden (metrics) och spårning (tracing) | Uppfyllt | `docs/observability.md`, `services/viewer/main.py`, `services/viewer/obs.py` |
| #84 | Drift på kommunens containerplattform (Red Hat OpenShift) | Uppfyllt | `deploy/helm/geodata-mcp/templates/openshift-route.yaml`, `deploy/README.md` §5 |
| #85 | Konfigurerbar via miljövariabler (12-factor) | Uppfyllt | `.env.example`, `CONTRACTS.md`, `deploy/helm/geodata-mcp/values.yaml` |
| #86 | Beständig lagring via volymer (PV/PVC) | Uppfyllt | `deploy/helm/geodata-mcp/templates/postgres.yaml`, `docker-compose.yml` |
| #87 | Resursförfrågningar/-gränser för kapacitetsplanering | Uppfyllt | `deploy/helm/geodata-mcp/values.yaml`, `deploy/README.md` §9 |
| #88 | Least-privilege säkerhetskontext i kluster (`restricted-v2` SCC) | Uppfyllt | `deploy/helm/geodata-mcp/`, `deploy/README.md` §5 |
| #89 | Zero-downtime-utrullning (RollingUpdate) | Uppfyllt | `deploy/helm/geodata-mcp/templates/mcp.yaml`, `deploy/README.md` §8 |
| #90 | Körning på kommunens egen infrastruktur (egen regi/on-prem) | Uppfyllt | `docs/regelefterlevnad.md` (driftmodell), `deploy/helm/geodata-mcp/` |
| #91 | Driftmodell i egen regi (ej leverantörshostad SaaS) | Uppfyllt | `docs/regelefterlevnad.md` (driftmodell) [not 4] |
| #92 | Leverantörsoberoende och utbytbara komponenter | Uppfyllt | `docs/exitplan.md` §3 |
| #93 | Förvaltnings-/underhållsåtagande (säkerhetspatchar, uppdateringar) | Dokumenterat åtagande | `SECURITY.md`, `docs/sla.md` §3, `docs/drifthandbok.md` |
| #94 | DORA (2022/2554) | Ej tillämpligt | `docs/regelefterlevnad.md` (#94) |
| #95 | ISO/IEC 27001 | Dokumenterat åtagande | `docs/regelefterlevnad.md` (#95) |
| #96 | Incidenthantering och sårbarhetsrapportering | Dokumenterat åtagande | `SECURITY.md`, `docs/sla.md` §2 |
| #97 | Inga inlåsningar – öppna standarder rakt igenom | Uppfyllt | `docs/exitplan.md` §3 |
| #98 | Rapportering och uppföljning (månadsrapport, mätvärden) | Dokumenterat åtagande | `docs/sla.md` §5 |
| #99 | Servicenivåavtal (SLA) | Dokumenterat åtagande | `docs/sla.md` |

---

## Funktionella krav (#1–#3)

De tre funktionella kraven motsvarar de scoutade användarfallen i uppdraget:
olovligt byggande via ortofoto-förändringsanalys, avvikelse från beviljat lov,
samt byggnation inom strandskyddszon. Förändringsdetektering är utformad som ett
**screeningverktyg som tar fram kandidater för mänsklig granskning, inte
påståenden** (se `geodata-mcp-architecture.md` §7 och
`docs/adr/0007-sam3-ortofoto-forandringsdetektering.md`).

| Krav-nr | Kort beskrivning | Status | Bevis/var |
| --- | --- | --- | --- |
| F#1 | Olovligt byggande – upptäckt via ortofoto-förändringsanalys mellan två flygbildsårgångar | Uppfyllt | `services/worker/connectors/change_detect.py`, `services/segmenter/app.py`, `services/mcp/analysis_ops.py`, `scripts/change_detect_test.py`, `geodata-mcp-architecture.md` §7 |
| F#2 | Avvikelse från beviljat lov – förändringspolygoner jämförs mot bygglovs-/beslutslager | Delvis | `services/mcp/analysis_ops.py` + `query`-join, `geodata-mcp-architecture.md` (revisionsflöde) [not 5] |
| F#3 | Byggnation inom strandskyddszon – rumslig analys mot strandskyddslager | Uppfyllt | `ref.strandskydd` (ingest), `services/mcp/query_ops.py`/`geometry.py`, `scripts/e2e_test.py`, `README.md` (Data loaded) |

---

## Noter

- **[not 1] Licens (#2):** AGPL-3.0-only är den överenskomna licensen. Den
  ersätter anbudsunderlagets ursprungliga skrivning om EUPL v1.2 / GNU GPL v3,
  vilken enligt Fråga/Svar är "felaktig och ska inte tillämpas". Se `LICENSE`
  och `docs/adr/0010-agpl-3-0-only-pmpc.md`.
- **[not 2] Publikt repo (#6):** Källkoden är komplett och öppen (AGPL-3.0-only) i
  leveransen, men kravet på ett *publikt/åtkomligt* repositorium förutsätter en
  åtgärd som utförs före eller vid anbudsinlämning: repot görs publikt **eller**
  beställaren ges läsåtkomst till repot i samband med anbudet. Statusen blir
  Uppfyllt när den åtkomsten är etablerad.
- **[not 3] Prestanda/lasttest (#38, #39):** Ett automatiserat, repeterbart
  lasttest med tydliga trösklar (p95 < 500 ms, ≥ 1 000 samtidiga användare) och
  pass/fail levereras (`scripts/loadtest.js`, `docs/lasttest.md`). Den formella
  mätningen ska köras i en produktionslik miljö hos beställaren – ett lasttest mot
  en utvecklingsmaskin mäter maskinen, inte lösningen – varför status är Delvis
  tills ett protokollfört resultat föreligger.
- **[not 4] Driftmodell (#91):** Lösningen driftas i egen regi (on-prem,
  containeriserad) och inte som leverantörshostad SaaS. De SaaS-orienterade kraven
  i spannet #91–#99 hanteras i det ljuset; de materiellt viktiga (#94 DORA, #95
  ISO/IEC 27001) behandlas separat i `docs/regelefterlevnad.md`.
- **[not 5] Avvikelse från beviljat lov (F#2):** Analysflödet finns – förändrings-
  polygoner från ortofoto-analysen kan joinas mot ett bygglovs-/beslutslager med
  `query`. Sundsvalls bygglovsregister (ByggR) är dock i huvudsak åtkomst-
  begränsat; endast ett WFS-lager för förhandsbesked
  (`SundsvallsKommun:ByggR_Huvudbeslut_Forhandsbesked`) är öppet. Full täckning
  förutsätter att beställaren ger åtkomst till beslutslagret. Därav status Delvis.
</content>
