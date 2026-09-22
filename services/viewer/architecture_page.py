"""Authenticated, dependency-free guide to the system architecture.

This is an explanatory snapshot, not a service-health monitor. Keep the content
alongside the implementation; it contains no credentials or live inventory.
"""
import json

import ui
import architecture_copy as copy
import architecture_sv as sv

e = ui.e

# id, position, category, name, subtitle, description, facts, source
NODES = [
    ("browser", (16, 98), "client", "Your browser", "Chat, maps & downloads",
     "You ask questions in Eneo and open the maps and exports returned by the assistant. Other MCP clients can also use the geodata service.",
     [("Access", "The websites are reached through the deployment’s HTTPS gateway and configured network access."),
      ("Rendering", "MapLibre or Origo draws the map in the browser. MapLibre can fetch CARTO basemap tiles directly."),
      ("Identity", "An Eneo user account and a geodata API-key principal are separate identities.")], None),
    ("gateway", (252, 98), "service", "HTTPS gateway", "Reverse proxy · access",
     "A reverse proxy terminates HTTPS and routes requests to Eneo and Geodata. Hostnames and network restrictions are deployment settings. Network access does not replace application authentication.",
     [("Eneo", "The configured Eneo address routes to its frontend and backend API."),
      ("Geodata", "The configured Geodata address routes to Caddy; /exports/ routes to MinIO."),
      ("Network and TLS", "The deployment configures certificates and renewal. A private network or VPN, such as Tailscale, can restrict access independently of the server hardware.")], None),
    ("caddy", (478, 98), "service", "Geodata router", "Caddy · internal HTTP",
     "A small reverse proxy routes geodata traffic to the appropriate container and streams MCP responses without buffering.",
     [("/mcp", "Streamable HTTP to the MCP server on port 8000."),
      ("/oauth/…", "Authorization and discovery endpoints also go to MCP."),
      ("Other paths", "Dashboard, architecture, map and data endpoints go to the viewer on port 8001.")], "deploy/Caddyfile"),
    ("viewer", (704, 98), "service", "Maps & dashboard", "FastAPI · MapLibre / Origo",
     "The viewer reads saved map specifications and serves maps, layer data, workspace dashboards and this page. It does not ask a language model to draw the map.",
     [("Map data", "PostGIS → GeoJSON or vector tiles. Smaller MapLibre layers use GeoJSON; large layers use MVT."),
      ("Two renderers", "MapLibre uses Web Mercator. Origo uses EPSG:3014 with compatible raster backgrounds."),
      ("Access", "Dashboard pages require a signed login cookie. Map/data URLs use an unguessable view capability and check layer membership."),
      ("Imagery", "Catalogued WMS is fetched through a server-side proxy; change-detection maps get before/after image controls.")], "services/viewer/main.py"),
    ("basemap", (956, 98), "external", "CARTO basemap", "Browser tile requests",
     "CARTO provides the Positron street-map background used by MapLibre. This background is separate from the orthophotos used for change detection.",
     [("Network", "The browser requests basemap tiles from CARTO."),
      ("Coordinates", "Web Mercator tiles fit MapLibre. Origo needs a backdrop compatible with its EPSG:3014 projection.")], "services/viewer/compile_maplibre.py"),
    ("eneo", (252, 266), "service", "Eneo", "Chat UI & tool orchestration",
     "Eneo holds the conversation, calls the configured language model, executes its tool requests and feeds tool results back into the conversation. The Geodata assistant has geodata MCP and internet search attached.",
     [("Deployment", "Frontend, backend and background worker run as separate containers."),
      ("Model loop", "Eneo sends conversation and tool-result context to its chat model through its configured model connection."),
      ("Tool connection", "Eneo authenticates to geodata MCP using its configured server credential."),
      ("Outputs", "The answer links to real viewer maps and exported files returned by the tools.")], None),
    ("mcp", (478, 266), "service", "Geodata MCP", "8 tools · workspace scope",
     "The MCP server turns tool calls into catalog lookups, SQL, workspace operations or queued jobs. It validates arguments and access, and returns structured results to the assistant.",
     [("Identity", "Bearer API key or OAuth token resolves to a geodata principal."),
      ("SQL", "query uses a read-only role and a 15-second limit. layer writes derived tables in the selected owned workspace."),
      ("Long work", "load, analyze and export enqueue app.jobs in PostgreSQL and return a job ID when still running."),
      ("Search", "Hybrid trigram + vector catalog/document search. Query embeddings come from the local worker.")], "services/mcp/server.py"),
    ("ops", (704, 266), "service", "Service control", "Admin-only host bridge",
     "The administrator’s Services page talks to a narrowly scoped host controller. The architecture page itself only explains the system; it never starts or stops a service.",
     [("Connection", "A private Unix socket and server-side token connect the viewer to the controller."),
      ("Scope", "Only configured service actions are accepted; the viewer does not receive the Docker socket."),
      ("Audit", "Service operations have a maintenance history. Live health belongs on the Services page.")], "services/viewer/service_admin.py"),
    ("model", (956, 266), "model", "Model API", "Chat & image analysis",
     "Two independent callers use separately configurable model connections: Eneo for conversation and the geodata worker for change detection, PDF/image inspection and OCR. They do not share a conversation or context window.",
     [("Model", "The configured model processes text and images through a model API."),
      ("Chat path", "Eneo sends conversation and selected tool-result context."),
      ("Image path", "The worker sends a separate before/after crop pair per request, with detail=high and up to 16,384 output tokens."),
      ("Boundary", "Prompts and supplied images are sent to the configured model API. Actual visual token allocation depends on the model service.")], "services/worker/connectors/vision_change.py"),
    ("eneo-state", (252, 438), "data", "Eneo state", "PostgreSQL / pgvector + Redis",
     "Eneo has its own database and Redis instance, separate from the geodata database. They support Eneo’s application state and background tasks.",
     [("Database", "Users, assistants, conversations and Eneo configuration belong to this application store."),
      ("Redis", "Supports Eneo’s background processing. Geodata analysis jobs are not queued here."),
      ("Separation", "Geodata workspace geometries, provenance and map specs live in the other PostgreSQL instance.")], None),
    ("worker", (478, 438), "service", "Geodata worker", "GDAL · jobs · embeddings",
     "The worker claims a queued job from PostgreSQL, performs the work and records its result. It ingests datasets, processes documents, compares imagery and builds exports.",
     [("Queue", "The current worker implementation processes one queued geodata job at a time; queued work survives an MCP reconnect."),
      ("Image comparison default", "800-pixel crop pairs, 50% overlap and four concurrent model requests within a job. Results are approximate review boxes."),
      ("SAM3 option", "backend=\"sam3\" sends each vintage to the local segmenter, then compares its masks in PostGIS."),
      ("Documents", "inspect offers two modes: transcribe returns full page text; answer asks the vision model about the original page images. Both return page citations. PDF ingestion shares extraction and OCR, keeping indexed chunks within their source page."),
      ("Embeddings", "The configured local embedding model produces 256-dimensional vectors through /embed. Its weights are cached in a persistent volume.")], "services/worker/jobs.py"),
    ("database", (704, 438), "data", "Geodata database", "PostgreSQL · PostGIS · pgvector",
     "This is the durable centre of the geodata system: searchable metadata, shared source layers, owned workspace results, job state, map specifications and provenance.",
     [("Geometry", "Stored geodata uses EPSG:3014. Image processing windows use EPSG:3006 and are transformed back for output."),
      ("Schemas", "catalog = metadata; ref = shared data; ws_* = workspace tables; doc = document text; app = control and audit records."),
      ("Workspaces", "Ownership is tied to the geodata credential. One key may own multiple named workspaces and one active default."),
      ("Durability", "A persisted PostgreSQL volume holds the data independently of container recreation.")], "CONTRACTS.md"),
    ("sources", (956, 438), "external", "Geodata & imagery", "WFS · WMS · STAC · files",
     "Municipal and national services provide source data. Catalog registration describes a source; ingestion copies selected vector data or documents into the local system. WMS imagery can be requested on demand.",
     [("Protocols", "WFS, WMS/WMTS, OGC API Features, STAC, files, PDF and text are supported."),
      ("Orthophotos", "Change detection reads aligned windows from STAC/COG assets or WMS vintage layers, such as Sundsvall’s cascaded Lantmäteriet imagery."),
      ("Credentials", "Authenticated upstream credentials stay on the worker/viewer, rather than in model prompts or the source catalog.")], "services/worker/connectors/change_detect.py"),
    ("web-search", (252, 628), "service", "Internet search MCP", "Separate local tool server",
     "Eneo’s internet search is a separate MCP connection. It searches the public web; it is not geodata catalog search and does not drive an interactive browser.",
     [("Implementation", "A local FastMCP service exposes web_search through DDGS using configured search backends."),
      ("Output", "Search result text and source URLs go back to the assistant."),
      ("Boundary", "The search terms are sent to external search services.")], None),
    ("sam3", (478, 628), "model", "SAM3", "Optional segmentation",
     "SAM3 segments prompted objects locally. It remains an explicit alternative to the vision model for change detection; it is not needed for a vision-model job.",
     [("Change-detection adapter", "The configured segmentation endpoint returns masks for each vintage. The worker polygonizes and compares them."),
      ("Hardware", "SAM3 runs as a separate model service. Hardware requirements depend on its backend; the architecture does not assume a particular GPU model."),
      ("Difference", "SAM3 returns segmentation masks. The vision model returns approximate boxes with visible evidence and qualitative confidence.")], "services/segmenter/README.md"),
    ("exports", (704, 628), "data", "Export files", "MinIO · S3-compatible storage",
     "The worker writes GIS files and provenance sidecars to MinIO. The assistant returns download links; geometry is not reconstructed from the text of the chat.",
     [("Formats", "GeoPackage, GeoJSON, CSV and Parquet. GeoJSON is exported in EPSG:4326."),
      ("Access", "Downloads use signed links valid for 24 hours, served through the configured export address and its network access policy."),
      ("Refresh", "export(job_id=…) signs fresh links to the existing artifact without rerunning the analysis.")], "services/worker/exporter.py"),
    ("web", (956, 628), "external", "Public web", "Search engines & sources",
     "External search engines provide web results for the internet-search tool. These sources complement, rather than replace, the registered geodata catalog.",
     [("Use", "Find background information and source references."),
      ("Distinction", "Web search is not a browser automation service and does not execute actions on the websites it finds.")], None),
]

# Explicit paths keep the diagram legible and prevent a layout dependency.
# Connections describe logical request/data paths; replies use the same connection.
EDGES = [
    ("access", "browser", "gateway", "HTTPS through configured network access", "M196 136 H252"),
    ("chat", "gateway", "eneo", "Chat UI and API", "M342 174 V266"),
    ("route", "gateway", "caddy", "Geodata requests", "M432 136 H478"),
    ("tools-route", "caddy", "mcp", "MCP streaming HTTP", "M568 174 V266"),
    ("pages", "caddy", "viewer", "Pages and map data", "M658 136 H704"),
    ("tools", "eneo", "mcp", "Authenticated tool calls", "M432 304 H478"),
    ("chat-model", "eneo", "model", "Conversation and tool-result context", "M432 288 H452 V228 H936 V292 H956"),
    ("chat-state", "eneo", "eneo-state", "Application state and background tasks", "M342 342 V438"),
    ("internet-tool", "eneo", "web-search", "Separate MCP connection", "M252 320 H238 V666 H252"),
    ("search-web", "web-search", "web", "External web search", "M432 666 H450 V738 H940 V666 H956"),
    ("tool-data", "mcp", "database", "SQL, workspace state and job submission", "M658 304 H682 V476 H704"),
    ("embedding", "mcp", "worker", "Local query embeddings", "M568 342 V438"),
    ("jobs", "database", "worker", "Worker claims jobs; writes results back", "M704 476 H658"),
    ("image-model", "worker", "model", "Paired orthophoto crops", "M658 454 H670 V374 H942 V316 H956"),
    ("source-data", "worker", "sources", "Ingest data or read image windows", "M658 494 H676 V552 H940 V476 H956"),
    ("local-model", "worker", "sam3", "Local segmentation requests", "M568 514 V628"),
    ("export-files", "worker", "exports", "GIS files and provenance sidecars", "M588 514 V594 H794 V628"),
    ("map-data", "viewer", "database", "Read map specs and geodata", "M884 152 H902 V476 H884"),
    ("raster", "viewer", "sources", "Server-side WMS proxy", "M884 164 H920 V454 H956"),
    ("background", "viewer", "basemap", "MapLibre in the browser fetches tiles", "M884 136 H956"),
    ("control", "viewer", "ops", "Private Unix socket; admin actions only", "M794 174 V266"),
]

# Each step lights up its exact edge(s), while the complete flow stays visible.
FLOWS = {
    "query": ("Ask a geodata question", "From a question to a grounded answer.", [
        ("Open Eneo", "Your browser reaches the chat through the deployment’s HTTPS gateway.", ["access", "chat"]),
        ("Plan with the chat model", "Eneo sends conversation context to its chat model. The model can request tools.", ["chat-model"]),
        ("Call geodata tools", "Eneo executes the tool request using its geodata credential and the selected workspace.", ["tools"]),
        ("Find and query data", "Catalog search uses local embeddings when available. query executes read-only spatial SQL; layer writes derived workspace tables.", ["embedding", "tool-data"]),
        ("Explain the result", "Tool results return to Eneo and the model, which writes the answer. A map requires the map tool; text alone does not create one.", ["tools", "chat-model"]),
    ]),
    "vision": ("Detect changes · Vision model", "The default image-comparison path.", [
        ("Submit an analysis", "analyze creates a workspace-scoped change_detect job in app.jobs. Omitting backend selects the vision model.", ["tools", "tool-data"]),
        ("Claim the queued job", "The geodata worker takes one job from PostgreSQL. This queue is independent of Eneo’s Redis.", ["jobs"]),
        ("Read two vintages", "The worker requests aligned imagery windows, then prepares before/after pairs at 800 pixels with 50% overlap.", ["source-data"]),
        ("Compare crop pairs", "Up to four calls run concurrently inside the job. Each sends two images to the vision model, using a separate model context from Eneo chat.", ["image-model"]),
        ("Save review candidates", "Approximate boxes, evidence, qualitative confidence and coverage are written to the workspace. Errors are not labelled as unchanged areas.", ["jobs"]),
        ("Review and export", "Use map and export on the result table. Boxes may overlap and area_m2 measures the box, not the building footprint.", ["map-data", "export-files"]),
    ]),
    "sam3": ("Detect changes · SAM3", "An explicit local segmentation alternative.", [
        ("Select SAM3", "Set backend=\"sam3\" when submitting change_detect. The method becomes mask_compare.", ["tools", "tool-data"]),
        ("Read imagery", "The worker claims the job and reads both vintages into aligned 1008-pixel windows.", ["jobs", "source-data"]),
        ("Segment both dates", "Each vintage goes to the configured SAM3 segmentation service, using English object concepts.", ["local-model"]),
        ("Compare masks", "The worker polygonizes masks and uses PostGIS overlap calculations to classify appeared, disappeared and changed candidates.", ["jobs"]),
        ("Review the result", "The same workspace, coverage, map and export flow handles SAM3 results. Segmentation candidates still need visual review.", ["map-data", "export-files"]),
    ]),
    "map": ("Open a map or export", "Persisted data becomes a shareable artifact.", [
        ("Save a map specification", "map stores layer references, styles and the extent in app.map_views and returns a viewer URL.", ["tools", "tool-data"]),
        ("Open the viewer", "The browser follows the returned URL through the HTTPS gateway and Geodata router.", ["access", "route", "pages"]),
        ("Render real data", "The viewer reads PostGIS and serves GeoJSON or MVT. The browser draws features with MapLibre or Origo.", ["map-data"]),
        ("Add a background", "CARTO tiles are fetched by the MapLibre browser client. Registered WMS imagery is proxied server-side.", ["background", "raster"]),
        ("Download a GIS file", "export queues a worker job, which writes the chosen format and a provenance sidecar to MinIO. Signed links return through the tools and use the configured /exports/ route.", ["tool-data", "jobs", "export-files"]),
    ]),
    "internet": ("Search the internet", "A separate tool from catalog search.", [
        ("Request web search", "Eneo chooses its internet-search MCP tool, not the geodata search tool.", ["internet-tool"]),
        ("Search external sources", "The local search service sends the query to its DDGS search backends.", ["search-web"]),
        ("Return sources to chat", "Result text and URLs return to Eneo. The chat model can use those results in its response; no interactive browser is involved.", ["internet-tool", "chat-model"]),
    ]),
}

TOOLS = [("workspace", "Choose and manage owned workspaces"), ("search", "Find catalog entries and document passages"),
         ("load", "Register sources and ingest data"), ("query", "Read-only spatial SQL"),
         ("layer", "Create and style derived workspace layers"), ("map", "Save a map and return its URL"),
         ("analyze", "Inspect PDFs/images or queue the vision model/SAM3 change detection"), ("export", "Build or retrieve a GIS download")]


def content(lang):
    if lang == "en":
        return NODES, EDGES, FLOWS, TOOLS
    if lang != "sv":
        raise ValueError("unsupported architecture language")
    nodes = [(*node[:3], *sv.NODES[node[0]], node[-1]) for node in NODES]
    edges = [(key, start, end, sv.EDGES[key], path) for key, start, end, _, path in EDGES]
    flows = {key: (sv.FLOWS[key][0], sv.FLOWS[key][1], [
        (*translated, original[2])
        for translated, original in zip(sv.FLOWS[key][2], value[2], strict=True)
    ]) for key, value in FLOWS.items()}
    return nodes, edges, flows, [(key, sv.TOOLS[key]) for key, _ in TOOLS]


def render(principal, csrf, lang="sv"):
    content_nodes, content_edges, content_flows, content_tools = content(lang)
    def text(key):
        return copy.TEXT[key][lang == "en"]
    def t(key):
        return e(text(key))
    nodes, details = [], []
    for ident, (x, y), kind, name, subtitle, description, facts, source in content_nodes:
        nodes.append(f'<button class="arch-node kind-{kind}" id="node-{ident}" data-node="{ident}" '
                     f'style="left:{x}px;top:{y}px" aria-pressed="false" aria-controls="component-detail">'
                     f'<span class="node-mark" aria-hidden="true"></span><strong>{e(name)}</strong><span>{e(subtitle)}</span></button>')
        source_link = (f'<a class="source-link" href="https://github.com/BenjaminHenriksson/geodata-mcp/blob/main/{source}" '
                       f'target="_blank" rel="noopener noreferrer">{t("code_source")}: {e(source)}</a>' if source else '')
        kind_key = {"client": "client", "service": "host_service", "data": "data", "model": "model", "external": "external_service"}[kind]
        details.append(f'<article id="detail-{ident}" class="component-detail" hidden><span class="detail-kind">'
                       f'{t(kind_key)}</span><h2>{e(name)}</h2><p>{e(description)}</p><dl>' +
                       ''.join(f'<dt>{e(label)}</dt><dd>{e(value)}</dd>' for label, value in facts) +
                       f'</dl>{source_link}<h3>{t("connections")}</h3><ul class="connection-list" data-connections="{ident}"></ul></article>')
    paths = ''.join(f'<path id="edge-{ident}" data-from="{start}" data-to="{end}" data-label="{e(label)}" '
                    f'd="{path}" marker-end="url(#arrow)"><title>{e(label)}</title></path>'
                    for ident, start, end, label, path in content_edges)
    flow_buttons = ''.join(f'<button data-flow="{key}" aria-pressed="false">{e(value[0])}</button>' for key, value in content_flows.items())
    flow_data = {key: {"title": title, "description": desc, "steps": [
        {"title": title, "text": text, "edges": edges} for title, text, edges in steps]}
        for key, (title, desc, steps) in content_flows.items()}
    messages = {key: text(key) for key in ("caption", "related", "step", "of")}
    languages = ''.join(f'<a href="/architecture?lang={code}" lang="{code}" hreflang="{code}" data-language="{code}"'
                        + (' aria-current="page"' if code == lang else '') + f'>{name}</a>'
                        for code, name in (("sv", "Svenska"), ("en", "English")))
    storage = ''.join(f'<tr><th scope="row">{title}</th><td>{contents}</td><td>{lifetime}</td></tr>'
                      for title, contents, lifetime in copy.STORAGE[lang])
    boundaries = ''.join(f'<div><h2>{e(title)}</h2><p>{first}</p><p>{second}</p></div>'
                         for title, first, second in copy.BOUNDARIES[lang])
    body = f'''<link rel="stylesheet" href="/static/architecture/architecture.css?v=3">
<div class="architecture" lang="{lang}" data-flows="{e(json.dumps(flow_data))}" data-ui="{e(json.dumps(messages))}">
  <header class="arch-heading"><div><p class="arch-context">{t('context')}</p>
  <h1>{t('heading')}</h1><p>{t('intro')}</p></div>
  <div class="arch-actions"><nav class="arch-language" aria-label="{t('language')}">{languages}</nav>
  <a class="arch-jump" href="#architecture-notes">{t('notes')}</a></div></header>
  <div class="arch-explorer">
    <div class="flow-picker" role="group" aria-label="{t('trace')}">
      <button data-flow="overview" aria-pressed="true">{t('overview')}</button>{flow_buttons}
    </div>
    <div class="explorer-body">
      <div class="diagram-column">
        <div class="diagram-toolbar"><span id="diagram-caption">{t('caption')}</span>
          <div class="zoom-controls" role="group" aria-label="{t('zoom')}">
            <button id="zoom-out" aria-label="{t('zoom_out')}">−</button><output id="zoom-value" aria-live="polite">{t('fit')}</output>
            <button id="zoom-in" aria-label="{t('zoom_in')}">+</button><button id="zoom-fit">{t('fit')}</button>
          </div></div>
        <div class="diagram-viewport" tabindex="0" role="region" aria-label="{t('diagram')}">
          <div class="diagram-size"><div class="diagram-stage">
            <div class="arch-zone client-zone"><strong>{t('device')}</strong></div>
            <div class="arch-zone workstation-zone"><strong>{t('deployment')}</strong><span>{t('network')}</span></div>
            <div class="arch-zone external-zone"><strong>{t('external')}</strong><span>{t('outbound')}</span></div>
            <svg class="diagram-edges" viewBox="0 0 1180 780" aria-hidden="true">
              <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10z"/></marker></defs>
              <g>{paths}</g></svg>{''.join(nodes)}
            <p class="diagram-note">{t('arrows')}</p>
          </div></div>
        </div>
        <div class="diagram-legend"><span><i class="legend-service"></i>{t('service')}</span><span><i class="legend-data"></i>{t('data')}</span>
          <span><i class="legend-model"></i>{t('model')}</span><span><i class="legend-external"></i>{t('source')}</span>
          <button id="clear-selection">{t('clear')}</button></div>
        <div id="flow-walkthrough" hidden><div class="walk-heading"><h2 id="flow-title"></h2><span id="step-count" aria-live="polite"></span></div>
          <div id="flow-steps" role="group" aria-label="{t('flow_steps')}"></div>
          <div class="walk-copy" aria-live="polite"><h3 id="step-title"></h3><p id="step-text"></p></div>
          <div class="walk-controls"><button id="previous-step">{t('previous')}</button><button id="next-step">{t('next')}</button></div>
        </div>
      </div>
      <aside id="component-detail" aria-label="{t('component')}" aria-live="polite">
        <button id="back-to-diagram">{t('back')}</button>
        <div id="detail-intro"><span class="detail-kind">{t('guide')}</span><h2>{t('one')}<br>{t('several')}</h2>
          <p>{t('select')}</p>
          <div class="intro-note"><h3>{t('default')}</h3><p>{t('default_detail')}</p></div>
          <div class="intro-note"><h3>{t('persist')}</h3><p>{t('persist_detail')}</p></div>
          <p class="snapshot-note">{t('reviewed')}</p>
        </div>{''.join(details)}
      </aside>
    </div>
  </div>
  <noscript><p>{t('noscript')}</p><style>.component-reference{{display:block!important}}</style></noscript>
  <section class="architecture-notes" id="architecture-notes"><div class="notes-heading"><h2>{t('stored')}</h2><p>{t('context_note')}</p></div>
    <div class="arch-table-wrap"><table><thead><tr><th scope="col">{t('store')}</th><th scope="col">{t('contents')}</th><th scope="col">{t('lifetime')}</th></tr></thead><tbody>{storage}</tbody></table></div>
  </section>
  <section class="tool-reference"><h2>{t('eight_tools')}</h2><dl>{''.join(f'<div><dt><code>{name}</code></dt><dd>{e(text)}</dd></div>' for name, text in content_tools)}</dl></section>
  <section class="boundary-notes">{boundaries}</section>
  <details class="component-reference"><summary>{t('reference')}</summary>{''.join(f'<section><h3>{e(n[3])}</h3><p>{e(n[5])}</p><dl>'+''.join(f'<dt>{e(k)}</dt><dd>{e(v)}</dd>' for k,v in n[6])+'</dl></section>' for n in content_nodes)}</details>
  <footer class="arch-footer"><span>{t('documented')}</span><a href="https://github.com/BenjaminHenriksson/geodata-mcp" target="_blank" rel="noopener noreferrer">{t('repository')}</a><a href="/docs">{t('api')}</a></footer>
</div><script src="/static/architecture/architecture.js?v=3" defer></script>'''
    return ui.document(text("title"), body, principal, csrf, "architecture", lang=lang)
