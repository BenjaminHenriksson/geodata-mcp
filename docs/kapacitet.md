# Kapacitet och dimensionering — Geodata MCP v2

Detta dokument anger rekommenderad dimensionering (CPU, RAM, lagring och nät) per tjänst för två
driftprofiler:

- **Pilot** — en enda värd, ett fåtal samtidiga användare, pilotdatat för **Sundsvalls kommun**
  (12 ingesterade `ref`-lager, katalog med 2 100+ dataset, inbäddningar). Motsvarar den
  `docker compose`-driftsättning som beskrivs i [`drifthandbok.md`](drifthandbok.md), avsnitt 3.1.
- **Produktion** — flerpersonersdrift hos beställaren med redundans, TLS och central övervakning.
  Motsvarar en Helm-/Kubernetes-driftsättning (`deploy/helm/`).

Siffrorna är dimensioneringsriktlinjer (per tjänst/replik), inte hårda gränser. Kalibrera mot
faktisk last med mätvärdena i `docs/observability.md` (#81). Se topologin i
[`CONTRACTS.md`](../CONTRACTS.md) och tjänsteöversikten i [`drifthandbok.md`](drifthandbok.md),
avsnitt 1.

---

## 1. Dimensioneringstabell — Pilot (en värd)

Profil: 1–5 samtidiga användare, ett agentflöde i taget, pilotdatat. Allt körs på en värd.

| Tjänst | vCPU (typ./max) | RAM (typ./max) | Lagring | Nät | Kommentar |
|--------|-----------------|----------------|---------|-----|-----------|
| **postgres** | 1 / 2 | 2 / 4 GB | **25 GB** (persistent, `pgdata`) | intern | `shared_buffers=512MB`, `max_connections=120` (satt i compose). Ref-tabeller med geometri + GIST-index, katalog med `vector(256)`-inbäddningar, provenance/audit, WAL. Tyngsta lagringsposten. |
| **worker** | 2 / 4 | 3 / 6 GB | 3 GB (`hf-cache` ~1,2 GB + `/tmp`-scratch för ingest/export) | intern + **extern** | EmbeddingGemma-300M laddas i minnet (~1,2 GB), batch 32, CPU-bunden vid inbäddning; ogr2ogr-subprocesser vid ingest. Skördar från externa källor. |
| **viewer** | 1 / 2 | 1 / 2 GB | — (tillståndslös) | intern + klient | Serverar GeoJSON/MVT-tiles; CPU-toppar vid `ST_AsMVT` för stora lager. WMS-proxy. |
| **mcp** | 0,5 / 1 | 0,5 / 1 GB | — (sessioner i minnet) | intern | Lättviktigt styrplan; håller streamable-HTTP-sessioner i processminnet. |
| **minio** | 0,5 / 1 | 0,5 / 1 GB | **10 GB** (persistent, `minio-data`) | intern + klient | Exportbucket; växer utan livscykelregel (housekeeping). |
| **caddy** | 0,25 / 0,5 | 128 / 256 MB | 1 GB (certifikat/`/data`) | klient | Omvänd proxy, en ingång. |
| **segmenter** (native, valfri) | GPU-bunden | ≥ **16 GB** enhetligt minne (Apple Silicon) | 4 GB (SAM 3-vikter ~3,4 GB) | intern (till worker) | Endast vid `change_detect`. MLX på Apple Silicon, eller CUDA-GPU ≥ 8 GB VRAM med `transformers`-backend. |
| **Summa värd (utan segmenter)** | **~6 vCPU** | **~16 GB RAM** (24–32 GB bekvämt) | **~50–60 GB SSD** | — | Räkna med marginal för OS, byggcache och Docker. |

Riktvärde för pilotvärd: **8 vCPU, 32 GB RAM, 100 GB SSD**. Segmentern körs lämpligen på en
separat Apple Silicon-maskin (t.ex. utvecklarens arbetsstation) och behövs bara när
förändringsdetektering körs.

---

## 2. Dimensioneringstabell — Produktion (Kubernetes/Helm)

Profil: många samtidiga användare, redundans, hanterade backuper och TLS. Siffror **per replik**.

| Tjänst | vCPU (req/limit) | RAM (req/limit) | Lagring | Nät | Repliker & noteringar |
|--------|------------------|-----------------|---------|-----|------------------------|
| **postgres** | 4 / 8 | 16 / 32 GB | **200–500 GB** NVMe (PV) + separat WAL/PITR-arkiv | intern, hög I/O | 1 primär (+ ev. läsreplika). `shared_buffers` 4–8 GB. Full kommunkorpus utöver de 12 pilotlagren, många workspaces, audit/WAL. Överväg hanterad PostGIS med automatiska backuper. |
| **worker** | 4 / 8 | 8 / 16 GB | 5 GB (`hf-cache`) + scratch-PV för ingest/export | intern + **extern** (hög) | **1 replik tills lease-baserad jobbåterhämtning finns** (se drifthandbok §11). Inbäddningsgenomströmning skalar med kärnor; överväg GPU för embedding vid volym. Stora COG-nedladdningar vid förändringsdetektering. |
| **viewer** | 2 / 4 | 2 / 4 GB | — (tillståndslös) | intern + klient (hög) | **2+ repliker**, horisontellt skalbar bakom ingress. Rekommendera tile-cache framför för stora lager. |
| **mcp** | 1 / 2 | 1 / 2 GB | — (sessioner i minnet) | intern | **Sticky sessions krävs** för flera repliker (streamable-HTTP-tillstånd i minnet), alt. SDK:ns tillståndslösa läge. |
| **minio** | 1 / 2 | 2 / 4 GB | **100+ GB** (helst distribuerat/erasure-coded) | intern + klient | Med livscykelregel för att åldra ut exporter, eller ersatt av hanterad S3-kompatibel lagring. |
| **caddy / ingress** | 0,5 / 1 | 256 / 512 MB | 1 GB (certifikat) | klient (TLS) | TLS-terminering, HTTP/2, Let's Encrypt (drifthandbok §8). Behåll `flush_interval -1` för `/mcp`. |
| **segmenter** (native/GPU-nod) | GPU-bunden | ≥ **32 GB** (Apple Silicon) / ≥ **16 GB VRAM** (CUDA) | 4 GB (vikter) | intern (till worker) | Dedikerad GPU-nod. `facebook/sam3` via `transformers` på CUDA (HF-gated — begär åtkomst), eller Apple Silicon via MLX. Skala efter antal samtidiga `change_detect`-jobb. |

Notera skalningsförbehållen i [`drifthandbok.md`](drifthandbok.md), avsnitt 11: `mcp` behöver
sticky sessions och `worker` behöver lease-baserad återhämtning innan de körs med fler än en
replik. `viewer` och `caddy` är tillståndslösa och skalar horisontellt utan förbehåll.

---

## 3. Lagring — tillväxt och planering

All beständig lagring finns i **Postgres** och **MinIO** (se [`drifthandbok.md`](drifthandbok.md),
avsnitt 6).

- **Postgres (`pgdata`):** dominerande poster är ref-tabellernas geometri och GIST-index
  (t.ex. `fastighetsgrans` 101 226 rader, `byggnader` 85 499, `adressplats` 36 247),
  katalogens `vector(256)`-inbäddningar för 2 100+ dataset och dokumentchunkar, samt
  provenance-/pgAudit-spåret som växer monotont med användningen. Pilot: ~15–25 GB. Produktion:
  planera för 200–500 GB beroende på hur många lager och workspaces som tas i drift, och sätt en
  retention-/vakuumpolicy för audit-tabellerna.
- **MinIO (`minio-data`):** exportfiler ackumuleras utan livscykelregel (den presignerade URL:en
  förfaller efter 24 h men objektet ligger kvar). Sätt en MinIO-lifecycle-regel i produktion.
- **HuggingFace-cache (`hf-cache`):** ~1,2 GB (EmbeddingGemma). Återskapbar — behöver inte
  säkerhetskopieras.
- **SAM 3-vikter (segmenter, native):** ~3,4 GB i `~/.cache/huggingface` på värden. Återskapbar.

---

## 4. Nät

- **Internt (compose-/klusternät):** tjänst-till-tjänst-trafik, framför allt viewer↔postgres
  (GeoJSON/MVT-generering) och worker↔minio (export-uppladdning). Tile-/GeoJSON-serveringen kan
  vara bandbreddstung för stora lager.
- **Externt (utgående):** worker skördar och ingesterar från `karta.sundsvall.se/geoserver`,
  `isyroad.isy.se` och autentiserade Lantmäteriet-tjänster. Förändringsdetektering strömmar stora
  COG-fönster via `/vsicurl/` över HTTPS — kan uppgå till flera GB per jobb. Säkerställ god
  utgående bandbredd och att brandväggen släpper igenom dessa värdar.
- **Klientvänd (inkommande):** kartsidor, tiles, GeoJSON och exportnedladdningar (presignerade
  MinIO-URL:er på `:9000`, utanför Caddy). Bandbredden styrs av lagerstorlek och antal samtidiga
  kartklienter.
- **Riktvärden:** pilot klarar sig på ~100 Mbit/s. Produktion bör ha ≥ 1 Gbit/s, särskilt för
  COG-fönstring vid förändringsdetektering och för exportnedladdningar.
- **Portar:** extern trafik går genom en ingång (Caddy, värdport 8080 → TLS 443 i produktion).
  Postgres (5433) och MinIO-konsolen (9001) är bundna till `127.0.0.1`. MinIO-API:t (9000) måste
  vara klientnåbart för presignerade länkar men läggs bakom TLS före icke-lokal användning
  (drifthandbok §8).

---

## 5. Antaganden och kalibrering

- Siffrorna utgår från pilotdatat (Sundsvall, 12 `ref`-lager) och ett agentflöde i taget för
  piloten. Fler samtidiga agentflöden ökar främst last på **postgres** (samtidiga `query`/tiles)
  och **worker** (samtidiga jobb).
- **Toppar:** worker-CPU toppar vid inbäddning (batch 32) och ogr2ogr-ingest; viewer-CPU toppar
  vid MVT-generering för stora lager; postgres-I/O toppar vid ingest och vid tunga rumsliga
  frågor. Dimensionera limits efter topparna, inte medelvärdet.
- **Modellvikter laddas lat:** första inbäddningsanropet efter en worker-omstart laddar ner
  ~1,2 GB och kan överskrida sökverktygets embed-timeout (search faller då tillbaka till trigram
  för det anropet). Detta är övergående och kräver ingen extra dimensionering.
- Kalibrera mot verklig last med Prometheus-mätvärdena och larmen i `docs/observability.md` (#81):
  jobbködjup, ingest-/embed-latens, tile-serveringstid och diskanvändning för `pgdata`/`minio-data`.
