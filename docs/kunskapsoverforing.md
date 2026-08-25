# Plan för strukturerad kunskapsöverföring (ska-krav #58)

Detta dokument beskriver hur leverantören strukturerat överför kunskap till
Sundsvalls kommun (eller den part kommunen utser) så att lösningen kan driftas,
felsökas och vidareutvecklas utan den ursprungliga leverantören. Planen hänger
ihop med exitplanen (`docs/exitplan.md`) och SLA:t (`docs/sla.md`): exitplanen
levererar *material och data*, denna plan levererar *kompetens*.

## 1. Mål

Efter genomförd kunskapsöverföring ska den mottagande förvaltaren självständigt
kunna:

1. Bygga, konfigurera och starta hela systemet (`docker compose up`).
2. Förstå arkitekturen och *varför* de centrala designbesluten togs.
3. Drifta, övervaka och felsöka tjänsterna i löpande drift.
4. Hantera säkerhet, autentisering, nycklar och incidenter.
5. Utföra dataoperationer: ingest, export, backup och återställning.
6. Vidareutveckla och släppa nya versioner med bibehållet kvalitetsnät (tester).

Kunskapsöverföringen är genomförd när förvaltaren har utfört momenten i avsnitt 4
**själv**, med leverantören som stöd – inte bara sett dem demonstreras.

## 2. Målgrupper

| Roll | Behov | Fokus |
| --- | --- | --- |
| **Driftpersonal / DevOps** | Starta, övervaka, säkerhetskopiera, felsöka | Avsnitt 3.2, 3.3, 3.5 |
| **Utvecklare / förvaltare** | Läsa och ändra koden, släppa versioner | Avsnitt 3.1, 3.6 |
| **Säkerhetsansvarig** | Auth, härdning, incidenter, regelefterlevnad | Avsnitt 3.4 |
| **GIS-/verksamhetsansvarig** | Datamodell, källor, kartvyer, agentanvändning | Avsnitt 3.1, 3.5 |

## 3. Innehåll (moduler)

Varje modul består av en genomgång + praktisk övning + hänvisning till
dokumentation som redan finns i repot, så att kunskapen är återfinnbar efteråt.

### 3.1 Arkitektur och kodbas

- Systemöversikt: sex containrar + valfri segmenter (`docker-compose.yml`,
  `geodata-mcp-architecture.md`).
- De centrala designbesluten via **arkitekturbesluten** i `docs/adr/` (PostGIS
  som enda tillståndslager, MCP-gränssnitt, durable workspaces, två renderare,
  OAuth, SAM 3, MinIO, Caddy, AGPL).
- Tjänsterna: `mcp` (kontrollplan/verktyg), `worker` (connectors + embeddings),
  `viewer` (kartor + endpoints), `segmenter` (SAM 3).
- Det bindande implementationskontraktet: `CONTRACTS.md`.

### 3.2 Installation och konfiguration

- Från `.env.example` till körande system; varje miljövariabels betydelse.
- Byggrecept per tjänst (`Dockerfile`), Caddy-proxyn (`deploy/Caddyfile`).
- Databasinitiering och roller (`db/init`), migrationer (`db/migrations`).

### 3.3 Drift och observerbarhet

- Hälsokontroller och omstartspolicy.
- Loggar (strukturerad JSON till stdout), metrics (Prometheus `/metrics`),
  spårning (OpenTelemetry/OTLP) – `docs/observability.md`.
- Backup/återställning och `scripts/validate_data.py` (SLA:t, `docs/sla.md`).

### 3.4 Säkerhet

- Autentisering: bearer-nycklar och OAuth 2.1 + PKCE (`services/mcp/oauth.py`).
- SQL-validering och behörighetsgränser (`services/mcp/sqlguard.py`, README
  "Security boundaries").
- CSP/XSS-skydd i `viewer`.
- Nyckelrotation, hemlighetshantering, incidentrutin (`SECURITY.md`).
- OWASP-kartläggning och granskningsplan (`docs/sakerhetsgranskning.md`).

### 3.5 Data och användning

- Datakällor och proveniens (`data_sources.xlsx`,
  `services/mcp/provenance.py`, `app.query_log`).
- Ingest via connectors (WFS/WMTS/OGC-API/STAC/PDF/text/files).
- De åtta MCP-verktygen och kartvyer (README "The eight tools", `scripts/mcp_client.py`).
- Export i öppna format och dataägarskap (`docs/exitplan.md`,
  `docs/dataskydd-gdpr.md`).

### 3.6 Vidareutveckling och kvalitetsnät

- Utvecklingsflöde, git-historik och ADR-processen (`docs/adr/0000-mall.md`).
- Testsviterna: `e2e_test.py`, `security_test.py`, `connector_test.py`,
  `change_detect_test.py`, `validate_data.py`, `scripts/loadtest.js` – hur de
  körs och tolkas (`docs/lasttest.md`).
- SBOM och beroendehantering (`scripts/gen_sbom.sh`, `sbom/`).

## 4. Genomförande och format

Kunskapsöverföringen sker **hands-on** och integreras med överlämnandefaserna i
`docs/exitplan.md` (typiskt vecka 2–4):

| Aktivitet | Format | Resultat |
| --- | --- | --- |
| **Genomgångar** per modul (3.1–3.6) | Workshop med skärmdelning; spelas in | Inspelningar + anteckningar |
| **Installationsövning** | Förvaltaren startar systemet från noll i egen miljö | Fungerande egen instans |
| **Driftövning** | Förvaltaren gör backup, återställning, felsökning, nyckelrotation | Verifierad driftförmåga |
| **Skuggdrift** | Förvaltaren driftar med leverantören som bollplank | Självständig drift |
| **"Break/fix"-övning** | Leverantören inför ett fel i testmiljö; förvaltaren felsöker | Bevisad felsökningsförmåga |
| **Frågor & utestående** | Löpande logg över öppna frågor | Alla frågor besvarade |

All dokumentation som modulerna hänvisar till finns **i repot** (README,
CONTRACTS, arkitektur, `docs/`, ADR) och följer med i leveransen, så materialet
är sökbart och versionshanterat även efter att sessionerna är avslutade. Vid
behov kompletteras med korta, uppgiftsorienterade driftrunbooks.

## 5. Avslut och kvittering

Kunskapsöverföringen anses genomförd när:

- Samtliga moduler (3.1–3.6) är gjorda och praktiskt övade.
- Förvaltaren har startat, driftat och felsökt systemet **självständigt** i egen
  miljö (avsnitt 4).
- Testsviterna körs grönt av förvaltaren själv.
- Utestående frågor är stängda.

Detta kvitteras skriftligt mellan parterna och utgör en förutsättning för det
formella övertagandet (fas 5 i `docs/exitplan.md`). En kort
uppföljningsavstämning bokas efter en inledande självständig driftperiod för att
fånga eventuella återstående behov.
