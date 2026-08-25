"""Inline HTML templates for the viewer pages (no template engine, no CDN)."""

import html

_MAPLIBRE_PAGE = """<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kartvy</title>
<link rel="stylesheet" href="/static/maplibre-gl.css">
<style>
  html, body { height: 100%; margin: 0; }
  #map { position: absolute; inset: 0; }
  #titlebar {
    position: absolute; top: 10px; left: 50%; transform: translateX(-50%);
    z-index: 10; display: none;
    background: rgba(255,255,255,.92); padding: 6px 12px; border-radius: 4px;
    font: 600 14px/1.4 system-ui, sans-serif; box-shadow: 0 1px 4px rgba(0,0,0,.25);
    max-width: 60%; text-align: center;
  }
  #inspector {
    position: absolute; top: 10px; left: 10px; z-index: 11; display: none;
    background: rgba(255,255,255,.95); padding: 9px 11px; border-radius: 6px;
    font: 12px/1.4 system-ui, sans-serif; color: #222;
    box-shadow: 0 1px 6px rgba(0,0,0,.28); width: 210px;
  }
  #inspector .hdr { font-weight: 600; font-size: 12px; margin-bottom: 7px;
    letter-spacing: .02em; }
  #inspector .seg { display: flex; border: 1px solid #cdcdcd; border-radius: 5px;
    overflow: hidden; }
  #inspector .seg button { flex: 1 1 0; min-width: 0; border: 0; background: #fff;
    padding: 6px 4px; font: 600 11px system-ui, sans-serif; color: #333; cursor: pointer;
    border-right: 1px solid #e4e4e4; white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
  #inspector .seg button:last-child { border-right: 0; }
  #inspector .seg button.on { background: #1f78b4; color: #fff; }
  #inspector .seg button:not(.on):hover { background: #eef4f8; }
  #inspector .row { display: flex; align-items: center; gap: 7px; margin-top: 9px; }
  #inspector .row.disabled { opacity: .4; pointer-events: none; }
  #inspector .row label { color: #555; flex: none; }
  #inspector input[type=range] { flex: 1; accent-color: #1f78b4; }
  #inspector .chk { display: flex; align-items: center; gap: 7px; margin-top: 9px;
    cursor: pointer; }
  #inspector .chk .sw { width: 12px; height: 12px; border: 2px solid #333;
    background: rgba(31,120,180,.35); flex: none; box-sizing: border-box; }
  #inspector .tip { color: #888; font-size: 10.5px; margin-top: 8px; line-height: 1.35; }
  #inspector kbd { font: 10px ui-monospace, monospace; background: #f0f0f0;
    border: 1px solid #d0d0d0; border-radius: 3px; padding: 0 3px; }
  #legend {
    position: absolute; bottom: 24px; left: 10px; z-index: 10; display: none;
    background: rgba(255,255,255,.92); padding: 8px 12px; border-radius: 4px;
    font: 12px/1.5 system-ui, sans-serif; box-shadow: 0 1px 4px rgba(0,0,0,.25);
    max-height: 40%; overflow: auto;
  }
  .legend-title { font-weight: 600; margin-bottom: 4px; }
  .legend-row { display: flex; align-items: center; gap: 6px; margin: 2px 0; }
  .swatch { display: inline-block; width: 14px; height: 14px; flex: none; box-sizing: border-box; }
  .swatch-polygon { border: 2px solid #333; }
  .swatch-line { height: 3px; }
  .swatch-point { border-radius: 50%; border: 2px solid #fff; box-shadow: 0 0 0 1px rgba(0,0,0,.25); }
  .popup-table { border-collapse: collapse; font: 12px/1.4 system-ui, sans-serif; }
  .popup-table th, .popup-table td { border: 1px solid #ddd; padding: 2px 6px; text-align: left; vertical-align: top; }
  .popup-table th { background: #f5f5f5; font-weight: 600; }
  #error {
    position: absolute; top: 45%; left: 0; right: 0; z-index: 20; display: none;
    text-align: center; font: 14px/1.5 system-ui, sans-serif; color: #b00020;
  }
  #renderer-toggle {
    position: absolute; bottom: 24px; right: 10px; z-index: 10;
    background: rgba(255,255,255,.92); padding: 4px 10px; border-radius: 12px;
    font: 12px/1.5 system-ui, sans-serif; color: #333; text-decoration: none;
    box-shadow: 0 1px 4px rgba(0,0,0,.25);
  }
  #renderer-toggle:hover { background: #fff; }
  .skip-link {
    position: absolute; left: -9999px; top: 0; z-index: 30;
    background: #1f78b4; color: #fff; padding: 8px 14px; border-radius: 0 0 4px 0;
    font: 600 13px/1.4 system-ui, sans-serif; text-decoration: none;
  }
  .skip-link:focus { left: 0; }
</style>
</head>
<body>
<a class="skip-link" href="#map">Hoppa till innehåll</a>
<div id="map" role="main" aria-label="Interaktiv kartvy" tabindex="-1"></div>
<div id="titlebar" role="status" aria-live="polite"></div>
<div id="inspector" role="region" aria-label="Bildlager och förändringar"></div>
<div id="legend" role="region" aria-label="Teckenförklaring"></div>
<div id="error" role="alert"></div>
<a id="renderer-toggle" href="/v/__VIEW_ID__?renderer=origo" title="Rendera den här vyn med Origo (OpenLayers)" aria-label="Rendera den här vyn med Origo (OpenLayers)">⇄ Origo</a>
<script nonce="__NONCE__" src="/static/maplibre-gl.js"></script>
<script nonce="__NONCE__">
(function () {
  "use strict";
  var VIEW_ID = "__VIEW_ID__";
  var STYLE_URL = "/v/" + VIEW_ID + "/style.json";
  var POLL_MS = 4000;

  var map = null;
  var etag = null;
  var currentExtent = null;
  var popups = {};
  var fetching = false;
  var compareMeta = null;       // md.compare: before/after imagery + change layer ids
  var imageryMode = "map";      // "map" | "before" | "after"
  var imageryOpacity = 1;
  var changesOn = true;

  function escapeHtml(v) {
    return String(v)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function extentsEqual(a, b) {
    if (!a || !b) { return a === b; }
    if (a.length !== b.length) { return false; }
    for (var i = 0; i < a.length; i++) {
      if (Math.abs(a[i] - b[i]) > 1e-9) { return false; }
    }
    return true;
  }

  function fitExtent(extent) {
    if (!extent || !map) { return; }
    map.fitBounds([[extent[0], extent[1]], [extent[2], extent[3]]],
                  { padding: 40, duration: 0 });
  }

  function renderLegend(entries) {
    var el = document.getElementById("legend");
    if (!entries || !entries.length) { el.style.display = "none"; return; }
    el.innerHTML = "";
    var head = document.createElement("div");
    head.className = "legend-title";
    head.textContent = "Teckenförklaring";
    el.appendChild(head);
    entries.forEach(function (e) {
      var row = document.createElement("div");
      row.className = "legend-row";
      var sw = document.createElement("span");
      sw.className = "swatch swatch-" + (e.type || "polygon");
      if (e.type === "polygon") {
        sw.style.background = e.fill || "#ccc";
        sw.style.borderColor = e.stroke || "#333";
      } else if (e.type === "line") {
        sw.style.background = e.stroke || e.fill || "#333";
      } else {
        sw.style.background = e.fill || "#333";
      }
      row.appendChild(sw);
      var label = document.createElement("span");
      label.textContent = e.title || e.id || "";
      row.appendChild(label);
      el.appendChild(row);
    });
    el.style.display = "block";
  }

  // ---- imagery inspector (only present on change-detection maps) ----------
  function setImageryMode(mode) {
    imageryMode = mode;
    applyImagery();
    updateInspectorUI();
  }

  function applyImagery() {
    if (!map || !compareMeta) { return; }
    [["before", compareMeta.before], ["after", compareMeta.after]].forEach(function (p) {
      var key = p[0], info = p[1];
      if (!info || !map.getLayer(info.id)) { return; }
      var visible = (imageryMode === key);
      try {
        map.setLayoutProperty(info.id, "visibility", visible ? "visible" : "none");
        if (visible) { map.setPaintProperty(info.id, "raster-opacity", imageryOpacity); }
      } catch (e) { /* layer not ready yet; reapplied on next style load */ }
    });
  }

  function applyChanges() {
    if (!map || !compareMeta || !compareMeta.change_layer_ids) { return; }
    compareMeta.change_layer_ids.forEach(function (id) {
      if (!map.getLayer(id)) { return; }
      try { map.setLayoutProperty(id, "visibility", changesOn ? "visible" : "none"); }
      catch (e) { /* not ready */ }
    });
  }

  function applyInspectorState() { applyImagery(); applyChanges(); }

  function updateInspectorUI() {
    if (!compareMeta) { return; }
    var btns = compareMeta._segButtons || {};
    Object.keys(btns).forEach(function (k) { btns[k].classList.toggle("on", k === imageryMode); });
    if (compareMeta._opacityRow) {
      var active = (imageryMode === "before" || imageryMode === "after");
      compareMeta._opacityRow.classList.toggle("disabled", !active);
    }
  }

  function segButton(label, title, onClick) {
    var b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.title = title || label;
    b.addEventListener("click", onClick);
    return b;
  }

  function buildInspector(compare) {
    var el = document.getElementById("inspector");
    compareMeta = (compare && (compare.before || compare.after)) ? compare : null;
    if (!compareMeta) { el.style.display = "none"; return; }
    el.innerHTML = "";

    var hdr = document.createElement("div");
    hdr.className = "hdr";
    hdr.textContent = "Ortofoto";
    el.appendChild(hdr);

    var seg = document.createElement("div");
    seg.className = "seg";
    var segButtons = {};
    var mapBtn = segButton("Karta", "Bakgrundskarta", function () { setImageryMode("map"); });
    seg.appendChild(mapBtn); segButtons.map = mapBtn;
    ["before", "after"].forEach(function (side) {
      var info = compareMeta[side];
      if (!info) { return; }
      var short = info.year || info.label || side;
      var b = segButton(short, info.label || side, function () { setImageryMode(side); });
      seg.appendChild(b); segButtons[side] = b;
    });
    el.appendChild(seg);
    compareMeta._segButtons = segButtons;

    var row = document.createElement("div");
    row.className = "row";
    var lab = document.createElement("label");
    lab.textContent = "Opacitet";
    var rng = document.createElement("input");
    rng.type = "range"; rng.min = "20"; rng.max = "100"; rng.step = "5";
    rng.value = String(Math.round(imageryOpacity * 100));
    rng.setAttribute("aria-label", "Opacitet");
    rng.addEventListener("input", function () {
      imageryOpacity = (+rng.value) / 100; applyImagery();
    });
    row.appendChild(lab); row.appendChild(rng);
    el.appendChild(row);
    compareMeta._opacityRow = row;

    if (compareMeta.change_layer_ids && compareMeta.change_layer_ids.length) {
      var chk = document.createElement("label");
      chk.className = "chk";
      var cb = document.createElement("input");
      cb.type = "checkbox"; cb.checked = changesOn;
      cb.addEventListener("change", function () { changesOn = cb.checked; applyChanges(); });
      var sw = document.createElement("span"); sw.className = "sw";
      var t = document.createElement("span"); t.textContent = "Förändringskandidater";
      chk.appendChild(cb); chk.appendChild(sw); chk.appendChild(t);
      el.appendChild(chk);
    }

    if (compareMeta.before && compareMeta.after) {
      var tip = document.createElement("div");
      tip.className = "tip";
      tip.innerHTML = "Tryck på <kbd>Blanksteg</kbd> för att växla mellan före ↔ efter.";
      el.appendChild(tip);
    }

    el.style.display = "block";
    updateInspectorUI();
  }

  document.addEventListener("keydown", function (e) {
    if (e.code !== "Space" || !compareMeta || !compareMeta.before || !compareMeta.after) { return; }
    var tag = (e.target && e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") { return; }
    e.preventDefault();
    setImageryMode(imageryMode === "before" ? "after" : "before");
  });

  function applyMetadata(md) {
    md = md || {};
    popups = md.popups || {};
    var bar = document.getElementById("titlebar");
    if (md.title) {
      bar.textContent = md.title;
      bar.style.display = "block";
      document.title = md.title;
    } else {
      bar.style.display = "none";
    }
    renderLegend(md.legend || []);
    buildInspector(md.compare);
  }

  function popupLayerIds() {
    if (!map) { return []; }
    return Object.keys(popups).filter(function (id) { return map.getLayer(id); });
  }

  function onMouseMove(e) {
    var ids = popupLayerIds();
    if (!ids.length) { map.getCanvas().style.cursor = ""; return; }
    var feats = map.queryRenderedFeatures(e.point, { layers: ids });
    map.getCanvas().style.cursor = feats.length ? "pointer" : "";
  }

  function onMapClick(e) {
    var ids = popupLayerIds();
    if (!ids.length) { return; }
    var feats = map.queryRenderedFeatures(e.point, { layers: ids });
    if (!feats.length) { return; }
    var f = feats[0];
    var attrs = popups[f.layer.id] || [];
    var rows = "";
    attrs.forEach(function (a) {
      var v = (f.properties && Object.prototype.hasOwnProperty.call(f.properties, a))
        ? f.properties[a] : "";
      if (v === null || v === undefined) { v = ""; }
      rows += "<tr><th>" + escapeHtml(a) + "</th><td>" + escapeHtml(v) + "</td></tr>";
    });
    if (!rows) { return; }
    new maplibregl.Popup({ maxWidth: "340px" })
      .setLngLat(e.lngLat)
      .setHTML('<table class="popup-table">' + rows + "</table>")
      .addTo(map);
  }

  function fetchStyle(withEtag) {
    var headers = {};
    if (withEtag && etag) { headers["If-None-Match"] = etag; }
    return fetch(STYLE_URL, { headers: headers }).then(function (res) {
      if (res.status === 304) { return null; }
      if (!res.ok) { throw new Error("style fetch failed: " + res.status); }
      etag = res.headers.get("ETag") || etag;
      return res.json();
    });
  }

  function poll() {
    if (fetching || !map) { return; }
    fetching = true;
    fetchStyle(true).then(function (style) {
      if (style) {
        var md = style.metadata || {};
        map.setStyle(style, { diff: true });
        applyMetadata(md);
        applyInspectorState();  // diff:true reverts layout/paint to style defaults
        var newExtent = md.extent_4326 || null;
        if (newExtent && !extentsEqual(newExtent, currentExtent)) {
          currentExtent = newExtent;
          fitExtent(newExtent);
        }
      }
    }).catch(function () { /* transient; retry on next tick */ })
      .finally(function () { fetching = false; });
  }

  function start(style) {
    var md = style.metadata || {};
    currentExtent = md.extent_4326 || null;
    // preserveDrawingBuffer keeps the WebGL buffer readable so tab captures /
    // headless snapshots of the map work (tiny render cost, worth it here).
    map = new maplibregl.Map({ container: "map", style: style, preserveDrawingBuffer: true });
    window.__map = map;  // debugging handle (harmless; capability URL is the access control)
    map.on("error", function (e) {
      if (e && e.error) { console.warn("maplibre error:", e.error.message || e.error); }
    });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    // Fit immediately: camera ops don't need the style, and waiting for 'load'
    // stalls the initial view whenever any tile source is slow or failing.
    fitExtent(currentExtent);
    map.on("click", onMapClick);
    map.on("mousemove", onMouseMove);
    map.on("load", applyInspectorState);  // reapply selection once layers exist
    applyMetadata(md);
    setInterval(poll, POLL_MS);
  }

  function boot(attempt) {
    fetchStyle(false).then(start).catch(function (err) {
      var el = document.getElementById("error");
      if (attempt < 6) {
        el.textContent = "Laddar kartan… (försöker igen: " + err.message + ")";
        el.style.display = "block";
        setTimeout(function () { boot(attempt + 1); }, 1500 * (attempt + 1));
      } else {
        el.textContent = "Kunde inte ladda kartan: " + err.message;
        el.style.display = "block";
      }
    }).then(function () {
      if (map) { document.getElementById("error").style.display = "none"; }
    });
  }
  boot(0);
})();
</script>
</body>
</html>
"""

# Interactive Origo page. Constraints pinned by testing against Origo 2.10:
# - Origo mounts into <div id="app-wrapper"> by default.
# - It fetches 5 SVG icon sprites relative to the PAGE url unless svgSpritePath is
#   overridden — without the override every toolbar icon 404s silently.
# - A full https:// config URL is mangled by Origo's permalink parsing, so the config
#   is fetched here and handed over as an inline object (documented, avoids a refetch
#   and lets the page read our own `geodata` block).
# - Origo has no diff-apply equivalent to MapLibre's setStyle({diff:true}), so instead
#   of silently rebooting the viewer under the user, changes surface as a reload prompt.
_ORIGO_PAGE = """<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kartvy (Origo)</title>
<link rel="stylesheet" href="/static/origo/css/style.css">
<style>
  html, body { height: 100%; margin: 0; }
  #app-wrapper { position: absolute; inset: 0; }
  .chip {
    position: absolute; z-index: 10000; background: rgba(255,255,255,.94);
    padding: 4px 10px; border-radius: 12px; font: 12px/1.5 system-ui, sans-serif;
    color: #333; text-decoration: none; box-shadow: 0 1px 4px rgba(0,0,0,.25);
  }
  #renderer-toggle { bottom: 24px; right: 10px; }
  #renderer-toggle:hover { background: #fff; }
  #note { top: 10px; left: 10px; max-width: 30rem; display: none; color: #614700;
          background: rgba(255,247,214,.96); border: 1px solid #e0cd7a; }
  #note button { border: 0; background: none; cursor: pointer; color: #614700;
                 font: inherit; text-decoration: underline; padding: 0 0 0 6px; }
  #reload { top: 10px; right: 10px; display: none; cursor: pointer; border: 1px solid #1f78b4;
            color: #1f78b4; background: #fff; font: 12px/1.5 system-ui, sans-serif; }
  #error { position: absolute; top: 45%; left: 0; right: 0; z-index: 20000; display: none;
           text-align: center; font: 14px/1.5 system-ui, sans-serif; color: #b00020; }
  .skip-link {
    position: absolute; left: -9999px; top: 0; z-index: 30000;
    background: #1f78b4; color: #fff; padding: 8px 14px; border-radius: 0 0 4px 0;
    font: 600 13px/1.4 system-ui, sans-serif; text-decoration: none;
  }
  .skip-link:focus { left: 0; }
</style>
</head>
<body>
<a class="skip-link" href="#app-wrapper">Hoppa till innehåll</a>
<div id="app-wrapper" role="main" aria-label="Interaktiv kartvy" tabindex="-1"></div>
<div id="note" class="chip" role="status" aria-live="polite"><span id="note-text"></span><button type="button" id="note-x" aria-label="Stäng meddelande">stäng</button></div>
<button id="reload" class="chip" type="button">Kartan uppdaterad – ladda om</button>
<a id="renderer-toggle" class="chip" href="/v/__VIEW_ID__" title="Rendera den här vyn med MapLibre" aria-label="Rendera den här vyn med MapLibre">⇄ MapLibre</a>
<div id="error" role="alert"></div>
<script nonce="__NONCE__" src="/static/origo/js/origo.min.js"></script>
<script nonce="__NONCE__">
(function () {
  "use strict";
  var VIEW_ID = "__VIEW_ID__";
  var CONFIG_URL = "/v/" + VIEW_ID + "/origo.json";
  var POLL_MS = 4000;
  var revision = null;

  function showNote(text) {
    if (!text) { return; }
    document.getElementById("note-text").textContent = text;
    document.getElementById("note").style.display = "block";
  }

  document.getElementById("note-x").addEventListener("click", function () {
    document.getElementById("note").style.display = "none";
  });
  document.getElementById("reload").addEventListener("click", function () {
    window.location.reload();
  });

  function poll() {
    fetch(CONFIG_URL).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (cfg) {
        if (cfg && cfg.geodata && revision !== null && cfg.geodata.revision !== revision) {
          document.getElementById("reload").style.display = "block";
        }
      }).catch(function () { /* transient; try again next tick */ });
  }

  function boot(attempt) {
    fetch(CONFIG_URL).then(function (res) {
      if (!res.ok) { throw new Error("config fetch failed: " + res.status); }
      return res.json();
    }).then(function (cfg) {
      var meta = cfg.geodata || {};
      revision = meta.revision;
      if (meta.title) { document.title = meta.title; }
      // Inline object: Origo uses it directly instead of refetching.
      window.origo = Origo(cfg, { svgSpritePath: "/static/origo/css/svg/", baseUrl: "/" });
      showNote(meta.note);
      document.getElementById("error").style.display = "none";
      setInterval(poll, POLL_MS);
    }).catch(function (err) {
      var el = document.getElementById("error");
      el.style.display = "block";
      if (attempt < 5) {
        el.textContent = "Laddar kartan… (försöker igen: " + err.message + ")";
        setTimeout(function () { boot(attempt + 1); }, 1500 * (attempt + 1));
      } else {
        el.textContent = "Kunde inte ladda kartan: " + err.message;
      }
    });
  }
  boot(0);
})();
</script>
</body>
</html>
"""

_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Arbetsytehanterare – logga in</title>
<style>
  body { font: 15px/1.6 system-ui, sans-serif; max-width: 26rem; margin: 6rem auto; padding: 0 1rem; color: #222; }
  h1 { font-size: 1.25rem; }
  input[type=password] { width: 100%; padding: 8px; font: inherit; box-sizing: border-box;
    border: 1px solid #bbb; border-radius: 4px; }
  button { margin-top: 10px; padding: 8px 16px; font: inherit; border: 0; border-radius: 4px;
    background: #1f78b4; color: #fff; cursor: pointer; }
  button:hover { background: #17608f; }
  .error { color: #b00020; margin: 10px 0; }
  .hint { color: #666; font-size: 13px; }
  label { display: block; font-weight: 600; font-size: 13px; margin-bottom: 4px; }
  .skip-link {
    position: absolute; left: -9999px; top: 0;
    background: #1f78b4; color: #fff; padding: 8px 14px; border-radius: 0 0 4px 0;
    text-decoration: none;
  }
  .skip-link:focus { left: 0; }
</style>
</head>
<body>
<a class="skip-link" href="#huvudinnehall">Hoppa till innehåll</a>
<main id="huvudinnehall">
<h1>Arbetsytehanterare</h1>
<p class="hint">Logga in med en API-nyckel för geodata (samma nyckel som MCP-klienter
använder som <code>Authorization: Bearer …</code>). Endast en signerad
sessionscookie lagras.</p>
__ERROR__
<form method="post" action="/login">
  <label for="key">API-nyckel</label>
  <input type="password" id="key" name="key" placeholder="Din API-nyckel" autofocus autocomplete="current-password">
  <button type="submit">Logga in</button>
</form>
</main>
</body>
</html>
"""

_WORKSPACES_SHELL = """<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Arbetsytor</title>
<style>
  body { font: 15px/1.6 system-ui, sans-serif; max-width: 46rem; margin: 3rem auto; padding: 0 1rem; color: #222; }
  h1 { font-size: 1.25rem; display: flex; justify-content: space-between; align-items: baseline; }
  .error { color: #b00020; margin: 10px 0; }
  .ws { border: 1px solid #ddd; border-radius: 6px; padding: 12px 16px; margin: 12px 0; }
  .ws.active { border-color: #1f78b4; box-shadow: 0 0 0 1px #1f78b4 inset; }
  .ws h2 { font-size: 1.05rem; margin: 0 0 2px; display: flex; align-items: baseline; gap: 8px; }
  .badge { font-size: 11px; font-weight: 600; color: #fff; background: #1f78b4;
    border-radius: 10px; padding: 1px 8px; }
  .meta { color: #666; font-size: 13px; }
  .maps { margin: 6px 0 2px; padding-left: 18px; font-size: 14px; }
  .maps a { color: #1f78b4; }
  .actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; align-items: center; }
  .actions form { display: inline-flex; gap: 6px; align-items: center; margin: 0; }
  button { padding: 4px 12px; font: 13px system-ui, sans-serif; border: 1px solid #bbb;
    border-radius: 4px; background: #f7f7f7; cursor: pointer; }
  button:hover { background: #eee; }
  button.danger { border-color: #b00020; color: #b00020; }
  button.danger:hover { background: #fbe9ec; }
  input[type=text] { padding: 4px 8px; font: 13px system-ui, sans-serif;
    border: 1px solid #bbb; border-radius: 4px; width: 11rem; }
  details { display: inline-block; }
  details summary { cursor: pointer; font-size: 13px; color: #b00020; list-style: none;
    padding: 4px 12px; border: 1px solid #b00020; border-radius: 4px; }
  details[open] summary { background: #fbe9ec; }
  details .confirm { margin-top: 6px; }
  .logout button { border: 0; background: none; color: #666; text-decoration: underline;
    cursor: pointer; padding: 0; font-size: 13px; }
  .empty { color: #666; }
  .skip-link {
    position: absolute; left: -9999px; top: 0;
    background: #1f78b4; color: #fff; padding: 8px 14px; border-radius: 0 0 4px 0;
    text-decoration: none;
  }
  .skip-link:focus { left: 0; }
</style>
</head>
<body>
<a class="skip-link" href="#huvudinnehall">Hoppa till innehåll</a>
<header>
<h1>Arbetsytor
  <form class="logout" method="post" action="/logout"><input type="hidden" name="csrf" value="__CSRF__"><button type="submit">logga ut</button></form>
</h1>
</header>
<main id="huvudinnehall">
<p class="meta">Beständiga behållare för en API-nyckels lager och kartor. Den <b>aktiva</b>
arbetsytan är där anslutna MCP-agenter läser och skriver; att aktivera en annan får
effekt vid deras nästa verktygsanrop.</p>
__ERROR__
__ITEMS__
</main>
</body>
</html>
"""


def maplibre_page(view_id, nonce=""):
    return _MAPLIBRE_PAGE.replace("__VIEW_ID__", view_id).replace("__NONCE__", nonce)


def origo_page(view_id, nonce=""):
    return _ORIGO_PAGE.replace("__VIEW_ID__", view_id).replace("__NONCE__", nonce)


def login_page(error=None):
    err = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return _LOGIN_PAGE.replace("__ERROR__", err)


def _ws_item(w, csrf):
    e = html.escape
    wid = e(w["id"])
    maps = ""
    if w["maps"]:
        rows = "".join(
            f'<li><a href="/v/{e(m["view_id"])}">{e(m["title"])}</a>'
            f' <span class="meta">({e(m["updated_at"])};'
            f' <a href="/v/{e(m["view_id"])}?renderer=origo">origo</a>)</span></li>'
            for m in w["maps"])
        maps = f'<ul class="maps">{rows}</ul>'
    active_badge = '<span class="badge">aktiv</span>' if w["is_active"] else ""
    activate = "" if w["is_active"] else (
        f'<form method="post" action="/workspaces/action">'
        f'<input type="hidden" name="csrf" value="{csrf}">'
        f'<input type="hidden" name="workspace_id" value="{wid}">'
        f'<input type="hidden" name="action" value="activate">'
        f'<button type="submit">Aktivera</button></form>')
    layer_word = "lager" if w["layer_count"] == 1 else "lager"
    return f"""
<div class="ws{' active' if w['is_active'] else ''}">
  <h2>{e(w['name'])} {active_badge}</h2>
  <div class="meta">{w['layer_count']} {layer_word} · schema {e(w['ws_schema'])} ·
    skapad {e(w['created_at'])} · senast använd {e(w['last_used'])}</div>
  {maps}
  <div class="actions">
    {activate}
    <form method="post" action="/workspaces/action">
      <input type="hidden" name="csrf" value="{csrf}">
      <input type="hidden" name="workspace_id" value="{wid}">
      <input type="hidden" name="action" value="rename">
      <input type="text" name="new_name" placeholder="nytt namn" aria-label="Nytt namn för arbetsytan" pattern="[a-z0-9][a-z0-9_-]{{0,39}}" required>
      <button type="submit">Byt namn</button>
    </form>
    <details>
      <summary>Ta bort…</summary>
      <form class="confirm" method="post" action="/workspaces/action">
        <input type="hidden" name="csrf" value="{csrf}">
        <input type="hidden" name="workspace_id" value="{wid}">
        <input type="hidden" name="action" value="delete">
        <button type="submit" class="danger">Bekräfta borttagning av "{e(w['name'])}" och dess {w['layer_count']} {layer_word}</button>
      </form>
    </details>
  </div>
</div>"""


def workspaces_page(workspaces, csrf, error=None):
    err = f'<p class="error">{html.escape(error)}</p>' if error else ""
    if workspaces:
        items = "".join(_ws_item(w, html.escape(csrf)) for w in workspaces)
    else:
        items = ('<p class="empty">Inga arbetsytor ännu – de skapas när en MCP-agent '
                 'ansluter med den här API-nyckeln.</p>')
    return (_WORKSPACES_SHELL
            .replace("__CSRF__", html.escape(csrf))
            .replace("__ERROR__", err)
            .replace("__ITEMS__", items))
