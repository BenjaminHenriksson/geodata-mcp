# Lasttest: metod och resultatmall (ska-krav #38, #39, #41)

Detta dokument beskriver metoden för att lasttesta Geodata MCP v2 och en
resultatmall att fylla i vid varje körning. Det uppfyller:

| Krav | Innebörd |
| --- | --- |
| **#38** | Svarstid p95 < 500 ms under last |
| **#39** | Minst **1000 samtidiga användare** |
| **#41** | Automatiserat, repeterbart lasttest med tydligt pass/fail |

Lasttestet körs med **[k6](https://grafana.com/docs/k6/)** och skriptet
[`scripts/loadtest.js`](../scripts/loadtest.js). k6 är öppen källkod och kräver
ingen molntjänst, vilket ligger i linje med lösningens princip om öppna verktyg
utan inlåsning (`docs/exitplan.md`).

## 1. Vad som testas och varför `viewer`

`viewer`-tjänsten är systemets publika läsväg: kartsidor, kartstilar och
geodata (GeoJSON/MVT). Det är den yta som möter flest samtidiga användare i en
kommunal driftsättning, och därför den som p95- och samtidighetskraven är
relevanta för. Skriptet driver en **realistisk kartsession** mot de faktiska
endpoints som finns i [`services/viewer/main.py`](../services/viewer/main.py):

| Endpoint | Roll i sessionen |
| --- | --- |
| `GET /healthz` | Liveness (alltid tillgänglig) |
| `GET /v/{view}` | MapLibre-kartsida (HTML) |
| `GET /v/{view}/style.json` | MapLibre-stil (JSON, ETag-cachad) |
| `GET /v/{view}?renderer=origo` | Origo-kartsida (HTML) |
| `GET /v/{view}/origo.json` | Origo-konfiguration (JSON) |
| `GET /data/{layer}.geojson?view={view}` | Lagerdata som GeoJSON (behörighetskontrollerad) |
| `GET /tiles/{layer}/{z}/{x}/{y}.mvt?view={view}` | Vektortile (MVT) |

Varje virtuell användare (VU) i k6 utför en sådan sekvens med "tänketid"
emellan, vilket approximerar riktiga användare som öppnar en karta och panorerar.

## 2. Förutsättningar

1. **Ett kört system.** Starta stacken (`docker compose up`) i en miljö som
   liknar produktion – lasttest mot en utvecklingsmaskin mäter maskinen, inte
   lösningen.
2. **En kartvy att ladda.** Skapa en vy med `map`-verktyget (se
   `scripts/mcp_client.py`) och notera dess `view_id` samt ett lager som ingår i
   vyn (t.ex. `ref.naturreservat`). `/data`- och `/tiles`-vägarna är
   behörighetskontrollerade mot vyn (`_checked_layer` i `main.py`), så lagret
   måste vara en del av just den vyn.
3. **k6 installerat** på lastgeneratorn. För 1000 samtidiga VU:er bör
   generatorn vara en separat maskin med god nätkapacitet mot målet, annars blir
   generatorn flaskhalsen.

## 3. Körning

### Rök-test (verifiera uppsättning)

```sh
BASE_URL=http://localhost:8080 VIEW_ID=<view_id> \
  k6 run scripts/loadtest.js
```

### Fullständig ska-krav-körning (#38 + #39)

```sh
BASE_URL=https://geodata.example.se \
  VIEW_ID=<view_id> \
  LAYER=ref.naturreservat \
  VUS=1000 \
  DURATION=5m \
  RAMP=1m \
  k6 run scripts/loadtest.js
```

Detta rampar upp till **1000 samtidiga virtuella användare** (#39), håller lasten
i 5 minuter och rampar ner. Skriptet sätter tröskeln **p95 < 500 ms** (#38) på
`http_req_duration`; k6 avslutar med felkod om tröskeln inte hålls, vilket ger
det tydliga pass/fail som #41 kräver.

### Parametrar

Alla parametrar sätts som miljövariabler (se skriptets huvud):

| Variabel | Standard | Effekt |
| --- | --- | --- |
| `BASE_URL` | `http://localhost:8080` | Målets bas-URL |
| `VIEW_ID` | `""` | Kartvy att ladda (utan den körs endast health-mixen) |
| `LAYER` | `ref.naturreservat` | Vektorlager i vyn |
| `RENDERER` | `both` | `maplibre`, `origo` eller `both` |
| `VUS` | `50` | Antal samtidiga VU:er (sätt `1000` för #39) |
| `DURATION` | `1m` | Tid på toppnivå |
| `RAMP` | `30s` | Upp-/nedrampningstid |
| `P95_MS` | `500` | p95-tröskel i ms (#38) |

## 4. Tröskelvärden (pass/fail, #41)

Testet räknas som **godkänt** när samtliga trösklar hålls:

| Mätvärde | Tröskel | Krav |
| --- | --- | --- |
| `http_req_duration` p95 | **< 500 ms** | #38 |
| Samtidiga VU:er på topp | **≥ 1000** | #39 |
| `http_req_failed` (felkvot) | **< 1 %** | Meningsfullhet |

k6 returnerar exit-kod ≠ 0 om en `abortOnFail`-tröskel bryts, så testet kan
köras i CI och blockera en release automatiskt (#41). Skriptet skriver även en
maskinläsbar `loadtest-summary.json` och en textsammanfattning med de
krav-relevanta talen.

## 5. Resultatmall

Fyll i en rad per körning och arkivera tillsammans med `loadtest-summary.json`.

```
Lasttest – Geodata MCP v2 viewer
────────────────────────────────────────────────────────────
Datum/tid            :
Utförd av            :
Miljö (URL)          :
Målets specifikation :  (vCPU / RAM / värd / datacenter)
Lastgenerator        :  (var k6 kördes ifrån)
k6-version           :
Skript-commit        :  (git-sha för scripts/loadtest.js)
Vy / lager (VIEW_ID/LAYER):
Renderare (RENDERER) :

Parametrar
  VUS (samtidiga)    :
  RAMP               :
  DURATION           :

Resultat
  Totalt antal requests :
  Felkvot (%)           :
  Svarstid avg (ms)     :
  Svarstid p95 (ms)     :          [krav #38: < 500]
  Svarstid p99 (ms)     :
  Topp-VU:er            :          [krav #39: ≥ 1000]

Per endpoint (p95, ms)
  /v/{view} (sida)      :
  style.json/origo.json :
  /data + /tiles        :

Trösklar
  p95 < 500 ms (#38)    :  [ ] PASS   [ ] FAIL
  ≥ 1000 VU:er (#39)    :  [ ] PASS   [ ] FAIL
  felkvot < 1 %         :  [ ] PASS   [ ] FAIL

Samlat omdöme          :  [ ] GODKÄNT   [ ] UNDERKÄNT
Kommentar / flaskhals   :
Åtgärder               :
────────────────────────────────────────────────────────────
```

### Exempel på ifyllt resultat (illustrativt)

```
Datum/tid            : 2026-08-25 21:10
Miljö (URL)          : https://geodata.example.se
Parametrar           : VUS=1000, RAMP=1m, DURATION=5m
Totalt antal requests: 412 300
Felkvot (%)          : 0,03
Svarstid avg (ms)    : 118
Svarstid p95 (ms)    : 340        PASS (< 500, #38)
Topp-VU:er           : 1000       PASS (≥ 1000, #39)
Samlat omdöme        : GODKÄNT
```

*(Exempelsiffrorna är en illustration av mallens format, inte ett uppmätt
resultat.)*

## 6. Tolkning och åtgärder

- **p95 nära eller över 500 ms:** identifiera vilken endpoint som drar upp värdet
  via per-endpoint-trenderna (`viewer_page_ms`, `viewer_style_ms`,
  `viewer_data_ms`). Vanligast är `/data`/`/tiles` för stora lager – där hjälper
  geometriförenklingen (`SIMPLIFY_THRESHOLD`) och MVT redan (`docs/energi-co2.md`),
  och fler `viewer`-workers eller repliker skalar läsvägen (den är tillståndslös).
- **Databasen som flaskhals:** höj `max_connections`/`shared_buffers` i
  `docker-compose.yml` och överväg en connection pooler (PgBouncer – dokumenterad
  uppgraderingsväg i README "Deferred by decision").
- **Generatorn som flaskhals:** kör k6 distribuerat eller från en kraftfullare
  värd; verifiera att felkvoten inte beror på lokala socket-/CPU-gränser.

Lasttestet är avsett att köras återkommande (t.ex. inför varje release och som
del av SLA-uppföljningen, `docs/sla.md`) så att prestandakraven bevakas över tid.
