"""Inline HTML templates for the viewer pages (no template engine, no CDN)."""

_MAPLIBRE_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Map view</title>
<link rel="stylesheet" href="/static/maplibre-gl.css">
<style>
  html, body { height: 100%; margin: 0; }
  #map { position: absolute; inset: 0; }
  #titlebar {
    position: absolute; top: 10px; left: 10px; z-index: 10; display: none;
    background: rgba(255,255,255,.92); padding: 6px 12px; border-radius: 4px;
    font: 600 14px/1.4 system-ui, sans-serif; box-shadow: 0 1px 4px rgba(0,0,0,.25);
    max-width: 60%;
  }
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
</style>
</head>
<body>
<div id="map"></div>
<div id="titlebar"></div>
<div id="legend"></div>
<div id="error"></div>
<script src="/static/maplibre-gl.js"></script>
<script>
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
    head.textContent = "Legend";
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
    applyMetadata(md);
    setInterval(poll, POLL_MS);
  }

  function boot(attempt) {
    fetchStyle(false).then(start).catch(function (err) {
      var el = document.getElementById("error");
      if (attempt < 6) {
        el.textContent = "Loading map… (retrying: " + err.message + ")";
        el.style.display = "block";
        setTimeout(function () { boot(attempt + 1); }, 1500 * (attempt + 1));
      } else {
        el.textContent = "Failed to load map: " + err.message;
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

_ORIGO_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Origo map view</title>
<style>
  body { font: 15px/1.6 system-ui, sans-serif; max-width: 42rem; margin: 4rem auto; padding: 0 1rem; color: #222; }
  code, a { word-break: break-all; }
  h1 { font-size: 1.3rem; }
</style>
</head>
<body>
<h1>Origo renderer</h1>
<p>Origo support is config-level in v1: this service compiles the map view into an
Origo JSON configuration rather than serving an interactive Origo page.</p>
<p>Configuration for this view (projection EPSG:3014, GeoJSON sources served in
native CRS, WMS reference layers passed through):</p>
<p><a href="/v/__VIEW_ID__/origo.json"><code>/v/__VIEW_ID__/origo.json</code></a></p>
<p>Load it in an Origo deployment to render this view, or open the
<a href="/v/__VIEW_ID__">interactive MapLibre page</a> instead.</p>
</body>
</html>
"""


def maplibre_page(view_id):
    return _MAPLIBRE_PAGE.replace("__VIEW_ID__", view_id)


def origo_page(view_id):
    return _ORIGO_PAGE.replace("__VIEW_ID__", view_id)
