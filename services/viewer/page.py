"""Inline HTML templates for the viewer pages (no template engine, no CDN)."""

import ui

_MAPLIBRE_PAGE = """<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Kartvy</title>
<link rel="stylesheet" href="/static/maplibre-gl.css">
<link rel="stylesheet" href="/static/imagery/style.css?v=20260922-navigation">
<link rel="stylesheet" href="/static/traceability.css">
<style>__UI_CSS__
__MAP_CSS__</style>
</head>
<body class="map-page">
__HEADER__
<div class="map-stage">
<a class="skip-link" href="#map">Hoppa till innehåll</a>
<div id="map" role="main" aria-label="Interaktiv kartvy" tabindex="-1"></div>
<div id="inspector" role="region" aria-label="Bildlager och förändringar" tabindex="-1"></div>
<div id="legend" role="region" aria-label="Teckenförklaring"></div>
<div id="error" role="alert"><span id="error-message"></span><button id="retry" type="button">Ladda om</button></div>
</div>
<script nonce="__NONCE__" src="/static/maplibre-gl.js"></script>
<script nonce="__NONCE__" src="/static/traceability.js?v=20260922-navigation"></script>
<script nonce="__NONCE__">
(function () {
  "use strict";
  document.getElementById("retry").addEventListener("click", function () { window.location.reload(); });
  var VIEW_ID = "__VIEW_ID__";
  var STYLE_URL = "/v/" + VIEW_ID + "/style.json";
  var POLL_MS = 4000;

  var map = null;
  var etag = null;
  var currentExtent = null;
  var popups = {};
  var featureLayers = {};
  var tracePanel = GeodataTraceability.attach(VIEW_ID);
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
    Object.keys(btns).forEach(function (k) {
      btns[k].classList.toggle("on", k === imageryMode);
      btns[k].setAttribute("aria-pressed", String(k === imageryMode));
    });
    if (compareMeta._opacityRow) {
      var active = (imageryMode === "before" || imageryMode === "after");
      compareMeta._opacityRow.classList.toggle("disabled", !active);
      compareMeta._opacityRow.querySelector("input").disabled = !active;
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


    el.style.display = "block";
    updateInspectorUI();
  }

  document.addEventListener("keydown", function (e) {
    if (e.code !== "Space" || !compareMeta || !compareMeta.before || !compareMeta.after) { return; }
    var tag = (e.target && e.target.tagName || "").toLowerCase();
    if (["input", "textarea", "select", "button", "a", "summary"].indexOf(tag) !== -1 ||
        (e.target && e.target.isContentEditable) || !e.target.closest(".map-stage")) { return; }
    e.preventDefault();
    setImageryMode(imageryMode === "before" ? "after" : "before");
  });

  function applyMetadata(md) {
    md = md || {};
    popups = md.popups || {};
    featureLayers = md.feature_layers || {};
    tracePanel.invalidate();
    var bar = document.getElementById("titlebar");
    if (md.title) {
      bar.textContent = md.title;

      document.title = md.title;
    } else {
      bar.textContent = "Karta";
    }
    renderLegend(md.legend || []);
    buildInspector(md.compare);
  }

  function popupLayerIds() {
    if (!map) { return []; }
    return Object.keys(Object.assign({}, popups, featureLayers)).filter(function (id) { return map.getLayer(id); });
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
    if (featureLayers[f.layer.id]) {
      tracePanel.feature(featureLayers[f.layer.id], f.properties || {});
    }
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
    map = new maplibregl.Map({ container: "map", style: style, preserveDrawingBuffer: true,
      locale: {"NavigationControl.ZoomIn": "Zooma in", "NavigationControl.ZoomOut": "Zooma ut",
               "NavigationControl.ResetBearing": "Återställ norriktning", "Popup.Close": "Stäng"} });
    window.__map = map;  // debugging handle (harmless; capability URL is the access control)
    import("/static/imagery/index.js?v=20260922-navigation").then(function (module) { return module.attachImagery(map, VIEW_ID); })
      .catch(function (error) { console.warn("Imagery catalogue:", error.message); });
    map.on("error", function (e) {
      if (e && e.error) { console.warn("maplibre error:", e.error.message || e.error); }
    });
    map.addControl(new maplibregl.NavigationControl(), "top-left");
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
        document.getElementById("error-message").textContent = "Laddar kartan…";
        el.style.display = "block";
        setTimeout(function () { boot(attempt + 1); }, 1500 * (attempt + 1));
      } else {
        document.getElementById("error-message").textContent = "Kartan kunde inte laddas.";
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
<style>__UI_CSS__
__MAP_CSS__</style>
</head>
<body class="map-page">
__HEADER__
<div class="map-stage">
<a class="skip-link" href="#app-wrapper">Hoppa till innehåll</a>
<div id="app-wrapper" role="main" aria-label="Interaktiv kartvy" tabindex="-1"></div>
<div id="load-status" role="status" aria-live="polite">Laddar kartan…</div>
<details id="note"><summary>Lagerinformation</summary><div><span id="note-text"></span><br><button type="button" id="note-x">Stäng</button></div></details>
<button id="reload" type="button">Visa uppdaterad karta</button>
<div id="error" role="alert"><span id="error-message"></span><button id="retry" type="button">Ladda om</button></div>
</div>
<script nonce="__NONCE__" src="/static/origo/js/origo.min.js"></script>
<script nonce="__NONCE__" src="/static/origo-data.js"></script>
<script nonce="__NONCE__">
(function () {
  "use strict";
  document.getElementById("retry").addEventListener("click", function () { window.location.reload(); });
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
      if (meta.title) { document.title = meta.title; document.getElementById("titlebar").textContent = meta.title; }
      // Origo's built-in loader requests a single capped response and hides
      // request errors. Give it empty sources, then load complete layers in pages.
      var vectors = cfg.layers.filter(function (layer) { return layer.type === "GEOJSON"; })
        .map(function (layer) { return Object.assign({}, layer); });
      cfg.layers.forEach(function (layer) { if (layer.type === "GEOJSON") { layer.source = "none"; } });
      // Inline object: Origo uses it directly instead of refetching.
      window.origo = Origo(cfg, { svgSpritePath: "/static/origo/css/svg/", baseUrl: "/" });
      window.origo.on("load", function () {
        var status = document.getElementById("load-status");
        GeodataOrigo.load(window.origo.api(), vectors, function (count, done) {
          status.textContent = done ? count.toLocaleString("sv-SE") + " objekt laddade"
            : "Laddar kartlager… " + count.toLocaleString("sv-SE") + " objekt";
        }).catch(function () {
          status.textContent = "Kartlagren kunde inte laddas klart. Kartan är ofullständig.";
          document.getElementById("error-message").textContent = "Alla kartobjekt kunde inte hämtas.";
          document.getElementById("error").style.display = "block";
        });
      });
      showNote(meta.note);
      document.getElementById("error").style.display = "none";
      setInterval(poll, POLL_MS);
    }).catch(function (err) {
      var el = document.getElementById("error");
      el.style.display = "block";
      if (attempt < 5) {
        document.getElementById("error-message").textContent = "Laddar kartan…";
        setTimeout(function () { boot(attempt + 1); }, 1500 * (attempt + 1));
      } else {
        document.getElementById("error-message").textContent = "Kartan kunde inte laddas.";
      }
    });
  }
  boot(0);
})();
</script>
</body>
</html>
"""


MAP_CSS = """
.map-stage #map,.map-stage #app-wrapper{position:absolute;inset:0;height:100%;width:100%}
#load-status{position:absolute;left:60px;top:12px;z-index:11;background:white;border:1px solid var(--line);border-radius:4px;padding:6px 10px;max-width:calc(100% - 96px);font:12px/1.5 var(--font);pointer-events:none}
.map-stage #inspector,.map-stage #legend,.map-stage #note,.map-stage #error{
box-sizing:border-box;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);color:var(--ink);font:13px/1.5 var(--font)}
#inspector{position:absolute;top:12px;right:12px;z-index:11;display:none;padding:12px;width:230px;max-width:calc(100% - 74px)}
#inspector .hdr,.legend-title{font-weight:650;font-size:13px;margin-bottom:8px}
#inspector .seg{display:flex;border:1px solid #afbdc7;border-radius:4px;overflow:hidden}
#inspector .seg button{flex:1;min-width:0;border:0;border-right:1px solid var(--line);background:white;padding:7px 4px;font:600 12px var(--font);color:var(--ink);cursor:pointer;overflow:hidden;text-overflow:ellipsis}
#inspector .seg button:last-child{border-right:0}#inspector .seg button.on{background:#e5f1f9;color:#145d8c}
#inspector .row{display:flex;align-items:center;gap:8px;margin-top:12px}
#inspector .row.disabled{opacity:.5}#inspector input[type=range]{min-width:0;flex:1;accent-color:var(--accent)}
#inspector .chk{display:flex;align-items:center;gap:7px;margin-top:12px}
#inspector input[type=checkbox]{accent-color:var(--accent)}
#inspector .sw{width:12px;height:12px;border:1px solid var(--accent);background:#dcecf7;flex:none}
#legend{position:absolute;bottom:32px;right:12px;z-index:10;display:none;padding:12px;max-width:calc(100% - 74px);max-height:30%;overflow:auto}
.legend-row{display:flex;align-items:center;gap:8px;margin:4px 0;overflow-wrap:anywhere}
.swatch{display:inline-block;width:14px;height:14px;flex:none;box-sizing:border-box}.swatch-polygon{border:2px solid #333}.swatch-line{height:3px}.swatch-point{border-radius:50%;border:1px solid white}
.popup-table{border-collapse:collapse;font:12px/1.5 var(--font);color:var(--ink)}
.popup-table th,.popup-table td{padding:5px 8px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top;overflow-wrap:anywhere}
.popup-table th{font-weight:600;background:var(--canvas)}
.map-stage .maplibregl-ctrl-group{border:1px solid var(--line);border-radius:4px;box-shadow:none}
.map-stage .maplibregl-ctrl-group button{width:34px;height:34px}
.map-stage .maplibregl-popup-content{border:1px solid var(--line);border-radius:var(--radius);box-shadow:0 2px 8px #17354a20}
.map-stage .maplibregl-popup-close-button{font:20px var(--font);padding:0 6px}
#note{position:absolute;top:12px;right:12px;max-width:min(360px,calc(100% - 24px));z-index:10000;display:none;padding:8px 12px}
#note summary{cursor:pointer;font-weight:600}#note>div{padding-top:8px}#note button{margin-top:8px}
#reload{position:absolute;top:12px;left:50%;transform:translateX(-50%);z-index:10000;display:none}
#reload,#note button,#retry{font:13px var(--font);border:1px solid #afbdc7;border-radius:4px;background:white;color:var(--ink);padding:7px 10px;cursor:pointer}
#error{position:absolute;top:40%;left:50%;transform:translateX(-50%);z-index:20000;display:none;padding:16px;max-width:calc(100% - 32px)}
#error-message{display:block;margin-bottom:10px}
.map-stage .o-ui{font-family:var(--font);color:var(--ink)}
#app-wrapper .o-navigation button{border-radius:4px;border:1px solid var(--line);box-shadow:none;color:var(--ink);width:34px;height:34px}
#app-wrapper .o-legend{border:1px solid var(--line);border-radius:var(--radius);box-shadow:none}
#app-wrapper .o-footer{background:var(--surface);color:var(--muted);border-top:1px solid var(--line);font:12px/1.5 var(--font)}
#app-wrapper .o-footer a{color:#156598}
"""


def _map_page(template, view_id, nonce, renderer, title, principal, csrf):
    return (template.replace("__VIEW_ID__", view_id).replace("__NONCE__", nonce)
            .replace("__UI_CSS__", ui.CSS).replace("__MAP_CSS__", MAP_CSS)
            .replace("__HEADER__", ui.map_header(view_id, renderer, title, principal, csrf)))


def maplibre_page(view_id, nonce="", title="", principal=None, csrf=""):
    return _map_page(_MAPLIBRE_PAGE, view_id, nonce, "maplibre", title, principal, csrf)


def origo_page(view_id, nonce="", title="", principal=None, csrf=""):
    return _map_page(_ORIGO_PAGE, view_id, nonce, "origo", title, principal, csrf)


def login_page(error=None):
    err = f'<p class="alert" role="alert">{ui.e(error)}</p>' if error else ""
    body = f"""<section class="panel login"><h1>Logga in</h1>{err}
<form method="post" action="/login"><label for="key">API-nyckel</label>
<input type="password" id="key" name="key" required autofocus autocomplete="current-password">
<button class="primary" type="submit">Logga in</button></form></section>"""
    return ui.document("Logga in", body, active="login")


def _ws_item(w, csrf):
    e = ui.e
    wid = e(w["id"])
    common = (f'<input type="hidden" name="csrf" value="{e(csrf)}">'
              f'<input type="hidden" name="workspace_id" value="{wid}">')
    active = '<span class="badge active" title="Används för nästa MCP-anrop">Aktiv</span>' if w["is_active"] else ""
    activate = "" if w["is_active"] else (
        f'<form method="post" action="/workspaces/action">{common}'
        '<input type="hidden" name="action" value="activate"><button type="submit">Aktivera</button></form>')
    return f"""<article class="workspace-row">
<div class="section-title"><h2><a href="/workspaces/{wid}">{e(w['name'])}</a> {active}</h2>
<span class="caption">{w['layer_count']} lager</span></div>
<div class="workspace-actions"><a href="/workspaces/{wid}#audit">Logg</a>{activate}
<details><summary>Byt namn</summary><form class="action-form" method="post" action="/workspaces/action">{common}
<input type="hidden" name="action" value="rename">
<div><label for="rename-{wid}">Nytt namn</label><input id="rename-{wid}" type="text" name="new_name"
value="{e(w['name'])}" pattern="[a-z0-9][a-z0-9_-]{{0,39}}" maxlength="40"
title="1–40 tecken: a–z, 0–9, bindestreck eller understreck" required></div>
<button type="submit">Spara namn</button></form></details>
<details><summary class="danger-summary">Ta bort</summary>
<p>Lager och kartor tas bort permanent.</p>
<form method="post" action="/workspaces/action">{common}<input type="hidden" name="action" value="delete">
<button type="submit" class="danger">Ta bort {e(w['name'])}</button></form></details>
</div></article>"""


def workspaces_page(workspaces, csrf, error=None, principal=None):
    err = f'<p class="alert" role="alert">{ui.e(error)}</p>' if error else ""
    items = "".join(_ws_item(w, csrf) for w in workspaces) if workspaces else (
        '<p class="empty">Inga arbetsytor. Anslut en MCP-klient för att skapa din första.</p>')
    body = '<div class="page-heading"><h1>Arbetsytor</h1></div>' + err
    body += f'<section class="panel">{items}</section>'
    return ui.document("Arbetsytor", body, principal, csrf, "workspaces")
