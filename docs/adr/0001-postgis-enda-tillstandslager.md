# ADR 0001: PostgreSQL/PostGIS som enda tillståndslager

- **Status:** Antagen
- **Datum:** 2026-08-03
- **Berör:** hela systemet (datalager, kö, sessioner, kartspecar, STAC)

## Kontext

Prototypen byggde på session-scoped DuckDB + Spatial och en statisk
`catalog.json`. Det gav ingen delad, durabel eller samtidig lagring, och
komponenterna var kopplade parvis till varandra (loader→verktygsschema,
verktyg→DuckDB-session, karta→renderingspipeline). Upphandlingen ställer PostGIS
som hårt krav. Den bärande arkitekturidén är att sluta koppla komponenterna till
varandra och istället koppla var och en till PostGIS: loaders skriver bara dit,
kartklienter läser bara därifrån, MCP-servern orkestrerar och sitter aldrig i
dataflödet.

## Beslut

Postgres 17 med PostGIS, pgvector, pg_trgm och unaccent är systemets **enda
tillståndsbärande tjänst** (utöver objektlagret för blobbar, ADR 0008). Där
läggs katalog, vektordata, workspaces, dokument, kartspecar, proveniens **och
jobbkön** (`app.jobs`) — ingen separat Redis/RabbitMQ. Jobbstatus blir därmed
transaktionell med den data jobbet producerar. All CRS lagras i EPSG:3014;
`ST_Transform` sker bara vid serveringskanten.

## Konsekvenser

- (+) Maximal enkelhet: en tjänst att säkerhetskopiera, driva och resonera om;
  en färre stateful komponent; jobb och data i samma transaktion.
- (+) Proveniens och revision blir databasegenskaper (event-triggers, pgAudit),
  inte löften i applikationslagret.
- (−) Postgres måste driftas väl — PgBouncer, statement timeouts och row caps är
  inte valfria. Skalning sker senare via read replicas / CloudNativePG-operatorn,
  inte genom att flytta state ut ur databasen.

## Status

Antagen. Konkretiseras i `CONTRACTS.md` (schemadel) och `geodata-mcp-architecture.md` §3.
