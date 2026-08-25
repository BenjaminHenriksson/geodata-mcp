# ADR 0008: MinIO/S3 för export- och blob-lagring

- **Status:** Antagen
- **Datum:** 2026-08-03
- **Berör:** `services/worker` (`export`), `services/minio`

## Kontext

Exportfiler (GPKG/GeoJSON/CSV/Parquet), COG-raster och andra stora artefakter hör
inte hemma i Postgres — de är opaka blobbar, inte relationell state. Export till
standard-GIS (QGIS, ArcGIS) är ett krav, och filvägen är i v1 hela QGIS-storyn
tills de uppskjutna OGC-endpointsen landar (ADR 0005).

## Beslut

Ett **S3-kompatibelt objektlager** (MinIO on-prem, moln-S3 i produktion) håller
exporter och framtida COG-artefakter. `export`-jobbet kör `ogr2ogr` från PostGIS,
laddar upp till `exports/`-bucketen och returnerar en **presignerad GET-URL** (24 h)
plus en `citation.md`-sidecar byggd ur `app.provenance`. Presignerade URL:er
genereras mot `S3_PUBLIC_ENDPOINT` (direkt host-port) — signatursäkert och
medvetet **inte** proxat genom Caddy. Workern säkerställer att bucketen finns vid
uppstart.

## Konsekvenser

- (+) Blobbar hålls utanför databasen bakom ett standard-S3-API; presignerade
  URL:er ger capability-baserad åtkomst (att känna länken är behörigheten).
- (+) Samma image/kontrakt fungerar mot MinIO lokalt och en suverän moln-S3 senare.
- (−) Ytterligare en stateful tjänst — men dess tillstånd är objekt, inte
  processminne, vilket bevarar den statslösa disciplinen (ADR 0001).
- (−) COG-ingest + pgSTAC/TiTiler-visning kommer först med bild-milstolpen.

## Status

Antagen. Se `CONTRACTS.md` (Topology, Worker `export`) och `geodata-mcp-architecture.md` §5.4.
