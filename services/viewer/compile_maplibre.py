"""Compile an app.map_views spec into a MapLibre GL style document."""
import os
from urllib.parse import quote

import dbq

POSITRON_TILES = "https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png"
OSM_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
CARTO_ATTRIBUTION = "© OpenStreetMap contributors © CARTO"
OSM_ATTRIBUTION = "© OpenStreetMap contributors"

GEOJSON_MAX_FEATURES = 20000

DEFAULT_PALETTE = ["#1f78b4", "#e31a1c", "#33a02c", "#ff7f00",
                   "#6a3d9a", "#b15928", "#a6cee3", "#fb9a99"]
DEFAULT_POLYGON_STROKE = "#333333"
DEFAULT_FILL_OPACITY = 0.45
DEFAULT_LINE_WIDTH = 1.5
DEFAULT_POLYGON_OUTLINE_WIDTH = 1
DEFAULT_CIRCLE_RADIUS = 5


def wms_tile_url(base_url, external_id):
    sep = "&" if "?" in base_url else "?"
    return (base_url + sep
            + "SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0"
            + "&LAYERS=" + quote(external_id or "", safe="")
            + "&STYLES=&FORMAT=image/png&TRANSPARENT=true&CRS=EPSG:3857"
            + "&WIDTH=256&HEIGHT=256&BBOX={bbox-epsg-3857}")


def _num(value, default):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return default


def _basemap(conn, basemap, sources, layers):
    if basemap == "none":
        layers.append({"id": "background", "type": "background",
                       "paint": {"background-color": "#f8f8f8"}})
        return
    if isinstance(basemap, str) and basemap.startswith("wms:"):
        ds = dbq.wms_dataset(conn, basemap[4:])
        if ds is not None and ds["url"]:
            sources["basemap"] = {"type": "raster",
                                  "tiles": [wms_tile_url(ds["url"], ds["external_id"])],
                                  "tileSize": 256,
                                  "attribution": ds["attribution"]}
            layers.append({"id": "basemap", "type": "raster", "source": "basemap"})
            return
        basemap = "positron"  # fall through: unknown WMS dataset
    if basemap == "osm":
        tiles, attribution = [OSM_TILES], OSM_ATTRIBUTION
    else:  # positron (default)
        tiles, attribution = [POSITRON_TILES], CARTO_ATTRIBUTION
    sources["basemap"] = {"type": "raster", "tiles": tiles,
                          "tileSize": 256, "attribution": attribution}
    layers.append({"id": "basemap", "type": "raster", "source": "basemap"})


def compile_style(conn, view):
    """Return the MapLibre style dict for a map_views row (view_id/title/spec/version)."""
    spec = view["spec"] or {}
    view_id = view["view_id"]
    title = spec.get("title") or view.get("title") or ""
    public_base = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8080").rstrip("/")

    sources = {}
    layers = []
    legend = []
    popups = {}

    _basemap(conn, spec.get("basemap") or "positron", sources, layers)

    vec_index = 0
    for i, entry in enumerate(spec.get("layers") or []):
        if not isinstance(entry, dict):
            continue
        ref = entry.get("ref")
        if not isinstance(ref, str):
            continue

        if ref.startswith("wms:"):
            ds = dbq.wms_dataset(conn, ref[4:])
            if ds is None or not ds["url"]:
                continue
            sid = f"wms_{i}"
            sources[sid] = {"type": "raster",
                            "tiles": [wms_tile_url(ds["url"], ds["external_id"])],
                            "tileSize": 256,
                            "attribution": ds["attribution"]}
            layer = {"id": sid, "type": "raster", "source": sid}
            if entry.get("visible") is False:
                layer["layout"] = {"visibility": "none"}
            layers.append(layer)
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
        popup_attrs = entry["popup"] if "popup" in entry else meta["popup"]
        if not isinstance(popup_attrs, list):
            popup_attrs = []

        gclass = dbq.geometry_class(conn, schema, table)
        count = dbq.feature_count(conn, schema, table)

        base_color = DEFAULT_PALETTE[vec_index % len(DEFAULT_PALETTE)]
        vec_index += 1
        fill = eff.get("fill") or base_color
        opacity = _num(eff.get("opacity"), DEFAULT_FILL_OPACITY)
        width = eff.get("width")
        circle = eff.get("circle")

        if count is not None and count > GEOJSON_MAX_FEATURES:
            sources[ref] = {"type": "vector",
                            "tiles": [f"{public_base}/tiles/{ref}/{{z}}/{{x}}/{{y}}.mvt"
                                      f"?view={view_id}"],
                            "minzoom": 0, "maxzoom": 22}
            source_layer = table
        else:
            sources[ref] = {"type": "geojson",
                            "data": f"/data/{ref}.geojson?view={view_id}&crs=4326"}
            source_layer = None

        def make_layer(layer_id, layer_type, paint):
            out = {"id": layer_id, "type": layer_type, "source": ref, "paint": paint}
            if source_layer is not None:
                out["source-layer"] = source_layer
            if not visible:
                out["layout"] = {"visibility": "none"}
            return out

        if gclass == "polygon":
            stroke = eff.get("stroke") or DEFAULT_POLYGON_STROKE
            layers.append(make_layer(f"{ref}__fill", "fill",
                                     {"fill-color": fill, "fill-opacity": opacity}))
            layers.append(make_layer(
                f"{ref}__line", "line",
                {"line-color": stroke,
                 "line-width": _num(width, DEFAULT_POLYGON_OUTLINE_WIDTH)}))
            primary_id = f"{ref}__fill"
            legend_fill, legend_stroke = fill, stroke
        elif gclass == "line":
            color = eff.get("stroke") or eff.get("fill") or base_color
            layers.append(make_layer(
                f"{ref}__line", "line",
                {"line-color": color, "line-width": _num(width, DEFAULT_LINE_WIDTH)}))
            primary_id = f"{ref}__line"
            legend_fill, legend_stroke = color, color
        else:  # point
            layers.append(make_layer(
                f"{ref}__circle", "circle",
                {"circle-radius": _num(circle, DEFAULT_CIRCLE_RADIUS),
                 "circle-color": fill,
                 "circle-stroke-color": "#ffffff",
                 "circle-stroke-width": 1}))
            primary_id = f"{ref}__circle"
            legend_fill, legend_stroke = fill, "#ffffff"

        if popup_attrs:
            popups[primary_id] = [str(a) for a in popup_attrs]
        if visible:
            legend.append({"id": ref, "title": label, "type": gclass,
                           "fill": legend_fill, "stroke": legend_stroke})

    extent_3014 = dbq.view_extent_3014(conn, spec)
    extent_4326 = dbq.transform_extent_to_4326(conn, extent_3014) if extent_3014 else None

    metadata = {"view_id": view_id,
                "version": view["version"],
                "title": title,
                "extent_4326": extent_4326,
                "legend": legend if spec.get("legend", True) else [],
                "popups": popups}

    return {"version": 8,
            "name": title or view_id,
            "sources": sources,
            "layers": layers,
            "metadata": metadata}
