# Architecture Decision Records (ADR)

Den här katalogen samlar systemets **arkitekturbeslut** — ett kort dokument per
beslut som fångar *vad* som beslutades, *varför*, och vilka *konsekvenser* det för
med sig. Ett ADR är kortfattat (ca en halv sida) och skrivs inte om i efterhand:
ändras ett beslut skrivs ett nytt ADR som ersätter det gamla (status uppdateras
till `Ersatt av ADR NNNN`). Formatet följer [MADR](https://adr.github.io/madr/),
nedskuret till fyra rubriker som alla ADR:er i det här repot delar:

- **Kontext** — problemet och de krafter/begränsningar som tvingar fram ett beslut.
- **Beslut** — vad som beslutades, i konstaterande form.
- **Konsekvenser** — följderna, både fördelar (+) och kostnader/risker (−).
- **Status** — `Föreslagen` / `Antagen` / `Ersatt av ADR NNNN` / `Övergiven`.

Besluten konkretiserar `../../geodata-mcp-architecture.md` (målarkitektur) och
`../../CONTRACTS.md` (bindande implementationsspec); vid konflikt vinner CONTRACTS.

## Register

| ADR | Titel | Status |
|-----|-------|--------|
| [0001](0001-postgis-enda-tillstandslager.md) | PostgreSQL/PostGIS som enda tillståndslager | Antagen |
| [0002](0002-mcp-granssnitt-fastmcp.md) | MCP-gränssnitt via FastMCP över streamable HTTP | Antagen |
| [0003](0003-durable-workspaces-schema-per-workspace.md) | Durabla workspaces som schema-per-workspace | Antagen |
| [0004](0004-tva-renderare-via-compilers.md) | Två renderare (MapLibre + Origo) via renderer-compilers | Antagen |
| [0005](0005-tunn-dataendpoint-nu-ogc-senare.md) | Tunn dataendpoint nu, utbytbar mot Martin/TiPg/TiTiler | Antagen (OGC uppskjutet) |
| [0006](0006-oauth2-invite-code-legacy-bearer.md) | OAuth2 invite-code plus legacy bearer | Antagen |
| [0007](0007-sam3-ortofoto-forandringsdetektering.md) | SAM 3 för ortofoto-förändringsdetektering | Antagen (licensförbehåll) |
| [0008](0008-minio-s3-exportlagring.md) | MinIO/S3 för export- och blob-lagring | Antagen |
| [0009](0009-caddy-single-origin-proxy.md) | Caddy som single-origin reverse proxy | Antagen |
| [0010](0010-agpl-3-0-only-pmpc.md) | AGPL-3.0-only (Public Money, Public Code) | Antagen |
| [0011](0011-embeddinggemma-enda-embeddingmodell.md) | EmbeddingGemma som enda embedding-modell (v1) | Antagen |

## Lägga till ett nytt ADR

1. Kopiera [`0000-mall.md`](0000-mall.md) till `NNNN-kort-titel.md` med nästa
   lediga fyrsiffriga nummer (gemener, bindestreck i filnamnet).
2. Fyll i Kontext / Beslut / Konsekvenser / Status; håll det till en halv sida.
3. Lägg till en rad i registret ovan.
4. Upphäver beslutet ett tidigare ADR — sätt det gamla till `Ersatt av ADR NNNN`
   och låt dess innehåll stå kvar oförändrat (historiken är själva poängen).
