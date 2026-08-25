# Svar på Obligatoriska krav (e-Avrop)

Anbudssvar för **Sundsvalls kommun UH-2026-159, "Govtech4all Pilot 3 – AI och
geodata"**. Varje obligatoriskt ska-krav besvaras nedan med **Ja** samt en
kortfattad motivering och en referens till den levererade artefakten. Den
fullständiga kravuppfyllnadsmatrisen med status per krav finns i
[`../COMPLIANCE.md`](../COMPLIANCE.md).

Lösningen levereras som öppen källkod under **AGPL-3.0-only** (överenskommet med
beställaren; ersätter anbudsunderlagets ursprungliga EUPL/GPLv3-skrivning – se
`LICENSE` och `docs/adr/0010-agpl-3-0-only-pmpc.md`).

> **Läsanvisning:** "Ja" bekräftar att kravet uppfylls eller att leverantören
> åtar sig det enligt referensen. Där uppfyllnaden förutsätter en åtgärd
> vid/inför anbud eller drift (t.ex. publikt repo, formellt lasttest i
> beställarens miljö, granskning eller SLA-nivåer) framgår det uttryckligen av
> motiveringen. Vid avvikelse mellan sammanfattningen nedan och anbudsunderlagets
> ordalydelse gäller anbudsunderlaget.

## Icke-funktionella krav (#1–#99)

| Krav-nr | Svar | Motivering | Referens |
| --- | --- | --- | --- |
| #1 | Ja | Hela lösningen levereras som öppen källkod. | `LICENSE`, hela repot |
| #2 | Ja | Öppen licens AGPL-3.0-only (ersätter EUPL/GPLv3 enligt överenskommelse). | `LICENSE`, `docs/adr/0010-agpl-3-0-only-pmpc.md` |
| #3 | Ja | Användargränssnittet är på svenska (`lang="sv"`, svenska etiketter). | `services/viewer/page.py` |
| #4 | Ja | Maskinläsbar SBOM i CycloneDX-format medföljer. | `sbom/geodata-mcp.cdx.json` |
| #5 | Ja | Byggbar från källa med byggrecept per tjänst. | `services/*/Dockerfile`, `docker-compose.yml` |
| #6 | Ja | Repot görs publikt eller beställaren ges läsåtkomst i samband med anbudet. | repot / bilaga till anbud |
| #7 | Ja | Utvecklingen sker med spårbar, löpande commit-historik. | git-historik i repot |
| #8 | Ja | Ändringar dokumenteras i en ändringslogg (Keep a Changelog). | `CHANGELOG.md` |
| #9 | Ja | Projektet följer semantisk versionshantering. | `CHANGELOG.md`, `SECURITY.md` |
| #10 | Ja | Snabbstart och installationsanvisning finns. | `README.md`, `deploy/README.md` |
| #11 | Ja | Bindande implementationskontrakt dokumenterar systemet. | `CONTRACTS.md` |
| #12 | Ja | CI kör kompilerings- och kvalitetskontroller vid varje ändring. | `.github/workflows/ci.yml` |
| #13 | Ja | Automatiserade e2e-, connector- och valideringstester medföljer. | `scripts/e2e_test.py`, `scripts/connector_test.py`, `scripts/validate_data.py` |
| #14 | Ja | Data hämtas via öppna standarder (WFS/WMS/WMTS/OGC API/STAC). | `services/worker/connectors/` |
| #15 | Ja | Det öppna API:et är specificerat i OpenAPI 3.0.3. | `services/viewer/openapi.yaml`, `docs/api.md` |
| #16 | Ja | API:et är versionerat och bakåtkompatibelt, stöds ≥ 12 månader. | `services/viewer/openapi.yaml` |
| #17 | Ja | OAuth 2.1 + PKCE stöds jämte bearer-nycklar. | `services/mcp/oauth.py` |
| #18 | Ja | Inga avgifter per anrop eller transaktion tas ut. | `services/viewer/openapi.yaml`, `docs/api.md` |
| #19 | Ja | Inga avgifter per datavolym tas ut. | `services/viewer/openapi.yaml`, `docs/api.md` |
| #20 | Ja | Export sker i öppna format (GPKG/GeoJSON/CSV/Parquet). | `services/mcp/export_ops.py`, `docs/exitplan.md` |
| #21 | Ja | Varje lager bär proveniens (källa, URL, licens). | `services/mcp/provenance.py`, `README.md` |
| #22 | Ja | Dataexport tillhandahålls via `export`-verktyget. | `services/mcp/export_ops.py`, `services/worker/exporter.py` |
| #23 | Ja | Beroenden redovisas öppet med licens och möjlig ersättare. | `docs/beroenden.md` |
| #24 | Ja | Standardkoordinatsystem SWEREF 99 / EPSG används genomgående. | `README.md`, `CONTRACTS.md`, `db/init/` |
| #25 | Ja | Interoperabelt med GIS-verktyg (QGIS via GPKG/WMS). | `README.md`, `docs/exitplan.md` |
| #26 | Ja | Öppet agent-/integrationsprotokoll (MCP över HTTP). | `services/mcp/server.py`, `docs/adr/0002-mcp-granssnitt-fastmcp.md` |
| #27 | Ja | Lösningen levererar NIS2:s tekniska riskhanteringsåtgärder; leverantören stödjer beställarens efterlevnad. | `docs/regelefterlevnad.md`, `SECURITY.md` |
| #28 | Ja | Samma kontroller stödjer Cybersäkerhetslagens skyldigheter. | `docs/regelefterlevnad.md`, `SECURITY.md` |
| #29 | Ja | Lösningen är förberedd för EU:s cybersäkerhetscertifiering. | `docs/regelefterlevnad.md`, `SECURITY.md` |
| #30 | Ja | OWASP Top 10 är kartlagt mot konkreta, testade kontroller. | `docs/sakerhetsgranskning.md`, `scripts/security_test.py` |
| #31 | Ja | Autentisering och åtkomstkontroll gäller alla gränssnitt. | `services/mcp/oauth.py`, `services/mcp/sqlguard.py` |
| #32 | Ja | Skydd mot SQL-injektion och XSS är implementerat och testat. | `services/mcp/sqlguard.py`, `services/viewer/main.py` |
| #33 | Ja | Leverantören åtar sig oberoende säkerhetsgranskning före produktion. | `docs/sakerhetsgranskning.md`, `SECURITY.md` |
| #34 | Ja | TLS termineras vid kanten (Caddy/OpenShift Route/Ingress). | `deploy/Caddyfile`, `deploy/helm/geodata-mcp/templates/openshift-route.yaml` |
| #35 | Ja | Säkerhetsregressionstester ingår i leveransen. | `scripts/security_test.py` |
| #36 | Ja | CI kör statisk säkerhetsanalys (CodeQL) vid varje PR. | `.github/workflows/codeql.yml` |
| #37 | Ja | Inga hemligheter i repot; allt via `.env`/secret store. | `.env.example`, `.gitignore` |
| #38 | Ja | Lasttest med tröskeln p95 < 500 ms levereras; formell mätning körs i beställarens produktionslika miljö. | `docs/lasttest.md`, `scripts/loadtest.js` |
| #39 | Ja | Lasttest rampar till ≥ 1 000 samtidiga användare; mätning körs i beställarens miljö. | `docs/lasttest.md`, `scripts/loadtest.js` |
| #40 | Ja | Statslösa tjänster skalar horisontellt bakom ingress. | `deploy/helm/geodata-mcp/`, `deploy/README.md` §8 |
| #41 | Ja | Lasttestet är automatiserat och repeterbart med tydligt pass/fail. | `scripts/loadtest.js`, `docs/lasttest.md` |
| #42 | Ja | Varje tjänst har hälsokontroller (liveness/readiness). | `docker-compose.yml`, `deploy/helm/geodata-mcp/` |
| #43 | Ja | Webbgränssnittet är delvis förenligt med WCAG 2.1 AA; plan för full efterlevnad finns. | `docs/tillganglighet-wcag.md` |
| #44 | Ja | Layouten är responsiv och blockerar inte zoomning. | `services/viewer/page.py`, `docs/tillganglighet-wcag.md` |
| #45 | Ja | Etiketter, meddelanden och sidtitlar är på svenska. | `services/viewer/page.py` |
| #46 | Ja | Systemet degraderar kontrollerat när en källa är otillgänglig. | `docs/sla.md` §6, `README.md` |
| #47 | Ja | Frågor och åtkomst loggas i ett revisionsspår (query_log, pgAudit, event triggers). | `services/mcp/provenance.py`, `db/init/05_event_triggers.sql` |
| #48 | Ja | Dataintegritet vid ingest: geometrireparation och ingen tyst överskrivning. | `scripts/validate_data.py`, `services/worker/dbutil.py` |
| #49 | Ja | Automatisk beroende-, sårbarhets- och hemlighetsskanning i CI. | `.github/dependabot.yml`, `.github/workflows/ci.yml` |
| #50 | Ja | Lösningen levereras containeriserad (Docker/OCI). | `docker-compose.yml`, `services/*/Dockerfile` |
| #51 | Ja | Automatiserad driftsättning via Helm och CI/CD. | `deploy/helm/geodata-mcp/`, `deploy/README.md` |
| #52 | Ja | Hela systemet startas med ett kommando i en reproducerbar miljö. | `docker-compose.yml`, `README.md` |
| #53 | Ja | Systemarkitekturen är dokumenterad. | `geodata-mcp-architecture.md` |
| #54 | Ja | En drifthandbok medföljer. | `docs/drifthandbok.md` |
| #55 | Ja | Arkitekturbeslut dokumenteras som ADR:er. | `docs/adr/` |
| #56 | Ja | Exitplan för övertagande utan ursprunglig leverantör finns. | `docs/exitplan.md` |
| #57 | Ja | Data kan exporteras i öppet, dokumenterat format. | `docs/exitplan.md` §2, `services/mcp/export_ops.py` |
| #58 | Ja | Leverantören åtar sig strukturerad kunskapsöverföring. | `docs/kunskapsoverforing.md` |
| #59 | Ja | Hemligheter hämtas från beställarens secret store i produktion. | `deploy/helm/geodata-mcp/templates/secret.yaml`, `docs/drifthandbok.md` |
| #60 | Ja | Leverantören åtar sig backup-/PITR- och återställningsrutiner. | `docs/sla.md` §4, `docs/drifthandbok.md` |
| #61 | Ja | Databasändringar sker via idempotenta migrationer. | `db/migrations/` |
| #62 | Ja | En medföljande testsvit utgör kvalitetsnät vid övertagande. | `scripts/`, `docs/exitplan.md` §4 |
| #63 | Ja | Inbyggt dataskydd (privacy by design) och stöd för registrerades rättigheter. | `docs/dataskydd-gdpr.md` |
| #64 | Ja | Lösningen är förenlig med Data Governance Act. | `docs/dataskydd-gdpr.md` §7, `docs/regelefterlevnad.md` |
| #65 | Ja | Beställaren äger och kontrollerar data och lösning (Data Act, anti-inlåsning). | `docs/exitplan.md` |
| #66 | Ja | Underlag för DPIA-behovsbedömning tillhandahålls. | `docs/dataskydd-gdpr.md` §4.1 |
| #67 | Ja | All beständig data ligger hos beställaren. | `docs/dataskydd-gdpr.md` §5 |
| #68 | Ja | Ingen modellträning sker på beställarens data (endast lokal inferens). | `docs/dataskydd-gdpr.md` §6 |
| #69 | Ja | Dataportabilitet via export i öppna format. | `services/mcp/export_ops.py`, `docs/exitplan.md` |
| #70 | Ja | Behandling förläggs inom EU/EES (dataresidens). | `docs/dataskydd-gdpr.md`, `docs/energi-co2.md` §5 |
| #71 | Ja | Energieffektiv drift med inbyggda optimeringar. | `docs/energi-co2.md` §4 |
| #72 | Ja | Energi- och klimatpåverkan redovisas med transparent metod. | `docs/energi-co2.md` |
| #73 | Ja | Driften förläggs till datacenter inom EU/EES. | `docs/energi-co2.md` §5 |
| #74 | Ja | Rätt-dimensionerad, resurseffektiv drift enligt kapacitetsunderlag. | `docs/kapacitet.md`, `docs/energi-co2.md` |
| #75 | Ja | Tung beräkning (GPU) är händelsestyrd och valfri. | `services/segmenter/`, `docs/energi-co2.md` §3 |
| #76 | Ja | Modeller körs lokalt utan externa AI-API:er. | `docs/dataskydd-gdpr.md` §6, `README.md` |
| #77 | Ja | Frontend-bundlar är vendrade; inga CDN-anrop i drift. | `README.md`, `docs/beroenden.md` §3 |
| #78 | Ja | Container-images följer OCI-standard. | `services/*/Dockerfile`, `docker-compose.yml` |
| #79 | Ja | Paketering för Kubernetes/OpenShift via Helm. | `deploy/helm/geodata-mcp/`, `deploy/README.md` |
| #80 | Ja | Kapacitets- och dimensioneringsunderlag finns. | `docs/kapacitet.md`, `deploy/helm/geodata-mcp/values.yaml` |
| #81 | Ja | Central loggning/övervakning via öppna standarder (Prometheus/OTLP). | `docs/observability.md`, `services/viewer/obs.py` |
| #82 | Ja | Strukturerad JSON-loggning till stdout. | `services/viewer/obs.py`, `docs/observability.md` |
| #83 | Ja | Mätvärden (metrics) och spårning (tracing) exponeras. | `docs/observability.md`, `services/viewer/main.py` |
| #84 | Ja | Drift på Red Hat OpenShift via native Route. | `deploy/helm/geodata-mcp/templates/openshift-route.yaml`, `deploy/README.md` §5 |
| #85 | Ja | Konfiguration via miljövariabler (12-factor). | `.env.example`, `deploy/helm/geodata-mcp/values.yaml` |
| #86 | Ja | Beständig lagring via volymer (PV/PVC). | `deploy/helm/geodata-mcp/templates/postgres.yaml`, `docker-compose.yml` |
| #87 | Ja | Resursförfrågningar/-gränser är satta för kapacitetsplanering. | `deploy/helm/geodata-mcp/values.yaml`, `deploy/README.md` §9 |
| #88 | Ja | Least-privilege säkerhetskontext (`restricted-v2` SCC) stöds. | `deploy/helm/geodata-mcp/`, `deploy/README.md` §5 |
| #89 | Ja | Zero-downtime-utrullning med RollingUpdate (`maxUnavailable: 0`). | `deploy/helm/geodata-mcp/templates/mcp.yaml`, `deploy/README.md` §8 |
| #90 | Ja | Körs på beställarens egen infrastruktur (egen regi/on-prem). | `docs/regelefterlevnad.md`, `deploy/helm/geodata-mcp/` |
| #91 | Ja | Driftmodell i egen regi, inte leverantörshostad SaaS. | `docs/regelefterlevnad.md` |
| #92 | Ja | Leverantörsoberoende med utbytbara komponenter. | `docs/exitplan.md` §3 |
| #93 | Ja | Leverantören åtar sig förvaltning och säkerhetsunderhåll. | `SECURITY.md`, `docs/sla.md` §3 |
| #94 | Ja | DORA är ej tillämpligt på kommunal verksamhet; resiliensprinciperna stöds ändå. | `docs/regelefterlevnad.md` (#94) |
| #95 | Ja | Kontrollerna är kartlagda mot ISO/IEC 27001 Annex A och kan drivas i beställarens ISMS. | `docs/regelefterlevnad.md` (#95) |
| #96 | Ja | Incidenthantering och samordnat sårbarhetsröjande enligt policy. | `SECURITY.md`, `docs/sla.md` §2 |
| #97 | Ja | Inga inlåsningar – öppna standarder rakt igenom. | `docs/exitplan.md` §3 |
| #98 | Ja | Leverantören åtar sig rapportering och uppföljning (månadsrapport). | `docs/sla.md` §5 |
| #99 | Ja | Servicenivåavtal (SLA) med tillgänglighet, incident- och underhållsåtaganden. | `docs/sla.md` |

## Funktionella krav (#1–#3)

Förändringsdetektering levererar **kandidater för mänsklig granskning, inte
påståenden** (se `geodata-mcp-architecture.md` §7 och
`docs/adr/0007-sam3-ortofoto-forandringsdetektering.md`).

| Krav-nr | Svar | Motivering | Referens |
| --- | --- | --- | --- |
| F#1 | Ja | Olovligt byggande upptäcks via ortofoto-förändringsanalys mellan två flygbildsårgångar (SAM 3). | `services/worker/connectors/change_detect.py`, `services/segmenter/app.py`, `services/mcp/analysis_ops.py`, `scripts/change_detect_test.py` |
| F#2 | Ja | Avvikelse från beviljat lov: förändringspolygoner joinas mot bygglovs-/beslutslager; full täckning förutsätter att beställaren ger åtkomst till ByggR-beslutslagret. | `services/mcp/analysis_ops.py`, `geodata-mcp-architecture.md` (revisionsflöde) |
| F#3 | Ja | Byggnation inom strandskyddszon analyseras rumsligt mot strandskyddslagret. | `ref.strandskydd`, `services/mcp/query_ops.py`, `scripts/e2e_test.py`, `README.md` |

---

Fullständig status per krav (Uppfyllt/Delvis/Dokumenterat åtagande/Ej tillämpligt)
och underbyggande resonemang finns i [`../COMPLIANCE.md`](../COMPLIANCE.md),
`docs/regelefterlevnad.md` och `docs/dataskydd-gdpr.md`.
</content>
