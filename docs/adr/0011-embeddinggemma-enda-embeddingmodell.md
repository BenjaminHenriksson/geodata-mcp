# ADR 0011: EmbeddingGemma som enda embedding-modell (v1)

- **Status:** Antagen
- **Datum:** 2026-08-03
- **Berör:** `services/worker` (`/embed`, `embed_catalog`), katalog- och dokumentsök

## Kontext

Katalog- och dokumentsökning bygger på semantiska embeddings (pgvector). Två hårda
begränsningar gäller: modellerna måste vara av **västerländskt ursprung** (inga
Qwen-härledda vikter, vilket utesluter jina-embeddings-v4, nomic-embed-multimodal
och mxbai-rerank-v2) och de måste **köras lokalt**. Svensk språktäckning och liten
pgvector-indexstorlek är ytterligare önskemål.

## Beslut

**EmbeddingGemma-300M** (Google, öppna vikter) är den **enda** embedding-modellen i
v1, via den ogatade spegeln `unsloth/embeddinggemma-300m`, **trunkerad till 256
dimensioner** (Matryoshka) och körd på **CPU** inne i worker-containern
(`/embed`, `sentence-transformers`). **Ingen reranker** i v1 — vid katalogskala
rerankar klient-LLM:en top-k själv. `embedding_model` + version lagras på varje
embeddad rad; `embed_catalog` om-embeddar bara rader som saknas eller har fel modell.

## Konsekvenser

- (+) Lokal CPU-inferens, 100+ språk inkl. svenska, litet vektorindex vid 256d,
  och en enda modell att resonera om och byta.
- (+) Utbytbar utan API-ändring (tyngre alternativ: NVIDIA Nemotron Embed / e5-large;
  reranker: llama-nemotron-rerank bakom `search`).
- (−) Multimodala embeddings är medvetet **inte** i v1 — modalitet hanteras vid
  ingest (PDF→textchunkar, bild→SAM-masker + STAC-metadata).
- (−) Mixed-model-tillstånd måste vara detekterbara, aldrig tysta — därav den
  lagrade modellstämpeln.

## Status

Antagen. Se `CONTRACTS.md` (Worker `/embed`, `embed_catalog`) och
`geodata-mcp-architecture.md` §12.
