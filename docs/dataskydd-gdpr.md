# Dataskydd och GDPR (ska-krav #63, #67, #68, #64)

Detta dokument beskriver hur Geodata MCP v2 hanterar dataskydd: **inbyggt
dataskydd (privacy by design)**, en **behovsbedömning av konsekvensbedömning
(DPIA)**, **registrerades rättigheter**, att **beställaren äger sin data**, att
**ingen modellträning sker på beställarens data**, samt förhållandet till
**EU:s dataförvaltningsförordning (Data Governance Act)**.

| Krav | Innebörd | Var det uppfylls |
| --- | --- | --- |
| **#63** | Inbyggt dataskydd (privacy by design/by default) + registrerades rättigheter | Avsnitt 2, 4 |
| **#67** | Beställaren äger och kontrollerar sin data | Avsnitt 5 |
| **#68** | Ingen modellträning på beställarens data | Avsnitt 6 |
| **#64** | Förenlighet med Data Governance Act | Avsnitt 7 |

## 1. Systemets karaktär: mestadels öppna geodata, lite personuppgifter

Geodata MCP v2 arbetar huvudsakligen med **öppna kommunala geodata** (detaljplaner,
byggnader, naturreservat, strandskydd m.m.) från GeoServer, Lantmäteriet och
Trafikverket. Merparten är inte personuppgifter i GDPR:s mening.

Personuppgifter kan dock förekomma, framför allt i **adress- och
fastighetsanknuten data**. Exempel i pilotens datamängd:

- `ref.adressplats` (36 247 adresspunkter, driver även `app.geocode()`),
- `ref.fastighetsgrans` och byggnadsdata där en fastighet kan knytas till en
  fysisk person,
- fritextfält i planhandlingar/dokument som kan innehålla namn.

En adress eller fastighet blir en personuppgift när den **kan kopplas till en
identifierbar fysisk person**. Behandlingen ska därför utgå från att sådana
uppgifter *kan* förekomma och skyddas därefter.

## 2. Inbyggt dataskydd och dataskydd som standard (#63)

Följande är inbyggt i lösningens design och kan verifieras i koden. Det utgör
grunden för *privacy by design/by default* (artikel 25 GDPR):

| Princip | Hur den realiseras | Var |
| --- | --- | --- |
| **Dataminimering** | Endast katalogförda källor hämtas; `viewer` returnerar enbart de kolumner ett lager exponerar, geometrier förenklas över en tröskel | `data_sources.xlsx`, `services/viewer/main.py` |
| **Åtkomstkontroll** | `/mcp` kräver giltig nyckel (401 annars); `/data`/`/tiles`/`/wmsref` är capability-kontrollerade mot en namngiven vy | `services/mcp`, `services/viewer/main.py` |
| **Least privilege** | Agent-SQL körs som read-only-roll (`agent_ro`); skrivning är workspace-scoped (`agent_ws`) | README "Security boundaries", `sqlguard.py` |
| **Skydd mot injektion/XSS** | SQL-validering och CSP utan `unsafe-inline` | `services/mcp/sqlguard.py`, `services/viewer/main.py` |
| **Kryptering av hemligheter** | API-nycklar lagras endast som SHA-256-digest; TLS i proxy; DB/MinIO host-only | README, `docker-compose.yml` |
| **Spårbarhet** | Proveniens (`app.query_log`), pgAudit, strukturerade loggar | `services/mcp/provenance.py`, `docs/observability.md` |
| **Gallringsbarhet** | Data kan raderas kontrollerat (workspace-radering, `docker compose down -v`, tömning av S3) | `docs/exitplan.md` |
| **EU/EES-drift** | Självhostad, platsoberoende; förläggs inom EU/EES | `docs/energi-co2.md` §5 |

De tekniska säkerhetskontrollerna beskrivs i sin helhet i
`docs/sakerhetsgranskning.md` (OWASP-kartläggning) och `SECURITY.md`.

## 3. Roller enligt GDPR

I ett kommunalt uppdrag är **Sundsvalls kommun personuppgiftsansvarig** och
leverantören (vid driftåtagande) **personuppgiftsbiträde**. Ett
**personuppgiftsbiträdesavtal (PUB-avtal/DPA)** enligt artikel 28 GDPR ska
upprättas och reglera bland annat: behandlingens föremål och syfte, instruktioner
från den ansvarige, sekretess, säkerhetsåtgärder, hantering av eventuella
underbiträden (t.ex. datacenteroperatör inom EU/EES), bistånd vid registrerades
rättigheter och incidenter, samt radering/återlämnande vid avtalets slut.

Om kommunen driftar systemet helt själv (enligt exitplanen) är kommunen både
ansvarig och driftansvarig och något biträdesavtal behövs inte för leverantören.

## 4. Behovsbedömning av konsekvensbedömning (DPIA) och registrerades rättigheter

### 4.1 DPIA-behovsbedömning (artikel 35 GDPR)

En **konsekvensbedömning avseende dataskydd (DPIA)** krävs när en behandling
"sannolikt leder till en hög risk" för registrerade. En förenklad
behovsbedömning för denna lösning:

| Riskindikator (art. 35 / IMY:s lista) | Bedömning för Geodata MCP v2 |
| --- | --- |
| Storskalig behandling av känsliga uppgifter (art. 9) | **Nej** – inga känsliga kategorier behandlas avsiktligt |
| Systematisk övervakning / kartläggning av individer | **Nej** – data är objekt-/fastighetsorienterad, inte personprofilering |
| Automatiserat beslutsfattande med rättslig verkan | **Nej** – systemet är ett analys-/beslutsstöd; SAM 3-resultat är uttryckligen *screening, inte fakta* (ADR 0007), en människa beslutar |
| Storskalig behandling av personuppgifter | **Delvis** – adress/fastighetsdata kan vara storskaligt personanknuten |
| Ny teknik / AI | **Ja** – LLM-agent + embeddings, vilket i sig kan motivera en DPIA |

**Slutsats:** Behandlingen är sannolikt **inte** i sig högrisk, men två faktorer
– storskalig adress-/fastighetsanknuten data och användning av AI-teknik – gör
att kommunen som personuppgiftsansvarig **bör genomföra en DPIA** innan
produktionssättning, åtminstone en förenklad sådan, och besluta om en fullständig
DPIA behövs utifrån den faktiska användningen. Leverantören bistår med det tekniska
underlaget (denna dokumentation, arkitektur, säkerhetskontroller, SBOM).

### 4.2 Registrerades rättigheter (art. 12–22)

Lösningens egenskaper stödjer den ansvariges förmåga att tillgodose rättigheterna:

| Rättighet | Hur den stöds |
| --- | --- |
| **Tillgång / registerutdrag** (art. 15) | Data är sökbar med `query`/`search`; källor och proveniens redovisas (`sources`, `app.query_log`) |
| **Rättelse** (art. 16) | Rättas i källsystemet och läses in igen; ingen "sanning" skapas utanför källan |
| **Radering** (art. 17) | Kontrollerad radering av workspace-/exportdata (`docs/exitplan.md`); källdata rättas hos ägaren |
| **Dataportabilitet** (art. 20) | Export i öppna format: GeoPackage/GeoJSON/CSV/Parquet (`docs/exitplan.md` §2) |
| **Invändning / begränsning** (art. 18, 21) | Åtkomst kan stängas (nyckel avaktiveras) och lager/vyer tas bort |
| **Information** (art. 13–14) | Denna dokumentation + proveniensredovisning ger transparens om behandlingen |

En viktig princip: systemet **speglar källan troget** och skapar inte egna
personregister vid sidan av. Rättelse och radering av grunddata sker hos
respektive dataägare; systemet läser om och exporterar därifrån.

## 5. Beställaren äger och kontrollerar sin data (#67)

- **All beständig data ligger hos beställaren** – i en PostGIS-databas och ett
  S3-objektlager som körs i beställarens (eller på beställarens uppdrag) EU/EES-
  miljö. Ingen data lämnas till en tredjepartstjänst för behandling.
- **Ingen leverantörsinlåsning:** hela lösningen är öppen källkod (AGPL-3.0-only)
  och data kan när som helst exporteras i öppna format eller dumpas med
  `pg_dump` (`docs/exitplan.md`). Beställaren kan drifta, migrera och radera helt
  i egen regi.
- **Kontroll över åtkomst:** beställaren äger nycklar och `VIEWER_SECRET`, styr
  vem som når `/mcp` och manager-UI:t, och kan återkalla åtkomst.

## 6. Ingen modellträning på beställarens data (#68)

- **EmbeddingGemma-300M** körs **lokalt** i `worker`-containern (förtränad,
  öppen vikt) och används endast för *inferens* – att skapa sökvektorer. Inga
  vikter uppdateras; ingen finjustering eller träning sker på beställarens data
  (README "Model stack", `.env.example`).
- **SAM 3** (segmentering) körs likaså som lokal *inferens* bakom ett HTTP-kontrakt
  (`services/segmenter`); den promptas per körning och tränas inte
  (`docs/adr/0007-...`).
- **Inga externa AI-API:er:** systemet anropar ingen extern modelltjänst för att
  behandla beställarens data. Embeddings och segmentering sker på egen hårdvara,
  vilket innebär att beställarens data aldrig skickas till en tredje part för
  vare sig inferens eller träning.
- Om en **extern LLM-klient** (t.ex. via MCP-anslutning) används av kommunen för
  att styra agenten, sker det under kommunens egen kontroll och avtal med den
  klientleverantören; själva plattformen exponerar data via behörighetskontrollerade
  verktyg och tränar inte på den. Detta bör regleras i kommunens interna riktlinjer
  för AI-användning.

## 7. Data Governance Act (dataförvaltningsförordningen) (#64)

EU:s **Data Governance Act (DGA, förordning (EU) 2022/868)** främjar tillgång
till och återanvändning av data, inklusive data som innehas av offentliga organ,
under förtroendeingivande former. Lösningen ligger väl i linje med DGA:

- **Främjar återanvändning av offentlig data** – öppna geodata görs sökbara och
  exporterbara i öppna, interoperabla format (GeoPackage/GeoJSON/CSV/Parquet),
  vilket underlättar vidareutnyttjande enligt DGA:s syfte och samspelar med
  öppna-data-/PSI-regelverket.
- **Proveniens och spårbarhet** – varje lager bär sitt ursprung och sin licens
  (`sources`, `app.query_log`, `services/mcp/provenance.py`), vilket stödjer den
  transparens och det förtroende DGA eftersträvar.
- **Skydd vid delning** – där data innehåller skyddsvärda inslag
  (personuppgifter, sekretess) gäller åtkomstkontroll och de dataskyddsåtgärder
  som beskrivs ovan, så att återanvändning kan ske utan att skyddet urholkas.
- **Interoperabilitet och öppna standarder** – hela lösningen bygger på öppna
  standarder utan inlåsning (`docs/exitplan.md`), vilket är förenligt med DGA:s
  och den bredare europeiska datastrategins mål.

DGA reglerar främst **villkoren för delning och återanvändning**, inte den
enskilda behandlingens laglighet – den bedöms alltjämt enligt GDPR (avsnitt 2–5).
Lösningen ger kommunen de tekniska förutsättningarna (öppna format, proveniens,
åtkomstkontroll) att dela och återanvända data på ett DGA-förenligt sätt.

## 8. Sammanfattning och rekommendationer före produktion

1. Upprätta **PUB-avtal (DPA)** om leverantören driftar (avsnitt 3).
2. Genomför kommunens **DPIA-behovsbedömning** (avsnitt 4.1) och en DPIA om den
   faktiska användningen motiverar det.
3. Fastställ **EU/EES-datacenter** och dokumentera eventuella underbiträden.
4. Bekräfta rutiner för **registrerades rättigheter** och **personuppgifts-
   incidenter** (72-timmarsregeln, se `docs/sla.md` och `SECURITY.md`).
5. Anta interna **riktlinjer för AI-användning** för de klienter som styr agenten.

Med dessa på plats behandlas personuppgifter i enlighet med GDPR:s principer,
beställaren behåller full äganderätt och kontroll över sin data, ingen träning
sker på datan, och lösningen är förenlig med Data Governance Act.
