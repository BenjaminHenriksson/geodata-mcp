# ADR 0006: OAuth2 invite-code plus legacy bearer

- **Status:** Antagen
- **Datum:** 2026-08-18
- **Berör:** `services/mcp` (`oauth.py`, `sessions.py`)

## Kontext

Agentklienter (claude.ai, Claude Code, ChatGPT) ansluter helst till `/mcp` via
browser-login snarare än en manuellt klistrad bearer-nyckel. Samtidigt måste
script och CI kunna autentisera med en enkel, delad nyckel. Vi vill inte bygga en
fullständig identitetsleverantör för en pilot, men vi vill inte heller att den
enda vägen in är en långlivad hemlighet i klippbordet.

## Beslut

En minimal **OAuth 2.1 + PKCE authorization server** (`oauth.py`) med
**invite-code-login** (`GEODATA_INVITE_CODE`). Varje slutförd authorization mintar
en stabil `subject`; `sessions.py` provisionerar en `app.api_keys`-rad per subject,
så varje inloggning får sin egen isolerade uppsättning workspaces. Token-refresh
bevarar subject, så ett workspace överlever access-tokenens 7-dygns livslängd.
**Legacy delade bearer-nycklar** (`GEODATA_API_KEYS`) fortsätter fungera parallellt
för script/CI, validerade i `sessions.py`. Klienter och tokens persisteras i
Postgres; auth codes är in-memory (5 min TTL, engångs). `/mcp` kräver antingen en
giltig bearer eller ett OAuth-access-token, annars 401 + `WWW-Authenticate: Bearer`.

## Konsekvenser

- (+) Låg friktion för agentklienter, isolerade workspaces per inloggning, och
  scriptvägen förblir en enkel nyckel.
- (−) Identitet = delad invite-kod, inte per-person-autentisering; riktig
  OIDC/IdP är ett senare tillägg vid samma chokepoint.
- (−) In-memory auth codes binder authorization-flödet till en single-process-server.

## Status

Antagen. Se `services/mcp/oauth.py` och `db/migrations/004_oauth.sql`.
