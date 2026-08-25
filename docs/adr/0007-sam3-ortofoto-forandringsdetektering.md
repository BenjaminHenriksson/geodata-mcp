# ADR 0007: SAM 3 för ortofoto-förändringsdetektering

- **Status:** Antagen (med licensförbehåll)
- **Datum:** 2026-08-03
- **Berör:** `services/segmenter`, `services/worker` (`change_detect`), `analyze`-verktyget

## Kontext

Förändringsdetektering över ortofoton (byggnader, uppfarter, upplag som dykt upp
i områden utan bygglov) är ett arbetslasttyp för revision — inte ett arkitektoniskt
särfall. Vi vill att agenten själv väljer *vad* den letar efter per errand, utan
per-klass-modellträning. Vid kommunal ortofotoupplösning är signalen dessutom
approximativ och måste behandlas som ett screeninginstrument, inte som fakta.

## Beslut

**SAM 3** promptbar concept-segmentering (metod `mask_compare`) är segmenterings-
motorn. Agenten anger `concepts` som engelska substantivfraser per körning.
Segmentern körs som en **native host-process** (MLX på Apple Silicon,
`mlx-community/sam3-image`) bakom ett HTTP-kontrakt (`SAM3_URL`); det kontraktet
är sömmen för att svappa in `facebook/sam3` på transformers/GPU för en Linux-
deploy. Resultat skrivs som vanliga workspace-lager (kandidat- + coverage-tabell).

## Konsekvenser

- (+) Agent-driven, ingen träning; resultaten är vanliga lager som går att
  `query`:a, kartlägga med svep och inspektera — screening-loopen är produkten.
- (+) Guardrails inbyggda: kandidater ej slutsatser, misregistrerings-suppression,
  säsongsflaggor, coverage-rader, och `dsm_diff` som den fysiskt robusta uppgraderingen.
- (−) SAM 3 ligger under en **custom Meta-licens** (SAM 1/2 var Apache-2.0, 3 är det
  inte) — **kräver juridisk granskning före kontraktsleverans** (jfr ADR 0010).
- (−) Approximativt instrument vid municipal GSD; aldrig som påstående mot medborgare.

## Status

Antagen, licensgranskning kvarstår. Se `geodata-mcp-architecture.md` §7 och
`CONTRACTS.md` (Worker `change_detect`, `analyze`).
