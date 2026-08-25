# ADR 0005: Tunn dataendpoint nu, utbytbar mot Martin/TiPg/TiTiler

- **Status:** Antagen (OGC-uppgradering uppskjuten by decision)
- **Datum:** 2026-08-03
- **Berör:** `services/viewer` (dataendpoint)

## Kontext

Kartklienter kan inte läsa Postgres direkt — något måste servera lager över HTTP.
Fullständiga OGC-tjänster (Martin, TiPg/pg_featureserv, TiTiler-pgSTAC) är den
uppenbara industristandarden men innebär extra containrar och drift som v1 inte
behöver. Både Origo och MapLibre konsumerar GeoJSON och vektortiles nativt, så
kart-interoperabilitet hänger inte på OGC-infrastruktur.

## Beslut

Viewer-tjänsten äger en **tunn, read-only endpoint** rakt av Postgres:
`/data/{layer}.geojson` (`ST_AsGeoJSON`, radtak, 3014 för Origo / 4326 för
MapLibre) och `/tiles/{layer}/{z}/{x}/{y}.mvt` (`ST_AsMVT`). Registrerade
WMS/WMTS-referenslager skickas rakt igenom till kartkonfigen. OGC-tjänster hålls
**uppskjutna men bakom samma proxy-paths**; kartspec-kontraktet nämner aldrig
vilken tjänst som fyller en URL, så uppgraderingen blir "compilers repekar".

## Konsekvenser

- (+) I storleksordningen hundra rader kod, noll extra containrar; serverar och
  exporterar bevisas innan något kart-UI finns (migrationsteg 3).
- (+) Ingenting i v1 antar att den tunna endpointen är permanent.
- (−) Tunn endpoint klarar inte live-GIS-interop (QGIS mot OGC API Features) eller
  mycket hög tile-volym; det är precis vad den uppskjutna uppgraderingen löser.
- (−) TiTiler-pgSTAC krävs för att visa COG-raster i browser och kommer först med
  bild-milstolpen.

## Status

Antagen. Se `CONTRACTS.md` (Viewer) och `geodata-mcp-architecture.md` §5.
