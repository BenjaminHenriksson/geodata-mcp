# Servicenivåavtal (SLA) (ska-krav #99)

Detta dokument beskriver de servicenivåer leverantören åtar sig för drift och
förvaltning av Geodata MCP v2: **tillgänglighet**, **incidenthantering**,
**underhållsfönster** och **svarstider**. Nivåerna är utformade för en kommunal
pilot- och produktionsdrift och bygger på lösningens faktiska tekniska
förutsättningar (hälsokontroller, observerbarhet och en tillståndslös arkitektur
där all data ligger i PostGIS + MinIO).

De konkreta nivåerna (procentsatser och tider nedan) är **förslag som fastställs
i avtal** mellan leverantör och Sundsvalls kommun. Dokumentet definierar
strukturen, mätmetoden och åtagandena.

## 1. Tjänstetider och tillgänglighet

| Begrepp | Definition |
| --- | --- |
| **Servicefönster** | Den tid tjänsten ska vara tillgänglig. Föreslås **07:00–19:00 helgfria vardagar** (kontorstid) för pilot; kan höjas till 24/7 i produktion. |
| **Tillgänglighet** | Andel av servicefönstret då tjänsten svarar korrekt på hälsokontroll, exklusive planerade underhållsfönster. |
| **Mätpunkt** | HTTP `GET /healthz` på `viewer` (8001) och `mcp` (8000), samt `pg_isready` mot Postgres. Se `docker-compose.yml` (`healthcheck` per tjänst). |

**Åtagen tillgänglighet (förslag):**

| Servicenivå | Tillgänglighet i servicefönstret | Typisk användning |
| --- | --- | --- |
| **Bas** | 99,0 % | Pilot / intern användning |
| **Förhöjd** | 99,5 % | Produktion, kontorstid |
| **Utökad** | 99,9 % | Produktion 24/7 |

Tillgänglighet mäts per kalendermånad. Planerade underhållsfönster (avsnitt 3)
och fel utanför leverantörens kontroll (t.ex. avbrott i kommunens nät eller i
externa källtjänster som Lantmäteriet/GeoServer) räknas **inte** som otillgänglig
tid.

### Tekniska förutsättningar som stödjer tillgängligheten

- **Automatisk omstart.** Alla containrar körs med `restart: unless-stopped`, så
  en kraschad process startas om automatiskt.
- **Hälsokontroller.** Varje tjänst har en `healthcheck`; orkestratorn (Compose,
  Kubernetes eller OpenShift) upptäcker en osund container och kan ersätta den.
- **Beroendeordning.** Tjänsterna startar först när Postgres och MinIO
  rapporterar `service_healthy`, vilket undviker startglapp.
- **Tillståndslös applikation.** All beständig data ligger i PostGIS och MinIO;
  `worker` och `viewer` kan startas om utan dataförlust (känd begränsning:
  `mcp`-transporten håller sessionsläge i minnet – se README "What's left").

## 2. Incidenthantering

En **incident** är en oplanerad avvikelse som helt eller delvis hindrar
avtalad användning. Incidenter klassas efter påverkan:

| Prioritet | Beskrivning | Exempel |
| --- | --- | --- |
| **P1 – Kritisk** | Tjänsten är otillgänglig eller data är i fara | `viewer`/`mcp` svarar inte; databasen nås inte; misstänkt dataintrång |
| **P2 – Hög** | Väsentlig funktion nere, ingen rimlig kringgående | Export fungerar inte; kartrendering trasig; en connector felar systematiskt |
| **P3 – Medel** | Begränsad påverkan, kringgående finns | Ett enskilt lager renderas fel; långsam men fungerande funktion |
| **P4 – Låg** | Kosmetiskt eller förbättringsförslag | Textfel, mindre UI-avvikelse |

### Svarstider och åtgärdstider (förslag, kontorstid)

| Prioritet | Bekräftad mottagning (svarstid) | Påbörjad åtgärd | Mål för lösning/kringgående |
| --- | --- | --- | --- |
| **P1** | 1 timme | 2 timmar | 4 timmar |
| **P2** | 2 timmar | 4 timmar | 1 arbetsdag |
| **P3** | 1 arbetsdag | 2 arbetsdagar | 5 arbetsdagar |
| **P4** | 2 arbetsdagar | Planeras | Nästa release |

- **Svarstid** = tid från registrerad anmälan till bekräftad mottagning och
  klassning.
- **Åtgärdstid** = tid till påbörjat felavhjälpande.
- Tider räknas inom servicefönstret om inget annat avtalas.

### Incidentflöde

1. **Anmälan** via avtalad kanal (e-post/ärendesystem), med tidsstämpel.
2. **Klassning** enligt prioritet ovan.
3. **Diagnos** med stöd av observerbarhetsdata: strukturerade JSON-loggar,
   Prometheus-metrics (`/metrics`) och OpenTelemetry-spår – se
   `docs/observability.md`. Provenienstabellen `app.query_log` och pgAudit-loggen
   ger spårbarhet på databasnivå.
4. **Åtgärd** och verifiering (relevanta testsviter körs, se `scripts/`).
5. **Återkoppling** till beställaren och stängning.
6. **Efteranalys (post-mortem)** för P1/P2: grundorsak och förebyggande åtgärd
   dokumenteras.

### Säkerhetsincidenter

Misstänkt eller bekräftad säkerhetsincident (t.ex. intrång, läckt nyckel)
hanteras alltid som **P1** och följer rutinen i `SECURITY.md`. Personuppgifts-
incidenter hanteras dessutom enligt GDPR:s krav på anmälan till IMY inom 72
timmar – se `docs/dataskydd-gdpr.md`. Regulatoriska anmälningsskyldigheter
(NIS2/Cybersäkerhetslagen) beaktas enligt `SECURITY.md`.

## 3. Underhållsfönster

Planerat underhåll (uppdateringar, säkerhetspatchar, databasmigrationer,
beroendeuppdateringar från SBOM) utförs i **förhandsaviserade fönster** och
räknas inte mot tillgängligheten.

| Egenskap | Åtagande (förslag) |
| --- | --- |
| **Ordinarie fönster** | Utanför servicefönstret, t.ex. vardagar 19:00–07:00 eller helg |
| **Avisering** | Minst **5 arbetsdagar** i förväg för planerat underhåll |
| **Akut säkerhetsunderhåll** | Får utföras utan ordinarie varsel vid kritisk sårbarhet; aviseras snarast möjligt |
| **Migrationer** | Databasändringar sker via idempotenta skript i `db/migrations/` och testas i en icke-produktionsmiljö först |
| **Återställning** | Varje planerad ändring har en dokumenterad återställningsväg (rollback) |

**Minimering av avbrott.** Eftersom applikationstjänsterna är tillståndslösa kan
`viewer` och `worker` rullas om med kort eller ingen märkbar nedtid bakom Caddy.
Databasmigrationer är utformade som additiva och icke-brytande där det är
möjligt (jfr `docs/observability.md`).

## 4. Säkerhetskopiering och återställning

| Begrepp | Åtagande (förslag) |
| --- | --- |
| **Backup, databas** | Daglig `pg_dump`/logisk backup av PostGIS; retention enligt avtal |
| **Backup, objektlager** | Spegling/versionering av MinIO-bucket |
| **RPO** (max dataförlust) | ≤ 24 timmar (dagliga backuper) – kan sänkas med WAL-arkivering |
| **RTO** (max återställningstid) | ≤ 4 timmar för P1 vid behov av återläsning |
| **Test av återställning** | Återläsning verifieras minst en gång per period med `scripts/validate_data.py` |

## 5. Rapportering och uppföljning

- **Månadsrapport**: uppmätt tillgänglighet, antal incidenter per prioritet,
  åtgärdstider, planerade och utförda underhåll.
- **Uppföljningsmöte**: enligt avtal (t.ex. kvartalsvis) för trender och
  förbättringar.
- **Underlag**: mätvärden hämtas från Prometheus/loggar; inga proprietära
  övervakningsagenter krävs (`docs/observability.md`).

## 6. Ansvarsavgränsning

Leverantörens SLA gäller lösningens egna tjänster. Utanför åtagandet ligger:

- Kommunens egen infrastruktur (nät, värdmaskiner, DNS, TLS-terminering om den
  drivs av kommunen).
- **Externa källtjänster** – WFS/WMS hos GeoServer, Lantmäteriets och
  Trafikverkets tjänster m.fl. Deras tillgänglighet ligger utanför leverantörens
  kontroll; systemet degraderar kontrollerat när en källa är otillgänglig.
- Ändringar som kommunen själv gör i koden eller konfigurationen (lösningen är
  öppen källkod och får ändras – se `docs/exitplan.md`).
