"""Compile an app.map_views spec into an Origo-style JSON configuration.

Interop contract document (per CONTRACTS.md): keys are kept plausible per the
Origo layer/config model, but this is not a pixel-perfect Origo deployment file.
"""
import dbq
from compile_maplibre import (CARTO_ATTRIBUTION, DEFAULT_CIRCLE_RADIUS,
                              DEFAULT_FILL_OPACITY, DEFAULT_LINE_WIDTH,
                              DEFAULT_PALETTE, DEFAULT_POLYGON_OUTLINE_WIDTH,
                              DEFAULT_POLYGON_STROKE, OSM_ATTRIBUTION,
                              OSM_TILES, POSITRON_TILES, _num)

PROJ4_3014 = ("+proj=tmerc +lat_0=0 +lon_0=17.25 +k=1 +x_0=150000 +y_0=0 "
              "+ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs")

# Fallback: roughly the Sundsvall region in EPSG:3014, used only when a view has
# neither an explicit extent nor any vector layer with rows.
DEFAULT_EXTENT_3014 = [100000.0, 6850000.0, 210000.0, 6990000.0]


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


def _origo_style(gclass, eff, base_color):
    fill = eff.get("fill") or base_color
    opacity = _num(eff.get("opacity"), DEFAULT_FILL_OPACITY)
    width = eff.get("width")
    if gclass == "polygon":
        stroke = eff.get("stroke") or DEFAULT_POLYGON_STROKE
        return [[{"fill": {"color": _rgba(fill, opacity)},
                  "stroke": {"color": stroke,
                             "width": _num(width, DEFAULT_POLYGON_OUTLINE_WIDTH)}}]]
    if gclass == "line":
        color = eff.get("stroke") or eff.get("fill") or base_color
        return [[{"stroke": {"color": color, "width": _num(width, DEFAULT_LINE_WIDTH)}}]]
    return [[{"circle": {"radius": _num(eff.get("circle"), DEFAULT_CIRCLE_RADIUS),
                         "fill": {"color": fill},
                         "stroke": {"color": "#ffffff", "width": 1}}}]]


def compile_origo(conn, view):
    """Return the Origo config dict for a map_views row (view_id/title/spec/version)."""
    spec = view["spec"] or {}
    view_id = view["view_id"]
    title = spec.get("title") or view.get("title") or ""

    extent = dbq.view_extent_3014(conn, spec) or list(DEFAULT_EXTENT_3014)
    center = [(extent[0] + extent[2]) / 2.0, (extent[1] + extent[3]) / 2.0]

    layers_out = []

    basemap = spec.get("basemap") or "positron"
    if isinstance(basemap, str) and basemap.startswith("wms:"):
        ds = dbq.wms_dataset(conn, basemap[4:])
        if ds is not None and ds["url"]:
            layers_out.append({"name": ds["external_id"], "title": ds["title"],
                               "type": "WMS", "source": ds["url"],
                               "attribution": ds["attribution"], "visible": True})
    elif basemap == "osm":
        layers_out.append({"name": "basemap_osm", "title": "OpenStreetMap",
                           "type": "XYZ", "source": OSM_TILES,
                           "attribution": OSM_ATTRIBUTION, "visible": True})
    elif basemap != "none":
        layers_out.append({"name": "basemap_positron", "title": "Carto Positron",
                           "type": "XYZ", "source": POSITRON_TILES,
                           "attribution": CARTO_ATTRIBUTION, "visible": True})

    vec_index = 0
    for entry in spec.get("layers") or []:
        if not isinstance(entry, dict):
            continue
        ref = entry.get("ref")
        if not isinstance(ref, str):
            continue

        if ref.startswith("wms:"):
            ds = dbq.wms_dataset(conn, ref[4:])
            if ds is None or not ds["url"]:
                continue
            visible = entry.get("visible") is not False
            layers_out.append({"name": ds["external_id"],
                               "title": entry.get("label") or ds["title"],
                               "type": "WMS", "source": ds["url"],
                               "attribution": ds["attribution"], "visible": visible})
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
        gclass = dbq.geometry_class(conn, schema, table)
        base_color = DEFAULT_PALETTE[vec_index % len(DEFAULT_PALETTE)]
        vec_index += 1

        layers_out.append({
            "name": ref.replace(".", "__"),
            "title": label,
            "type": "GEOJSON",
            "source": f"/data/{ref}.geojson?view={view_id}&crs=3014",
            "style": _origo_style(gclass, eff, base_color),
            "visible": visible,
        })

    return {"title": title,
            "projectionCode": "EPSG:3014",
            "proj4Defs": [{"code": "EPSG:3014", "projection": PROJ4_3014}],
            "extent": extent,
            "center": center,
            "zoom": 5,
            "layers": layers_out,
            "controls": [{"name": "legend"}, {"name": "zoom"}, {"name": "attribution"}]}
