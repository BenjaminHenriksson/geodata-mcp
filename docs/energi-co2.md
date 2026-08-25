# Energiförbrukning och CO2 för drift (ska-krav #71, #72, #73)

Detta dokument redovisar en **metod** och en **uppskattning** av
energiförbrukning och koldioxidavtryck för att drifta Geodata MCP v2, per
komponent, samt de **optimeringar** som håller avtrycket nere (#71, #72) och
kravet på **datacenter inom EU/EES** (#73).

Siffrorna är transparenta uppskattningar baserade på komponenternas faktiska
resursprofil (se `docker-compose.yml` och `services/segmenter`). De ska ses som
en storleksordningsanalys för upphandlingsändamål, inte som en mätcertifierad
redovisning – metoden är utformad så att kommunen kan ersätta antagandena med
uppmätta värden från sin egen driftmiljö.

| Krav | Innebörd | Var det uppfylls |
| --- | --- | --- |
| **#71** | Energieffektiv drift / optimeringar | Avsnitt 3, 4 |
| **#72** | Redovisad energi- och klimatpåverkan | Avsnitt 1–3 |
| **#73** | Datacenter inom EU/EES | Avsnitt 5 |

## 1. Metod

Uppskattningen följer principen bakom **Software Carbon Intensity (SCI,
ISO/IEC 21031)** i förenklad form:

```
E_komponent (kWh) = P_medel (kW) × drifttid (h)
CO2 (kg)          = E_total (kWh) × PUE × I_nät (kg CO2e / kWh)
```

där

- **P_medel** = komponentens genomsnittliga elektriska effekt under drift,
- **PUE** (Power Usage Effectiveness) = datacentrets omkostnad för kylning och
  distribution (typiskt 1,1–1,2 för ett modernt nordiskt datacenter),
- **I_nät** = elmixens koldioxidintensitet på driftorten.

**Elmixens koldioxidintensitet** är den enskilt viktigaste faktorn och varierar
kraftigt med land:

| Elmix | I_nät (kg CO2e/kWh) | Kommentar |
| --- | --- | --- |
| Sverige (nordisk, fossilfri majoritet) | ~0,02–0,04 | Vatten/kärnkraft/vind |
| EU-genomsnitt | ~0,23 | Referens |
| Fossiltung mix | ~0,4–0,7 | Undviks (se #73) |

Uppskattningarna nedan använder **I_nät = 0,03 kg CO2e/kWh** (svensk mix) och
**PUE = 1,15** som huvudscenario, samt EU-genomsnitt som jämförelse för att visa
hur mycket driftorten betyder.

## 2. Komponenternas resursprofil

Systemet består av sex containrar (Postgres, MinIO, mcp, worker, viewer, caddy)
plus en valfri **GPU/segmenter-tjänst** för SAM 3. Effektförbrukningen delas i
en **basdrift** (tjänster som är igång dygnet runt men mestadels vilar) och en
**arbetslast** (CPU/GPU-intensiva jobb som körs vid behov).

| Komponent | Profil | Antagen P_medel | Kommentar |
| --- | --- | --- | --- |
| **Postgres/PostGIS** | I/O- och minnesbunden, mestadels vilande | 8–15 W | `shared_buffers=512MB`; spikar vid tunga rumsliga frågor |
| **viewer** (FastAPI, 3 workers) | Kortlivade förfrågningar, mestadels vilande | 3–8 W | Renderar/serverar GeoJSON+MVT; se `docs/lasttest.md` |
| **worker** (connectors + embeddings) | Skovis: tung vid ingest, annars vilande | 5–40 W | EmbeddingGemma-300M körs på CPU |
| **mcp** (FastMCP) | Lätt kontrollplan | 2–5 W | Orkestrerar verktygsanrop |
| **MinIO** | Objektlager, I/O-bunden | 2–6 W | Vilar mellan exporter |
| **caddy** | Reverse proxy | 1–3 W | Försumbar |
| **GPU-worker för SAM 3** | Endast under förändringsdetektering | 150–350 W (GPU) medan aktiv | **Inte dygnetruntdrift** – se nedan |

### Basdrift (allt utom GPU), dygnet runt

Summerad genomsnittlig basdrift antas till **~30 W** (0,030 kW) inklusive
databasens vilonivå. Det ger:

```
E_bas ≈ 0,030 kW × 24 h × 365 = ~263 kWh/år
```

| Elmix | CO2/år (PUE 1,15) |
| --- | --- |
| Svensk mix (0,03) | ≈ **9 kg CO2e/år** |
| EU-genomsnitt (0,23) | ≈ 70 kg CO2e/år |

Storleksordningen är alltså den för **en enskild kontorsdator som står på** –
avtrycket domineras helt av GPU-arbetslasten om förändringsdetektering körs ofta.

## 3. GPU-worker för SAM 3 – den dimensionerande posten

SAM 3-segmenteringen (`services/segmenter`) är den enda tunga beräkningsposten.
Den körs **inte kontinuerligt** utan startas per förändringsdetekterings-jobb
(`change_detect`) och är dessutom en **valfri** funktion (arkitekturbeslut
`docs/adr/0007-sam3-ortofoto-forandringsdetektering.md`). Detta är designval som
direkt håller nere energin (#71):

- **Modellen laddas lat** – `/healthz` triggar aldrig en modell-laddning
  (`services/segmenter/README.md`), så tjänsten drar nästan ingen ström när den
  inte används.
- **Ett `set_image` per bild**, textprompter itereras utan omladdning
  (`services/segmenter/backends.py`), vilket minimerar GPU-tid per körning.
- **Screening, inte kontinuerlig inferens** – jobbet körs när en revisor begär
  det, inte som en ständig ström.

Uppskattning per körning (illustrativ):

```
Antag 300 W GPU × 0,1 h (6 min) per ortofoto-jämförelse = 0,03 kWh/körning
→ svensk mix: ~0,001 kg CO2e/körning
→ 200 körningar/år: ~6 kWh, ~0,2 kg CO2e/år (svensk mix)
```

Även vid intensiv användning ligger GPU-posten alltså i samma storleksordning
som basdriften, tack vare att den är händelsestyrd. Om förändringsdetektering
**inte** används är GPU-avtrycket noll.

## 4. Optimeringar som sänker förbrukningen (#71, #72)

Följande är redan inbyggt i lösningen och kan verifieras i koden:

| Optimering | Effekt | Var |
| --- | --- | --- |
| **Händelsestyrd tung beräkning** | GPU drar ström endast under aktivt jobb, inte 24/7 | `services/segmenter`, `worker` `change_detect` |
| **Lat modell-laddning** | Ingen GPU-/minnesbelastning i viloläge | `services/segmenter/README.md` |
| **Liten, lokal embeddingmodell** | EmbeddingGemma-300M på CPU, 256 dims – ingen extern API-tjänst att anropa | README "Model stack", `.env.example` |
| **Vektortiles + geometriförenkling** | `viewer` förenklar geometrier över 5 000 features och serverar MVT i stället för rå GeoJSON, vilket sänker CPU och överförd datamängd | `services/viewer/main.py` (`SIMPLIFY_THRESHOLD`) |
| **ETag/If-None-Match-cachning** | Oförändrade kartstilar returneras som `304` utan omkompilering | `services/viewer/main.py` (`_etag_matches`) |
| **Statiskt vendrade frontend-bundlar** | MapLibre/Origo byggs in i imagen; inget hämtas från CDN vid körning → färre nätverksanrop | README "Two renderers" |
| **Tillståndslös, horisontellt skalbar design** | Kapacitet kan matchas mot last i stället för överdimensionering | README, `docs/adr/0003` |
| **Rätt-dimensionerad databas** | `shared_buffers`/`max_connections` satta för värdklassen i `docker-compose.yml` | `docker-compose.yml` |

**Ytterligare rekommenderade driftåtgärder** (för kommunens miljö):

- Schemalägg tunga ingest- och `change_detect`-jobb till tider med låg
  koldioxidintensitet i nätet där så är möjligt.
- Skala ned eller pausa `worker`/segmenter utanför arbetstid.
- Sätt livscykelregel på MinIO så gamla exporter gallras (README "What's left"),
  vilket sänker lagrings-energin.

## 5. Datacenter inom EU/EES (#73)

Lösningen är **platsoberoende och självhostad** – den kör på valfri
OCI-kompatibel värd (`docker compose up`) utan bindning till en specifik
molnleverantör (se `docs/exitplan.md`). Det gör kravet på EU/EES-placering enkelt
att uppfylla: driften förläggs till ett datacenter inom EU/EES, förslagsvis i
**Sverige eller Norden**, vilket ger både

- **regelefterlevnad** – data och behandling stannar inom EU/EES, i linje med
  GDPR och `docs/dataskydd-gdpr.md`, och
- **lägst klimatavtryck** – nordisk elmix har bland Europas lägsta
  koldioxidintensitet (avsnitt 1), vilket enligt beräkningen ovan sänker
  drift-CO2 med ungefär **en storleksordning** jämfört med EU-genomsnittet.

Rekommendationen är därför drift i ett **svenskt/nordiskt datacenter inom EU/EES**
med dokumenterat låg PUE och fossilfri eller ursprungsmärkt förnybar el.
Placeringen fastställs i avtal och dokumenteras som en del av driftöverenskommelsen.

## 6. Sammanfattning

- Basdriften motsvarar storleksordningen **en ständigt påslagen kontorsdator**
  (~260 kWh/år), och den dimensionerande posten – SAM 3 på GPU – är
  **händelsestyrd och valfri**, inte kontinuerlig.
- Med **svensk/nordisk elmix** landar det totala drift-CO2 för ett typiskt
  pilotscenario i storleksordningen **tiotals kg CO2e/år**, mot flera hundra kg
  vid EU-genomsnittlig mix. Driftorten är den största hävstången.
- Uppskattningarna är transparenta och avsedda att ersättas med kommunens egna
  uppmätta värden (effekt via PDU/hypervisor, elmix via elleverantör) för en
  löpande, faktisk redovisning.
