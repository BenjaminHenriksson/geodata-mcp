# Beroendeförteckning – Geodata MCP v2

Detta dokument uppfyller ska-krav **#23** (öppen redovisning av tredjepartsberoenden
med licens och möjlig ersättare) och kompletterar den maskinläsbara SBOM:en
`sbom/geodata-mcp.cdx.json` (CycloneDX 1.5) som svarar mot ska-krav **#4**.

Plattformen (Geodata MCP v2, Sundsvalls kommun, upphandling UH-2026-159) släpps
under **AGPL-3.0-only** (överenskommet med beställaren; ersätter anbudets
ursprungliga EUPL/GPLv3-skrivning). Samtliga beroenden nedan har licenser som är
förenliga med att distribuera plattformen som helhet under AGPL-3.0.

## Så här läser du tabellerna

- **Namn** – komponentens paket-/produktnamn.
- **Syfte** – vad komponenten gör i lösningen.
- **Licens** – SPDX-identifierare (eller närmaste beskrivning där SPDX saknas).
- **Möjlig ersättare** – en eller flera realistiska alternativ om komponenten
  behöver bytas ut (leverantörsoberoende, licensskäl eller drift).

En versionsupplöst och maskinläsbar förteckning över exakta versioner finns i
SBOM:en. Regenerera per tjänst med `scripts/gen_sbom.sh` (använder `cyclonedx-py`).

---

## 1. Python-bibliotek (tjänsternas direktberoenden)

| Namn | Syfte | Licens | Möjlig ersättare |
| --- | --- | --- | --- |
| `mcp` (FastMCP) | MCP-kontrollplanet i `services/mcp`: streamable HTTP-server, verktygsregister, bearer-/OAuth2-auth. Officiell MCP Python-SDK. | MIT | Egen streamable-HTTP-/JSON-RPC-server ovanpå Starlette, eller annan MCP-serverimplementation. |
| `fastapi` | Web-ramverk för `services/viewer`, `services/worker` och `services/segmenter` (routing, validering, ASGI). | MIT | Litestar, Starlette direkt, Flask/Quart. |
| `starlette` | ASGI-grunden som FastAPI bygger på (transitivt beroende). | BSD-3-Clause | Litestar, rå ASGI, Quart. |
| `uvicorn` | ASGI-server som kör MCP-, viewer-, worker- och segmenter-processerna. | BSD-3-Clause | Hypercorn, Granian, Daphne. |
| `python-multipart` | Parsar `multipart/form-data` i viewer (filuppladdning/formulär). | Apache-2.0 | Starlettes inbyggda parser, egen parser. |
| `httpx` | HTTP-klient mot externa WFS/WMTS/OGC API/STAC-källor och mellan tjänster. | BSD-3-Clause | `aiohttp`, `requests` (synkront), `urllib3`. |
| `psycopg` (`psycopg[binary]`) + `psycopg-pool` | PostgreSQL/PostGIS-drivrutin (v3) och anslutningspool för alla tjänster. | LGPL-3.0-or-later | `asyncpg` (MIT), `pg8000` (BSD). Obs: LGPL kräver att drivrutinen förblir utbytbar – uppfylls eftersom den länkas dynamiskt. |
| `shapely` | Geometri­operationer i MCP (buffert, klippning, validering) via GEOS. | BSD-3-Clause | GEOS-bindningar direkt, `pygeos`/`geopandas`. |
| `numpy` | Numerisk grund för embeddings och bildarrayer i worker/segmenter. | BSD-3-Clause | – (de facto standard; svårt att ersätta helt). |
| `pdfplumber` | Textextraktion ur PDF i workerns `pdf`-connector. | MIT | `pypdf`, `pdfminer.six`, `PyMuPDF` (AGPL/kommersiell). |
| `sentence-transformers` | Kör embeddingsmodellen (EmbeddingGemma-300m) för semantisk sökning i worker. | Apache-2.0 | Egna `transformers`-anrop, `FlagEmbedding`, `model2vec`. |
| `minio` (Python-SDK) | S3-klient mot MinIO-objektlagret (export/artefakter). | Apache-2.0 | `boto3`/`aioboto3` (Apache-2.0), `s3fs`. |
| `huggingface-hub` | Nedladdning/cachning av modellvikter (embeddings, SAM3). | Apache-2.0 | Egen nedladdning/spegling, `hf_transfer`. |
| `pillow` | Bildinläsning/-konvertering i segmenter (SAM3). | HPND (PIL-licens) | `pillow-simd`, `opencv-python`, `imageio`. |
| `mlx` / `mlx-metal` | Apples MLX-ramverk – kör SAM3 på Apple Silicon (segmenter, native host). | MIT | `torch` (MPS/CUDA), `onnxruntime`. |
| `torch` / `torchvision` | Djupinlärnings­runtime för `transformers`-backend av SAM3 samt transitivt via modellstacken. | BSD-3-Clause | `onnxruntime`, ren MLX-backend. |
| `transformers` (extra `hf`) | Alternativ, container-/GPU-vänlig SAM3-backend (`facebook/sam3`). | Apache-2.0 | Ren MLX-backend, `onnxruntime`. |
| `accelerate` (extra `hf`) | Enhets-/minnesplacering för `transformers`-backend. | Apache-2.0 | Manuell `.to(device)`, `deepspeed`. |
| `mlx-sam3` | MLX-port av SAM3 (segmenteringsmodell), fäst till en git-commit. Importnamn `sam3`. | Ingen SPDX-licens deklarerad i uppström (NOASSERTION) – gör en licensbedömning före produktion; se anmärkning nedan. | Officiell `sam3`/`transformers`-implementation, SAM2, egen inferens. |

> **Anmärkning om modellvikter:** SAM3-vikterna (`mlx-community/sam3-image` och
> `facebook/sam3`) omfattas av Metas egna **SAM-licens**, och EmbeddingGemma
> omfattas av **Gemma Terms of Use** – båda är modell­licenser (ej OSI-godkända
> öppna källkodslicenser) och gäller vikterna, inte plattformskoden. De laddas
> ned vid drift och distribueras inte tillsammans med källkoden. Byte till en
> fullt öppen embeddings-/segmenteringsmodell är möjligt utan kodändringar av
> betydelse (se ersättarkolumnen).

## 2. Infrastruktur och containrar (icke-Python)

| Namn | Syfte | Licens | Möjlig ersättare |
| --- | --- | --- | --- |
| PostgreSQL | Relationsdatabas (bas för all lagring). | PostgreSQL License (BSD-lik) | MariaDB (ej geo-likvärdig), CockroachDB. |
| PostGIS | Geospatialt tillägg till PostgreSQL – kärnan i lagrings- och frågelagret. | GPL-2.0-or-later | SpatiaLite (`libspatialite`), DuckDB Spatial (serverlöst) – men PostGIS är de facto standard i svensk offentlig sektor. |
| MinIO (server) | S3-kompatibelt objektlager för export och artefakter. | AGPL-3.0-only | Garage (AGPL), SeaweedFS (Apache-2.0), Ceph RGW, eller managed S3 (t.ex. AWS S3, Cloudflare R2). AGPL på servern smittar inte plattformskoden (nås via nätverks-API/S3-protokoll). |
| Caddy | Omvänd proxy/TLS-terminering framför MCP och viewer (`deploy/Caddyfile`). | Apache-2.0 | Nginx, Traefik, HAProxy. |
| Docker / Compose | Paketering och orkestrering av de sex tjänsterna. | Apache-2.0 (Engine/Compose) | Podman + `podman-compose`, Kubernetes. |
| `osgeo/gdal` (basavbildning för worker) | GDAL/OGR-verktyg för geodatakonvertering i workern. | MIT/X11 (GDAL) | Egen basavbildning med GDAL installerat via `apt`. |

## 3. Frontend / kartklienter (webbtillgångar, ej CDN i drift)

Kartklienterna serveras lokalt från `services/viewer` (`/static/...`) – inga
externa CDN-anrop vid körning, vilket underlättar drift i slutna miljöer.

| Namn | Syfte | Licens | Möjlig ersättare |
| --- | --- | --- | --- |
| MapLibre GL JS | Vektor-/rasterkart­rendering i standard-viewern (`page.py`, `compile_maplibre.py`). | BSD-3-Clause | OpenLayers, Leaflet. |
| Origo | Alternativ kartklient/ramverk (Sveriges kommuners öppna kartklient, ovanpå OpenLayers) i `compile_origo.py`. | BSD-2-Clause | Ren OpenLayers, ren MapLibre. |
| OpenLayers | Underliggande kartmotor som Origo bygger på (transitivt). | BSD-2-Clause | MapLibre GL JS, Leaflet. |

---

## Licensöversikt och AGPL-förenlighet

Alla direktberoenden är licensierade under tillåtande (MIT, BSD, Apache-2.0, HPND)
eller svaga copyleft-licenser (LGPL-3.0 för `psycopg`) som är förenliga med att
distribuera hela verket under **AGPL-3.0-only**:

- **Tillåtande (MIT/BSD/Apache-2.0/HPND):** kan kombineras fritt; kräver endast
  bevarad upphovsrätts-/licenstext.
- **LGPL-3.0 (`psycopg`):** förenlig så länge biblioteket förblir dynamiskt
  länkat och utbytbart – vilket det är (pip-installerat, inte statiskt inbakat).
- **GPL-2.0-or-later (PostGIS) och AGPL-3.0 (MinIO-server):** är separata
  nätverkstjänster som nås via SQL respektive S3-API. De länkas inte in i
  plattformskoden och påverkar därför inte plattformens egen licensiering.
- **Modellvikter (SAM3, EmbeddingGemma):** egna modell­licenser som gäller
  vikterna, inte koden; laddas ned vid drift.
- **`mlx-sam3`:** saknar deklarerad SPDX-licens i uppström. Bedöm licensläget
  (eller byt till officiell `sam3`/`transformers`-implementation) innan
  produktionssättning om strikt licensspårbarhet krävs.

## Underhåll

- SBOM och denna förteckning ska uppdateras när ett nytt **direkt** beroende
  införs. En CI-kontroll (se `scripts/gen_sbom.sh`) kan jämföra genererade
  per-tjänst-SBOM:er mot den incheckade `sbom/geodata-mcp.cdx.json` och flagga
  nya direktberoenden som ännu inte är förtecknade här.
- Exakta, versionsupplösta beroenden finns i `services/segmenter/uv.lock`
  (segmenter) och i respektive `services/*/requirements.txt` (versionsintervall).
