# ADR 0002: MCP-gränssnitt via FastMCP över streamable HTTP

- **Status:** Antagen
- **Datum:** 2026-08-03
- **Berör:** `services/mcp`

## Kontext

Systemets styrplan är agentvänd: en LLM-klient (claude.ai, Claude Code, ChatGPT)
ska anropa verktyg. Model Context Protocol (MCP) är den standard klienterna
redan talar, och 2026 års MCP-roadmap sätter statslös streamable-HTTP-transport
överst. Prototypen hade 13 verktyg med överlappande ansvar. Vägledande regel:
agentens intelligens bor i klienten, verktygen är dumma, ortogonala förmågor.

## Beslut

MCP-servern implementeras med **FastMCP** och exponeras som **streamable HTTP**
på `/mcp`. Verktygsytan hålls minimal (åtta verktyg: `workspace`, `search`,
`load`, `analyze`, `query`, `layer`, `map`, `export`) med SQL som arbetshäst.
Servern håller **ingen applikationsstate** — allt tillstånd ligger i Postgres,
och principalen resolvas per request ur Starlette-requesten. (Transporten körs i
FastMCP:s stateful-läge eftersom MCP-sessionen etableras vid `initialize`; det är
transportsession, inte applikationsstate.) Varje verktyg returnerar en
JSON-serialiserbar dict och felar med `{"error": …}` istället för att kasta.

## Konsekvenser

- (+) En statslös serverprocess går att köra i flera kopior bakom en lastbalans
  utan sessionsklibb — skalningsdörren hålls öppen utan att byggas nu.
- (+) Liten verktygsyta = mindre att felkonfigurera; nya analysprocessorer växer
  i `analysis_ops.REGISTRY`, inte i schemat (`analyze` är konstant i storlek).
- (−) SQL som arbetshäst byter verktygsnivå-vägledning mot generalitet; de gamla
  verb-verktygen återkommer som exempel i docstrings, inte som API-yta.

## Status

Antagen. Se `CONTRACTS.md` (MCP-servern) och `geodata-mcp-architecture.md` §8.
