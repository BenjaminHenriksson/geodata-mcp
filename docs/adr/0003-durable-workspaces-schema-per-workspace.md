# ADR 0003: Durabla workspaces som schema-per-workspace i Postgres

- **Status:** Antagen
- **Datum:** 2026-08-03
- **Berör:** datalager, `services/mcp` (`workspace`- och `layer`-verktygen)

## Kontext

Prototypens sessioner låg i minnet och försvann vid reconnect; agentens härledda
lager gick förlorade. Vi behöver isolering av per-session-arbete **och**
durabilitet. Två alternativ vägdes: row-level security (RLS) på delade tabeller,
eller ett eget schema per session. En felaktig RLS-policy läcker rader tyst; ett
schema gör ägarskapet synligt i varje tabells adress.

## Beslut

Ett **schema per durabelt workspace** (`ws_<8 hex>`, slumpad suffix), skapat
lazily av MCP-servern vid första skrivning, med `GRANT USAGE, CREATE` till
`agent_ws` och läsning till `agent_ro`. Delad referensdata ligger i `ref.*` och
läses av alla. Workspaces är **durabla** och raderas **explicit** (verktyg eller
manager-UI), aldrig via idle-TTL; en timvis sweep städar `ws_*`-scheman utan
ägande rad. Prototypens `checkpoint` pensioneras — durabilitet + `export` täcker
behovet.

## Konsekvenser

- (+) Isolering vilar på vanliga grants; städning är ett `DROP SCHEMA … CASCADE`;
  proveniens förblir läslig eftersom varje härlett objekt är namngivet.
- (−) Workspaces är **namnrymd och livscykel, inte tenancy**: all agent-SQL delar
  `agent_ro`, så varje autentiserad principal kan *läsa* alla workspaces; skrivning
  är workspace-scopad. Per-principal läsisolering kräver RLS eller per-user-roller
  (framtida tillägg).
- (−) Schema-churn vid hög samtidighet — mildras av explicit radering och sweep.

## Status

Antagen. Se `CONTRACTS.md` (ref/workspaces) och `geodata-mcp-architecture.md` §3.1.
