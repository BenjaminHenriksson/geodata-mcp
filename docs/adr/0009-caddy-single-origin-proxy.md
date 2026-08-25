# ADR 0009: Caddy som single-origin reverse proxy

- **Status:** Antagen
- **Datum:** 2026-08-03
- **Berör:** `deploy/Caddyfile`, hela deployment-topologin

## Kontext

Kartsidan, tile-endpointen och feature-endpointen måste dela **origin**, annars
blockerar browserns same-origin-policy klienten. Vi vill dessutom ha en enda
chokepoint där framtida autentisering och tile-cache kan läggas till utan att
röra tjänsterna bakom. Prototypen hade ingen sådan söm.

## Beslut

**Caddy** är systemets enda origin (host `http://localhost:8080` i compose).
Routing: `/mcp*` → `mcp:8000` (path bevarad), allt annat (`/v/*`, `/data/*`,
`/tiles/*`, `/static/*`, `/healthz`) → `viewer:8001`. MinIO:s presignerade URL:er
går **direkt** till host-porten (9000) och proxas inte, eftersom signaturen är
bunden till endpoint-värden. Kartvyer får långa, ogissbara ID:n — capability-URL:er.

## Konsekvenser

- (+) En origin: inga CORS-problem mellan sida, tiles och features.
- (+) En plats att senare lägga signerad/utgående token-auth och en tile-cache
  (CDN framför `/tiles/*`) — där lasten faktiskt koncentreras vid kartpanorering.
- (−) Capability-URL-åtkomst är medvetet minimal nu; riktig autentisering blir ett
  proxy-nivå-tillägg när en kund kräver det — en konfigändring, inte en omarkitektur.
- (−) Enda routing-punkt = enda felpunkt; hålls trivial och statslös.

## Status

Antagen. Se `CONTRACTS.md` (Topology / Caddy-routes) och `geodata-mcp-architecture.md` §5.2.
