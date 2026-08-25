# Security Policy / Säkerhetspolicy

This document is bilingual. The English version comes first, the Swedish version
(**svensk version längre ned**) follows and is equivalent.

Detta dokument är tvåspråkigt. Den engelska versionen kommer först, den svenska
motsvarande versionen följer nedan.

---

# Security Policy (English)

Geodata MCP v2 is delivered to Sundsvalls kommun as part of the public
procurement **UH-2026-159 "Govtech4all Pilot 3 – AI och geodata"** and is
licensed under **AGPL-3.0-only**. Security is treated as a first-class property:
the codebase ships with security regression tests (`scripts/security_test.py`),
an OWASP Top 10 mapping (`docs/sakerhetsgranskning.md`), and a Software Bill of
Materials (`sbom/geodata-mcp.cdx.json`).

## Supported versions

The project follows Semantic Versioning (see `CHANGELOG.md`). While the platform
is pre-1.0, security fixes are provided for the **latest released minor version**
only. Operators should track the latest release.

| Version | Supported |
| --- | --- |
| Latest released (currently `0.1.x`) | Yes – security fixes provided |
| Older pre-1.0 versions | No – upgrade to the latest release |

Because the whole system is open source and self-hosted, operators are also able
to apply fixes themselves and rebuild the affected image.

## Reporting a vulnerability (coordinated disclosure)

**Please do not open a public issue for security vulnerabilities.**

- Report privately to the maintainer/supplier via the security contact agreed in
  the contract, or to the repository maintainer by private channel.
- Include: affected component/endpoint, version or commit, a description, and
  reproduction steps or a proof of concept.
- We aim to **acknowledge within 3 business days** and to agree a remediation
  timeline based on severity (see `docs/sla.md` – security incidents are handled
  as priority P1).
- Please allow a reasonable period for a fix before any public disclosure
  (coordinated disclosure). We will credit reporters who wish to be named.

For deployments operated on behalf of the municipality, a confirmed security
incident is handled as **P1** per the SLA, including any statutory notification
duties (see "Regulatory posture" below and `docs/dataskydd-gdpr.md` for personal
data breaches – 72-hour notification to IMY).

## Security controls (summary)

The concrete controls and their code locations are enumerated in
`docs/sakerhetsgranskning.md` (OWASP Top 10 2021 mapping) and README "Security
boundaries". In brief:

- **Authentication** on `/mcp` (bearer keys + OAuth 2.1/PKCE); keys stored only
  as SHA-256 digests.
- **SQL safety**: single-statement, read-only validation and identifier quoting
  (`services/mcp/sqlguard.py`); agent SQL runs as a read-only role.
- **Access control**: workspace-scoped writes, capability-checked serving
  endpoints, per-principal CSRF tokens in the manager UI.
- **Web hardening**: Content-Security-Policy without `'unsafe-inline'`,
  `nosniff`, `Referrer-Policy` (`services/viewer/main.py`).
- **Transport**: TLS terminated at the reverse proxy; database and object store
  bound to `127.0.0.1`.
- **Auditing/observability**: pgAudit, provenance log, structured logs, metrics,
  tracing (`docs/observability.md`).

### Known, documented boundaries

Some properties are deliberate design choices, not defects, and must be discussed
before a production deployment (README "Security boundaries"): workspaces are
**namespacing, not tenancy** (authenticated principals can *read* any workspace;
writes are isolated); **API keys are static** with manual rotation; **map view
URLs are capability links**. Documented upgrade paths (RLS / role-per-key, an IdP
at the Caddy chokepoint) exist for each.

## Independent review before production

An **independent security review** (penetration test + code review) is planned
before production go-live, per `docs/sakerhetsgranskning.md` (ska-krav #33).
Critical/High findings must be remediated before go-live.

## Regulatory posture

The platform is built to support the operator's compliance with the following
frameworks. The **operator (data controller / essential-entity duties) remains
responsible** for the regulatory obligations; the platform provides the technical
means.

- **NIS2 (Directive (EU) 2022/2555) – ska-krav #27.** The solution provides risk
  management building blocks that map to NIS2 measures: access control and
  authentication, encryption of secrets and TLS, logging/monitoring and
  auditability, backup and recovery (`docs/sla.md`), supply-chain transparency
  via SBOM, and incident handling. Whether a given deployment is *in scope* of
  NIS2 is determined by the operating entity; the platform supports the required
  controls.
- **Swedish Cybersecurity Act / Cybersäkerhetslagen – ska-krav #28.** Sweden's
  implementation of NIS2. The same technical controls and the incident-handling
  process (this document + `docs/sla.md`) support the operator's obligations,
  including incident reporting to the competent authority where applicable.
- **EU Cybersecurity Act (Regulation (EU) 2019/881) – ska-krav #29.** Establishes
  ENISA and the EU cybersecurity certification framework. The solution favours
  standardised, certifiable building blocks (open protocols, documented crypto,
  SBOM) so a deployment can pursue relevant certification schemes; no proprietary
  security component blocks this.
- **OWASP Top 10 (2021) – ska-krav #30.** Every category is mapped to concrete
  controls with code references and regression tests in
  `docs/sakerhetsgranskning.md`.

---

# Säkerhetspolicy (svenska)

Geodata MCP v2 levereras till Sundsvalls kommun inom den offentliga
upphandlingen **UH-2026-159 "Govtech4all Pilot 3 – AI och geodata"** och
licensieras under **AGPL-3.0-only**. Säkerhet behandlas som en förstklassig
egenskap: kodbasen levereras med säkerhetsregressionstester
(`scripts/security_test.py`), en OWASP Top 10-kartläggning
(`docs/sakerhetsgranskning.md`) och en programvarukatalog/SBOM
(`sbom/geodata-mcp.cdx.json`).

## Versioner som stöds

Projektet följer Semantisk versionshantering (se `CHANGELOG.md`). Så länge
plattformen är pre-1.0 tillhandahålls säkerhetsrättningar **endast för den
senaste släppta minor-versionen**. Driftansvariga bör följa senaste release.

| Version | Stöds |
| --- | --- |
| Senaste release (för närvarande `0.1.x`) | Ja – säkerhetsrättningar ges |
| Äldre pre-1.0-versioner | Nej – uppgradera till senaste release |

Eftersom hela systemet är öppen källkod och självhostat kan driftansvariga
dessutom applicera rättningar själva och bygga om berörd image.

## Rapportera en sårbarhet (samordnat röjande)

**Öppna inte ett publikt ärende för säkerhetssårbarheter.**

- Rapportera privat till förvaltaren/leverantören via den säkerhetskontakt som
  avtalats, eller till repots förvaltare via privat kanal.
- Ange: berörd komponent/endpoint, version eller commit, en beskrivning samt
  reproduktionssteg eller ett proof of concept.
- Vi strävar efter att **bekräfta inom 3 arbetsdagar** och att komma överens om
  en åtgärdstidplan utifrån allvarlighetsgrad (se `docs/sla.md` –
  säkerhetsincidenter hanteras som prioritet P1).
- Ge rimlig tid för en rättning innan publikt röjande (samordnat röjande). Vi
  omnämner gärna rapportörer som vill namnges.

För driftmiljöer som drivs åt kommunen hanteras en bekräftad säkerhetsincident
som **P1** enligt SLA:t, inklusive eventuella lagstadgade anmälningsskyldigheter
(se "Regulatorisk hållning" nedan; personuppgiftsincidenter enligt
`docs/dataskydd-gdpr.md` – anmälan till IMY inom 72 timmar).

## Säkerhetskontroller (sammanfattning)

De konkreta kontrollerna och deras plats i koden räknas upp i
`docs/sakerhetsgranskning.md` (OWASP Top 10 2021-kartläggning) och README
"Security boundaries". I korthet:

- **Autentisering** på `/mcp` (bearer-nycklar + OAuth 2.1/PKCE); nycklar lagras
  endast som SHA-256-digest.
- **SQL-säkerhet**: validering av en läsande sats i taget och citering av
  identifierare (`services/mcp/sqlguard.py`); agent-SQL körs som read-only-roll.
- **Åtkomstkontroll**: workspace-scopad skrivning, capability-kontrollerade
  endpoints, per-principal CSRF-token i manager-UI:t.
- **Webbhärdning**: Content-Security-Policy utan `'unsafe-inline'`, `nosniff`,
  `Referrer-Policy` (`services/viewer/main.py`).
- **Transport**: TLS termineras i reverse proxy; databas och objektlager bundna
  till `127.0.0.1`.
- **Revision/observerbarhet**: pgAudit, proveniens-logg, strukturerade loggar,
  metrics, spårning (`docs/observability.md`).

### Kända, dokumenterade gränser

Vissa egenskaper är avsiktliga designval, inte brister, och ska tas upp före
produktionssättning (README "Security boundaries"): workspaces är **namnrymd,
inte tenancy** (autentiserade principaler kan *läsa* valfri workspace; skrivning
är isolerad); **API-nycklar är statiska** med manuell rotation; **kartvy-URL:er
är capability-länkar**. Dokumenterade uppgraderingsvägar (RLS/roll-per-nyckel,
en IdP i Caddy-strypningen) finns för var och en.

## Oberoende granskning före produktion

En **oberoende säkerhetsgranskning** (penetrationstest + kodgranskning) är
planerad före produktionssättning enligt `docs/sakerhetsgranskning.md`
(ska-krav #33). Kritiska/Höga fynd ska åtgärdas före driftsättning.

## Regulatorisk hållning

Plattformen är byggd för att stödja den driftansvariges efterlevnad av följande
regelverk. **Den driftansvarige (personuppgiftsansvarig / skyldigheter som
väsentlig entitet) är ansvarig** för de regulatoriska skyldigheterna; plattformen
tillhandahåller de tekniska medlen.

- **NIS2 (direktiv (EU) 2022/2555) – ska-krav #27.** Lösningen tillhandahåller
  byggstenar för riskhantering som avbildas mot NIS2:s åtgärder: åtkomstkontroll
  och autentisering, kryptering av hemligheter och TLS, loggning/övervakning och
  spårbarhet, backup och återställning (`docs/sla.md`), transparens i
  leveranskedjan via SBOM, samt incidenthantering. Om en viss driftsättning
  *omfattas* av NIS2 avgörs av den driftande entiteten; plattformen stödjer de
  krävda kontrollerna.
- **Cybersäkerhetslagen – ska-krav #28.** Sveriges genomförande av NIS2. Samma
  tekniska kontroller och incidenthanteringsprocess (detta dokument +
  `docs/sla.md`) stödjer den driftansvariges skyldigheter, inklusive
  incidentrapportering till behörig myndighet där det är tillämpligt.
- **EU:s cybersäkerhetsakt (förordning (EU) 2019/881) – ska-krav #29.** Inrättar
  ENISA och EU:s ramverk för cybersäkerhetscertifiering. Lösningen bygger på
  standardiserade, certifierbara byggstenar (öppna protokoll, dokumenterad
  kryptering, SBOM) så att en driftsättning kan söka relevanta
  certifieringsscheman; ingen proprietär säkerhetskomponent hindrar detta.
- **OWASP Top 10 (2021) – ska-krav #30.** Varje kategori avbildas mot konkreta
  kontroller med kodreferenser och regressionstester i
  `docs/sakerhetsgranskning.md`.
