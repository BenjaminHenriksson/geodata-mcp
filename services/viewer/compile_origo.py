"""Compile an app.map_views spec into a real Origo (v2.10) JSON configuration.

Grounded in the Origo 2.10 docs and karta.sundsvall.se's own production config
(EPSG:3014): projection block with proj4Defs + metre resolutions, named styles
(always array-of-arrays), GEOJSON layers served in the native CRS with an explicit
per-layer projection, WMS layers via named source blocks, legend membership via
group 'root' (vectors) / 'background' (basemaps). Served at /v/<id>/origo.json and
consumed by the interactive Origo page (page.origo_page).
"""
import html
import os

import dbq
import netauth
from compile_maplibre import (CODE_VERSION, DEFAULT_CIRCLE_RADIUS,
                              DEFAULT_FILL_OPACITY, DEFAULT_LINE_WIDTH,
                              DEFAULT_PALETTE, DEFAULT_POLYGON_OUTLINE_WIDTH,
                              DEFAULT_POLYGON_STROKE, _num, resolve_popup_attrs)

PROJ4_3014 = ("+proj=tmerc +lat_0=0 +lon_0=17.25 +k=1 +x_0=150000 +y_0=0 "
              "+ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs")
PROJ4_3006 = "+proj=utm +zone=33 +ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs"

# Origo forwards `extent` straight into the OpenLayers View, where it becomes a hard
# pan constraint — a municipal box would make anything outside it unreachable, and our
# data is not guaranteed to sit inside Sundsvall (RIGES layers are regional, and agents
# can ingest anything). So this is the generous projection-validity box that Origo's own
# docs and Enköping's production config use, not a data extent.
PROJECTION_EXTENT_3014 = [-1678505.18, 4665380.0, 2431912.74, 8775797.92]
# Metres-per-pixel ladder from Sundsvall's production config (their cache-alignment tail
# levels 0.112/0.056 omitted — we serve no cached WMS).
RESOLUTIONS = [560, 280, 140, 70, 28, 14, 7, 4.2, 2.8, 1.4, 0.56, 0.28, 0.14]


def _text(value):
    """Escape text that Origo will inject as raw HTML.

    Origo builds its legend with `createContextualFragment` and its feature-info window
    with `innerHTML`, neither of which escapes. Layer labels and popup attribute names
    are agent-supplied, so they are escaped here; the page additionally carries a CSP
    that blocks inline script/handlers (see main.py) for the values we cannot reach —
    feature *values* are substituted client-side, straight out of the GeoJSON.
    """
    return html.escape(str(value), quote=True)

# Fallback: roughly the Sundsvall region in EPSG:3014, used only when a view has
# neither an explicit extent nor any vector layer with rows.
DEFAULT_EXTENT_3014 = [100000.0, 6850000.0, 210000.0, 6990000.0]

# Assumed viewport when picking the initial zoom level for a view extent.
VIEWPORT_PX = (1200.0, 800.0)

# Origo's backdrop: the grey Lantmäteriet layer that karta.sundsvall.se itself
# uses as its default background, cascaded via the municipal GeoServer. The Origo
# renderer ALWAYS uses this backdrop (see the backdrop block in compile_origo);
# resolved from the catalog at compile time so a missing harvest degrades to a
# note, not an error.
WMS_BACKDROP = os.environ.get("ORIGO_WMS_BACKDROP",
                              "Lantmateriet:topowebbkartan_nedtonad")


def _rgba(hex_color, alpha):
    """'#rrggbb' (or '#rgb') + alpha -> 'rgba(r,g,b,a)'; passthrough when unparsable."""
    if not isinstance(hex_color, str):
        return hex_color
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return hex_color
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return hex_color
    return f"rgba({r},{g},{b},{alpha})"


def _zoom_for_extent(extent):
    """Finest resolution index at which the extent still fits the assumed viewport."""
    w = max(extent[2] - extent[0], 1.0)
    h = max(extent[3] - extent[1], 1.0)
    zoom = 0
    for i, res in enumerate(RESOLUTIONS):
        if w / res <= VIEWPORT_PX[0] and h / res <= VIEWPORT_PX[1]:
            zoom = i
        else:
            break  # resolutions shrink monotonically; once it overflows it stays overflowing
    return zoom


def _origo_style(gclass, eff, base_color, label):
    """One named Origo style: [[rule]] — outer level = variants, inner = stacked parts."""
    fill = eff.get("fill") or base_color
    opacity = _num(eff.get("opacity"), DEFAULT_FILL_OPACITY)
    width = eff.get("width")
    if gclass == "polygon":
        stroke = eff.get("stroke") or DEFAULT_POLYGON_STROKE
        rule = {"fill": {"color": _rgba(fill, opacity)},
                "stroke": {"color": stroke,
                           "width": _num(width, DEFAULT_POLYGON_OUTLINE_WIDTH)}}
    elif gclass == "line":
        color = eff.get("stroke") or eff.get("fill") or base_color
        rule = {"stroke": {"color": color, "width": _num(width, DEFAULT_LINE_WIDTH)}}
    else:
        rule = {"circle": {"radius": _num(eff.get("circle"), DEFAULT_CIRCLE_RADIUS),
                           "fill": {"color": fill},
                           "stroke": {"color": "#ffffff", "width": 1}}}
    rule["label"] = label
    return [[rule]]


def compile_origo(conn, view):
    """Return the Origo config dict for a map_views row (view_id/title/spec/version)."""
    spec = view["spec"] or {}
    view_id = view["view_id"]

    view_extent = dbq.view_extent_3014(conn, spec) or list(DEFAULT_EXTENT_3014)
    center = [(view_extent[0] + view_extent[2]) / 2.0,
              (view_extent[1] + view_extent[3]) / 2.0]

    sources = {}
    styles = {}
    # Origo draws the layers array top-down: entry 0 is the TOPMOST layer and the
    # last entries are the backdrop (Sundsvall's own config lists its five
    # background layers last). Overlays are collected separately from the basemap
    # so the opaque backdrop cannot end up painted over the data.
    layers_out = []
    basemap_layers = []

    def add_wms_layer(ds, title, visible, group, source_url=None):
        source_url = source_url or ds["url"]
        src_key = "wms_" + str(len(sources))
        # Reuse a source block when several layers share the endpoint.
        for k, v in sources.items():
            if v["url"] == source_url:
                src_key = k
                break
        else:
            # WMS 1.1.1 like Sundsvall's own configs: axis order stays x,y in
            # projected CRS, sidestepping the 1.3.0 north-first flip.
            sources[src_key] = {"url": source_url, "version": "1.1.1"}
        layer = {"name": ds["external_id"], "title": _text(title), "type": "WMS",
                 "source": src_key, "format": "image/png",
                 "visible": visible, "group": group}
        if ds.get("attribution"):
            layer["attribution"] = _text(ds["attribution"])
        if group == "background":
            # The legend's background switcher reads style[0][0].image for its
            # thumbnail and throws if the layer has no style, taking the whole
            # legend control down with it. Origo ships these thumbnails.
            haystack = f"{ds.get('external_id') or ''} {title or ''}".lower()
            thumb = "orto.png" if ("orto" in haystack or "flygbild" in haystack) else "gra.png"
            style_name = "background_thumb"
            styles[style_name] = [[{"image": {"src": f"/static/origo/img/png/{thumb}"}}]]
            layer["style"] = style_name
            basemap_layers.append(layer)
        else:
            layers_out.append(layer)

    # Backdrop. Origo forces every tile source to the map's own projection
    # (`source.projection = getProjectionCode()` in its layer factory), so
    # Web-Mercator XYZ tiles (Positron/OSM) would request 3857 addresses against
    # a SWEREF99 grid and silently render nothing. The Origo renderer therefore
    # ALWAYS uses the official municipal WMS backdrop (WMS_BACKDROP): the server
    # reprojects to EPSG:3014, which is why Sundsvall's own Origo cascades all
    # rasters as WMS. spec['basemap'] is not consulted — the backdrop is
    # per-renderer, not per-view: MapLibre always shows Carto Positron instead
    # (see compile_maplibre).
    note = None
    ds = dbq.wms_dataset_by_external_id(conn, WMS_BACKDROP)
    if ds is not None and ds["url"]:
        add_wms_layer(ds, ds["title"], True, "background")
    else:
        note = (f"No backdrop: the official WMS backdrop layer ({WMS_BACKDROP}) is not "
                f"in the catalog; harvest it or point ORIGO_WMS_BACKDROP at another layer.")

    vec_index = 0
    wmts_skipped = []
    for entry in spec.get("layers") or []:
        if not isinstance(entry, dict):
            continue
        ref = entry.get("ref")
        if not isinstance(ref, str):
            continue

        if ref.startswith("wms:"):
            dataset_id = ref[4:]
            ds = dbq.wms_dataset(conn, dataset_id)
            if ds is None or not ds["url"]:
                continue
            if ds.get("source_kind") == "wmts":
                # Origo builds WMTS tile grids from the map's own resolution ladder
                # by index, and municipal caches (Sundsvall's GWC included) use a
                # different ladder — the tiles cannot align here. The layer still
                # renders in the MapLibre view; its WMS twin renders in both.
                wmts_skipped.append(entry.get("label") or ds["title"])
                continue
            source_url = None
            if netauth.userpwd_for(ds["url"]) is not None:
                # The browser cannot hold the upstream credential; point the source
                # at the /wmsref proxy instead (OpenLayers appends its GetMap KVP
                # to the existing query string).
                source_url = f"/wmsref/{dataset_id}?view={view_id}"
            add_wms_layer(ds, entry.get("label") or ds["title"],
                          entry.get("visible") is not False, "root", source_url)
            continue

        parsed = dbq.split_layer_ref(ref)
        if parsed is None:
            continue
        schema, table = parsed
        cols = dbq.columns(conn, schema, table)
        if cols is None or not dbq.has_geom(cols):
            continue

        meta = dbq.layer_meta(conn, schema, table)
        eff = dict(meta["style"])
        entry_style = entry.get("style")
        if isinstance(entry_style, dict):
            eff.update({k: v for k, v in entry_style.items() if v is not None})
        visible = entry["visible"] if isinstance(entry.get("visible"), bool) else meta["visible"]
        label = entry.get("label") or meta["label"] or table
        popup_attrs = resolve_popup_attrs(entry, meta, cols)
        gclass = dbq.geometry_class(conn, schema, table)
        base_color = DEFAULT_PALETTE[vec_index % len(DEFAULT_PALETTE)]
        vec_index += 1

        name = ref.replace(".", "__")
        style_name = name + "_style"
        styles[style_name] = _origo_style(gclass, eff, base_color, _text(label))

        layer = {
            "name": name,
            "title": _text(label),
            "type": "GEOJSON",
            # Root-relative with query params: Origo fetches it verbatim. Served in
            # the native CRS; the explicit projection tells Origo what it is getting.
            "source": f"/data/{ref}.geojson?view={view_id}&crs=3014",
            "projection": "EPSG:3014",
            "style": style_name,
            "visible": visible,       # Origo defaults visible to FALSE — always set it
            "group": "root",          # omitting group hides the layer from the legend
            "queryable": bool(popup_attrs),
        }
        if popup_attrs:
            # `name` is a property lookup key (must stay verbatim); `title` is rendered.
            layer["attributes"] = [{"name": str(a), "title": _text(f"{a}: ")}
                                   for a in popup_attrs]
        layers_out.append(layer)

    config = {
        "projectionCode": "EPSG:3014",
        "proj4Defs": [
            {"code": "EPSG:3014", "alias": "urn:ogc:def:crs:EPSG::3014",
             "projection": PROJ4_3014},
            {"code": "EPSG:3006", "alias": "urn:ogc:def:crs:EPSG::3006",
             "projection": PROJ4_3006},
        ],
        "projectionExtent": list(PROJECTION_EXTENT_3014),
        "extent": list(PROJECTION_EXTENT_3014),
        "center": [round(center[0], 2), round(center[1], 2)],
        "zoom": _zoom_for_extent(view_extent),
        "resolutions": list(RESOLUTIONS),
        "featureinfoOptions": {"infowindow": "overlay", "pinning": False},
        "controls": [
            {"name": "legend", "options": {"expanded": True}},
            {"name": "home", "options": {"extent": view_extent}},
        ],
        # Origo renders index 0 on top; MapLibre paints the LAST array entry on top.
        # Reversing the overlays makes one spec stack identically in both renderers.
        # Backgrounds stay last, which is where Origo wants them.
        "layers": list(reversed(layers_out)) + basemap_layers,
    }
    if basemap_layers:
        config["groups"] = [{"name": "background", "title": "Bakgrundskarta"}]
    if sources:
        config["source"] = sources
    if styles:
        config["styles"] = styles
    # Consumed by the Origo page (Origo itself ignores unknown top-level keys).
    # `revision` covers app.layer_meta as well as the view row, mirroring the MapLibre
    # ETag: otherwise a layer(op='style') change would never prompt a reload.
    config["geodata"] = {"view_id": view_id, "version": view["version"],
                         "revision": (f"{view['version']}-{dbq.layer_meta_fingerprint(conn, spec)}"
                                      f"-{CODE_VERSION}"),
                         "title": spec.get("title") or view.get("title") or ""}
    if wmts_skipped:
        skip_note = ("WMTS layer(s) not shown here — their tile grid does not match this "
                     "map's resolution ladder: " + ", ".join(_text(t) for t in wmts_skipped)
                     + ". They render in the MapLibre view.")
        note = f"{note} {skip_note}" if note else skip_note
    if note:
        config["geodata"]["note"] = note
    return config
