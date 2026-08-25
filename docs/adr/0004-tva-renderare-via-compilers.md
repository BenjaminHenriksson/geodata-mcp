# ADR 0004: Två renderare (MapLibre + Origo) via renderer-compilers

- **Status:** Antagen
- **Datum:** 2026-08-03
- **Berör:** `services/viewer`, kartspec-kontraktet (`app.map_views`)

## Kontext

Leveransen ska stödja både Origo (kommunens etablerade OpenLayers-baserade
kartklient, EPSG:3014) och MapLibre, och fler renderare senare. Kravet är att
agenten **aldrig** rör renderarkonfiguration och att kartan öppnas i en egen
webbsida via en URL. Om agenten skrev MapLibre- eller Origo-JSON direkt vore
varje renderare inbränd i verktygslagret.

## Beslut

Agenten skriver en liten **renderaragnostisk kartspec** (JSON i `app.map_views`:
lager-via-referens, kartografiska hintar, extent, basemap). Viewer-tjänsten kör
en **renderer-compiler** per klient: `map_view → MapLibre style JSON` respektive
`map_view → Origo-config`. MapLibre serveras på `/v/<id>` (EPSG:3857, Positron),
Origo på `/v/<id>?renderer=origo` (EPSG:3014, officiell kommunal WMS-backdrop).
Att lägga till en tredje renderare = skriva en compiler till, inget annat.

## Konsekvenser

- (+) Renderarna blir utbytbara; specen är liten och det en viss renderare inte
  kan uttrycka faller bort mjukt.
- (+) Samma spec ger bit-för-bit samma vy i båda klienterna (krav i DoD §7).
- (−) En compiler per renderare måste underhållas, och per-renderare-särdrag
  (backdrop, CRS, lagerordning i Origo) bor i compilern, inte i specen.

## Status

Antagen. Se `CONTRACTS.md` (Viewer, `/v/*/style.json` och `/v/*/origo.json`) och
`geodata-mcp-architecture.md` §6.
