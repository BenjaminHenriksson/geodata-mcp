/* MapLibre traceability: render recorded evidence without interpreting it as a decision. */
(function (root) {
  "use strict";
  const copy = {
    sv: {
      title:"Källor & spårbarhet", map:"Kartans källor", feature:"Valt objekt", close:"Stäng",
      loading:"Hämtar registrerade uppgifter…", retry:"Försök igen", failed:"Uppgifterna kunde inte hämtas.",
      none:"Inga uppgifter registrerade.", sources:"Datakällor", processing:"Bearbetning", jobs:"Kopplade jobb",
      audit:"Relevant aktivitet", auditOwner:"Logga in som arbetsytans ägare för att se relevant aktivitet.",
      auditNote:"Visar registrerade lagerfrågor och kopplade jobb, inte en fullständig historik över alla händelser.",
      auditLink:"Öppna arbetsytans logg", limit:"Fler poster finns. Här visas de senaste.",
      catalogDate:"Katalog uppdaterad", dateNote:"Katalogens uppdateringstid är inte bildens fotograferingsdatum.",
      noSources:"Ingen katalogkälla är kopplad till kartlagren.", noProcessing:"Ingen bearbetningshistorik är registrerad för dessa lager.",
      select:"Klicka på ett objekt i kartan för att se dess registrerade underlag.",
      evaluation:"Analys & osäkerhet", evidence:"Synligt underlag", citations:"Dokument & sidreferenser",
      noCitations:"Ingen dokument- eller sidreferens är registrerad för objektet.",
      observations:"Källobservationer", conflicts:"Möjligt motstridiga observationer",
      review:"Granskning krävs: överlappande utsnitt har tolkats i olika riktningar. Båda tolkningarna visas; detta avgör inte vilken som är riktig. Rivning och nybyggnad kan också förekomma på samma plats.",
      missingDate:"Exakt fotograferingsdatum saknas för en eller båda årgångarna. Ett bildår eller WMS-lagernamn anger inte en verifierad fotograferingsdag.",
      properties:"Registrerade egenskaper", source:"Öppna källa",
      sourcePage:"Sida", confidenceNote:"Modellbedömning, inte en kalibrerad sannolikhet. Samstämmiga observationer höjer inte automatiskt tillförlitligheten.",
      bboxNote:"Granskningsruta, inte en uppmätt byggnadsgeometri. Arean avser rutan.",
      incomplete:"Analysen är ofullständig. Saknade eller misslyckade rutor betyder inte att ingen förändring har skett.",
      failedTile:"Denna ruta är inte färdiganalyserad.", refresh:"Uppdatera", details:"Registrerade detaljer",
      noFeatureKey:"Visar kartans objektuppgifter. Fullständiga detaljer kunde inte hämtas för detta objekt.",
      version:"Kartversion", layer:"Lager", elapsed:"Tid", status:"Status", before:"Före", after:"Efter",
      confidence:"Modellbedömning", change:"Förändring", area:"Area (m²)", vintage:"Bildår / årgång",
      acquisition:"Fotograferingsdatum", document:"Dokument", uncertainty:"Osäkerheter", count:"Observationer",
      relativeNote:"Bildreferenser och observationer är underlag för granskning, inte beslut eller bevis på tillstånd.",
    },
    en: {
      title:"Sources & traceability", map:"Map sources", feature:"Selected feature", close:"Close",
      loading:"Loading recorded information…", retry:"Try again", failed:"The information could not be loaded.",
      none:"No information recorded.", sources:"Data sources", processing:"Processing", jobs:"Linked jobs",
      audit:"Relevant activity", auditOwner:"Sign in as the workspace owner to see relevant activity.",
      auditNote:"Shows recorded layer queries and linked jobs, not a complete history of every event.",
      auditLink:"Open workspace log", limit:"More records exist. The latest are shown here.",
      catalogDate:"Catalogue updated", dateNote:"Catalogue update time is not the imagery acquisition date.",
      noSources:"No catalogue source is linked to these map layers.", noProcessing:"No processing history is recorded for these layers.",
      select:"Select a map feature to see its recorded evidence.",
      evaluation:"Analysis & uncertainty", evidence:"Visible evidence", citations:"Documents & page citations",
      noCitations:"No document or page citation is recorded for this feature.",
      observations:"Source observations", conflicts:"Potentially conflicting observations",
      review:"Review required: overlapping crops have been interpreted in opposing directions. Both interpretations remain visible; this does not decide which is correct. Demolition and new construction can also occur at the same location.",
      missingDate:"Exact acquisition date is missing for one or both vintages. An imagery year or WMS layer name is not a verified capture date.",
      properties:"Recorded properties", source:"Open source",
      sourcePage:"Page", confidenceNote:"Model judgement, not a calibrated probability. Agreement does not automatically increase confidence.",
      bboxNote:"Review bounding box, not a surveyed building footprint. Area refers to the box.",
      incomplete:"Analysis is incomplete. Missing or failed tiles do not mean that no change occurred.",
      failedTile:"This tile has not been successfully analyzed.", refresh:"Refresh", details:"Recorded details",
      noFeatureKey:"Showing rendered feature properties. Full details could not be retrieved for this feature.",
      version:"Map version", layer:"Layer", elapsed:"Duration", status:"Status", before:"Before", after:"After",
      confidence:"Model judgement", change:"Change", area:"Area (m²)", vintage:"Imagery year / vintage",
      acquisition:"Acquisition date", document:"Document", uncertainty:"Uncertainties", count:"Observations",
      relativeNote:"Image references and observations support review; they are not decisions or proof of permission.",
    }
  };
  const esc = value => String(value == null ? "" : value).replace(/[&<>"']/g,
    c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const publicQueryKeys = new Set(["id","documentid","document_id","docid","fileid","file_id","version","page","service","request","layers","typenames"]);
  function safeUrl(value) {
    try {
      const url = new URL(value);
      if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) return null;
      for (const key of [...url.searchParams.keys()])
        if (!publicQueryKeys.has(key.toLowerCase()) || url.searchParams.getAll(key).some(v => v.length > 500))
          url.searchParams.delete(key);
      if (!/^#page=\d+$/.test(url.hash)) url.hash = "";
      return url.href;
    } catch (_) { return null; }
  }
  function decode(value) {
    if (typeof value !== "string") return value;
    try { return JSON.parse(value); } catch (_) { return value; }
  }
  const featureFields = new Set([
    "fid","id","name","title","concept","change_class","change_type","confidence_label","confidence_a","confidence_b",
    "before_description","after_description","evidence","geometry_kind","area_m2","vintage_a","vintage_b","datetime_a","datetime_b",
    "source_tiles","observation_count","merge_method","observations","review_required","conflicting_observations","tile_id","status","gsd_m","source_url","document_url",
    "source_title","document_title","document_id","page","page_number","source_version","source_sha256","uncertainties",
    "warnings","warning","source_type","decision_id","permit_id","citation","citations","acquired_at","captured_at",
    "frame_id","site_id","camera_latitude","camera_longitude","camera_heading_deg","relative_yaw_deg","pitch_deg","hfov_deg"
  ]);
  function cleanFeature(properties) {
    const result = {};
    for (const [key, value] of Object.entries(properties || {})) {
      if (!featureFields.has(key)) continue;
      result[key] = ["source_url","document_url"].includes(key) ? safeUrl(value) : value;
    }
    return result;
  }
  const present = value => value !== undefined && value !== null && value !== "";
  const text = value => typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  function field(label, value) {
    return present(value) ? "<dt>" + esc(label) + "</dt><dd>" + esc(text(value)) + "</dd>" : "";
  }
  function detail(value, t) {
    if (!value || !Object.keys(value).length) return "";
    return "<details class=\"trace-detail\"><summary>" + esc(t.details) + "</summary><pre>" +
      esc(JSON.stringify(value, null, 2)) + "</pre></details>";
  }
  function link(url, label) {
    const safe = safeUrl(url);
    return safe ? '<a target="_blank" rel="noopener noreferrer" href="' + esc(safe) + '">' + esc(label) + " ↗</a>" : "";
  }
  function section(title, body) {
    return "<section class=\"trace-section\"><h3>" + esc(title) + "</h3>" + body + "</section>";
  }
  function note(message, warning) {
    return '<p class="' + (warning ? "trace-warning" : "trace-note") + '">' + esc(message) + "</p>";
  }
  function sourceList(data, t) {
    if (!data.sources.length) return note(t.noSources);
    return data.sources.map(source => "<article class=\"trace-record\"><strong>" + esc(source.title) +
      "</strong><p>" + esc(source.source_title) + "</p><dl>" + field(t.layer, source.ref) +
      field(t.catalogDate, source.catalog_updated_at) + field(t.acquisition, source.dates && (source.dates.datetime_min || source.dates.acquired_at)) + "</dl>" + link(source.url, t.source) +
      (source.attribution ? "<p class=\"trace-note\">" + esc(source.attribution) + "</p>" : "") +
      detail(source.dates, t) + "</article>").join("") + note(t.dateNote);
  }
  function renderMap(data, language) {
    const t = copy[language] || copy.sv;
    let body = '<p class="trace-lead">' + esc(data.title || "") + '</p><dl>' +
      field(t.version, data.version) + "</dl>";
    const incomplete = data.jobs.some(job => job.details && (job.details.tiles_skipped > 0 ||
      (job.details.model && job.details.model.complete === false))) ||
      data.processing.some(p => p.details && p.details.model && p.details.model.complete === false);
    if (incomplete) body += note(t.incomplete, true);
    body += section(t.sources, sourceList(data, t));
    body += section(t.processing, data.processing.length ? data.processing.map(p =>
      '<article class="trace-record"><strong>' + esc(p.kind) + '</strong><time>' + esc(p.at) +
      '</time><dl>' + field(t.layer, p.ref) + field("Job ID", p.job_id) +
      (p.inputs && p.inputs.length ? field("Input", p.inputs) : "") +
      field(t.vintage + " A", p.details && p.details.collections && p.details.collections.a) +
      field(t.vintage + " B", p.details && p.details.collections && p.details.collections.b) +
      field(language === "en" ? "Source types" : "Källtyper", p.details && p.details.source_kinds) +
      '</dl>' + detail(p.details, t) + "</article>"
    ).join("") : note(t.noProcessing));
    if (data.processing_more) body += note(t.limit);
    if (data.jobs.length) body += section(t.jobs, data.jobs.map(job =>
      '<article class="trace-record"><strong>Job ' + esc(job.id) + " · " + esc(job.kind) +
      '</strong><span class="trace-status ' + (job.status === "error" ? "trace-bad" : "") + '">' +
      esc(job.status) + '</span><dl>' + field("Start", job.started_at) +
      field(language === "en" ? "Finished" : "Avslutad", job.finished_at) +
      field(language === "en" ? "Attempts" : "Försök", job.attempts) + "</dl>" +
      ["a", "b"].map(side => {
        const vintage = job.details && job.details["vintage_" + side];
        if (!vintage || typeof vintage !== "object") return "";
        return "<dl>" + field(t.vintage + " " + side.toUpperCase(), vintage.collection) +
          field(t.acquisition, vintage.datetime_min || t.none) +
          field(language === "en" ? "Latest acquisition date" : "Senaste fotograferingsdatum", vintage.datetime_max) + "</dl>" +
          (!vintage.datetime_min ? note(t.missingDate) : "");
      }).join("") + detail(job.details, t) + "</article>").join(""));
    const audit = data.audit || {access:"owner_login_required", events:[]};
    let auditBody = note(t.auditNote);
    if (audit.access !== "owner") auditBody += note(t.auditOwner);
    else {
      auditBody += audit.events.length ? audit.events.map(event =>
        '<article class="trace-record"><strong>' + esc(event.action) + '</strong><time>' +
        esc(event.ts) + '</time><dl>' + field(t.status, event.status) +
        field(t.elapsed + " (ms)", event.duration_ms) + field("Job ID", event.job_id) +
        field("ID", event.id) + "</dl></article>").join("") : note(t.none);
      if (audit.more) auditBody += note(t.limit);
      // Server emits only an owner-authorized same-origin workspace link.
      if (/^\/workspaces\/[a-f0-9-]+#audit$/.test(audit.workspace_url || ""))
        auditBody += '<a href="' + esc(audit.workspace_url) + '">' + esc(t.auditLink) + "</a>";
    }
    return body + section(t.audit, auditBody);
  }
  function renderFeature(data, language) {
    const t = copy[language] || copy.sv, p = cleanFeature(data.properties);
    let body = '<p class="trace-lead">' + esc(p.title || p.name || data.layer || t.feature) + "</p><dl>" +
      field(t.layer, data.layer) + field("ID", p.fid ?? p.id) + "</dl>";
    if (data.fallback) body += note(t.noFeatureKey);
    if (p.review_required === true || p.review_required === "true") body += note(t.review, true);
    if (p.tile_id && p.status && p.status !== "analyzed") body += note(t.failedTile + " " + p.status, true);
    let evaluation = "<dl>" + field(t.change, p.change_type || p.change_class) +
      field(t.confidence, p.confidence_label) + field(t.count, p.observation_count) +
      field(t.area, p.area_m2) + field(t.vintage + " A", p.vintage_a) + field(t.vintage + " B", p.vintage_b) +
      field(t.acquisition + " A", p.datetime_a) + field(t.acquisition + " B", p.datetime_b) +
      field(t.acquisition, p.captured_at || p.acquired_at) + "</dl>";
    if ((present(p.vintage_a) && !present(p.datetime_a)) || (present(p.vintage_b) && !present(p.datetime_b)))
      evaluation += note(t.missingDate);
    if (p.frame_id) evaluation += "<dl>" + field("Frame ID", p.frame_id) +
      field(language === "en" ? "Camera GPS" : "Kamerans GPS", [p.camera_longitude, p.camera_latitude]) +
      field(language === "en" ? "Relative yaw (°)" : "Relativ vridning (°)", p.relative_yaw_deg) +
      field("Pitch (°)", p.pitch_deg) + field("HFOV (°)", p.hfov_deg) + "</dl>" +
      note(language === "en" ? "GPS locates the camera, not objects. Compass heading is uncalibrated; relative yaw is not a map bearing. Object ground positions require depth and heading calibration."
        : "GPS visar kamerans position, inte objektens. Kompassriktningen är okalibrerad; relativ vridning är inte en kartbäring. Objektens markposition kräver djupdata och kalibrerad riktning.", true);
    if (p.confidence_label || present(p.confidence_a) || present(p.confidence_b))
      evaluation += note(t.confidenceNote);
    if (p.geometry_kind === "bbox") evaluation += note(t.bboxNote);
    if (present(p.uncertainties) || present(p.warning) || present(p.warnings))
      evaluation += note(text(decode(p.uncertainties || p.warning || p.warnings)), true);
    body += section(t.evaluation, evaluation === "<dl></dl>" ? note(t.none) : evaluation);
    body += section(t.evidence, "<dl>" + field(t.before, p.before_description) +
      field(t.after, p.after_description) + field(t.evidence, p.evidence) + "</dl>" + note(t.relativeNote));
    const citations = [];
    if (p.source_url || p.document_url || p.document_id || present(p.page) || present(p.page_number))
      citations.push({url:p.document_url || p.source_url, title:p.document_title || p.source_title,
        document_id:p.document_id, page:p.page ?? p.page_number, version:p.source_version});
    const extra = decode(p.citations || p.citation);
    if (Array.isArray(extra)) citations.push(...extra.filter(v => v && typeof v === "object"));
    else if (extra && typeof extra === "object") citations.push(extra);
    const citationBody = citations.map(c => {
      let url = safeUrl(c.url || c.document_url || c.source_url);
      const page = c.page ?? c.page_number;
      if (url && /^\d+$/.test(String(page))) url = url.split("#")[0] + "#page=" + page;
      return '<article class="trace-record"><strong>' + esc(c.title || c.document_title || t.document) +
        "</strong><dl>" + field(t.sourcePage, page) + field("ID", c.document_id) +
        field(language === "en" ? "Source version" : "Källversion", c.version || c.source_version) +
        field(language === "en" ? "Quotation" : "Citat", c.quote) + "</dl>" + link(url, t.source) + "</article>";
    }).join("");
    body += section(t.citations, citationBody || note(t.noCitations));
    for (const [key, heading] of [["observations", t.observations], ["conflicting_observations", t.conflicts]]) {
      const observations = decode(p[key]);
      if (Array.isArray(observations) && observations.length) {
        body += section(heading, observations.filter(o => o && typeof o === "object").map(o => '<article class="trace-record"><strong>' +
          esc(o.tile_id) + '</strong><dl>' + field(t.change, o.change_type) + field(t.confidence, o.confidence_label) +
          field(t.before, o.before) + field(t.after, o.after) + field(t.evidence, o.evidence) +
          field(language === "en" ? "Clipped tile edges" : "Klippta rutkanter", o.clipped_edges) +
          field("SWEREF 99 TM · EPSG:3006", o.bounds_3006) + "</dl></article>").join(""));
      }
    }
    const attributes = Object.fromEntries(Object.entries(p).filter(([key]) =>
      !["observations", "conflicting_observations", "citations", "citation"].includes(key)));
    return body + section(t.properties, detail(attributes, t));
  }
  function attach(viewId) {
    const trigger = document.createElement("button");
    trigger.id = "trace-toggle"; trigger.type = "button"; trigger.className = "trace-toggle";
    trigger.setAttribute("aria-expanded", "false"); trigger.setAttribute("aria-controls", "trace-panel");
    const panel = document.createElement("aside");
    panel.id = "trace-panel"; panel.hidden = true; panel.setAttribute("aria-label", copy.sv.title);
    panel.innerHTML = '<div class="trace-header"><h2></h2><button data-language type="button">EN</button>' +
      '<button data-refresh type="button">↻</button><button data-close type="button">×</button></div><div class="trace-tabs" role="group">' +
      '<button data-tab="map" type="button"></button><button data-tab="feature" type="button"></button></div>' +
      '<div class="trace-content" tabindex="0" aria-live="polite"></div>';
    document.querySelector(".map-stage").append(trigger, panel);
    const content = panel.querySelector(".trace-content");
    let language = "sv", tab = "map", mapData = null, feature = null, featureSerial = 0, loading = false, failed = false;
    const t = () => copy[language];
    function render() {
      trigger.textContent = t().title;
      panel.querySelector("h2").textContent = t().title;
      panel.querySelector("[data-close]").setAttribute("aria-label", t().close);
      panel.querySelector("[data-refresh]").setAttribute("aria-label", t().refresh);
      panel.querySelector("[data-language]").textContent = language === "sv" ? "EN" : "SV";
      panel.querySelectorAll("[data-tab]").forEach(button => {
        button.textContent = t()[button.dataset.tab];
        button.setAttribute("aria-pressed", String(tab === button.dataset.tab));
      });
      if (tab === "feature") content.innerHTML = feature ? renderFeature(feature, language) : note(t().select);
      else if (loading) content.innerHTML = note(t().loading);
      else if (failed) {
        content.innerHTML = note(t().failed) + '<button data-retry type="button">' + esc(t().retry) + "</button>";
        content.querySelector("[data-retry]").onclick = load;
      } else content.innerHTML = mapData ? renderMap(mapData, language) : "";
    }
    function open(next) {
      tab = next || tab; panel.hidden = false; trigger.setAttribute("aria-expanded", "true"); render();
      if (tab === "map" && !loading) load();
    }
    function close() {
      panel.hidden = true; trigger.setAttribute("aria-expanded", "false"); trigger.focus();
    }
    async function load() {
      loading = true; failed = false; render();
      try {
        const response = await fetch("/v/" + viewId + "/traceability", {credentials:"same-origin", cache:"no-store"});
        if (!response.ok) throw new Error("unavailable");
        mapData = await response.json();
      } catch (_) { failed = true; }
      finally { loading = false; render(); }
    }
    trigger.onclick = () => panel.hidden ? open("map") : close();
    panel.querySelector("[data-close]").onclick = close;
    panel.querySelector("[data-refresh]").onclick = () => { tab = "map"; load(); };
    panel.querySelector("[data-language]").onclick = () => { language = language === "sv" ? "en" : "sv"; render(); };
    panel.querySelectorAll("[data-tab]").forEach(button => button.onclick = () => open(button.dataset.tab));
    panel.addEventListener("keydown", event => { if (event.key === "Escape") { event.stopPropagation(); close(); } });
    document.addEventListener("geodata:imagery-evidence", event => {
      const {site, frame, camera} = event.detail;
      ++featureSerial;
      feature = {layer:site.label, properties:{
        title:site.label, frame_id:frame.id, site_id:site.id, captured_at:frame.utc,
        camera_longitude:frame.longitude, camera_latitude:frame.latitude,
        camera_heading_deg:frame.camera_heading_deg,
        relative_yaw_deg:camera.yaw, pitch_deg:camera.pitch, hfov_deg:camera.hfov,
      }};
      open("feature");
    });
    render();
    return {
      invalidate() { mapData = null; if (!panel.hidden && tab === "map") load(); },
      async feature(layer, properties) {
        const serial = ++featureSerial;
        feature = {layer, properties:cleanFeature(properties), fallback:true}; open("feature");
        const key = present(properties.fid) ? "fid" : present(properties.id) ? "id" : null;
        if (!key) return;
        try {
          const query = new URLSearchParams({layer, key, identity:String(properties[key])});
          const response = await fetch("/v/" + viewId + "/feature-evidence?" + query,
            {credentials:"same-origin", cache:"no-store"});
          if (!response.ok) return;
          const data = await response.json();
          if (serial === featureSerial) { feature = data; render(); }
        } catch (_) { /* Rendered feature remains explicitly marked as fallback. */ }
      },
    };
  }
  const api = {attach, renderMap, renderFeature, safeUrl};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.GeodataTraceability = api;
})(typeof window !== "undefined" ? window : globalThis);
