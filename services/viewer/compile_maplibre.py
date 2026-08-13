"""Compile an app.map_views spec into a MapLibre GL style document."""
import hashlib
import os
import pathlib
from urllib.parse import quote

import dbq
import netauth


def _code_version():
    """Short fingerprint of the compiler code, folded into the style ETag and the
    Origo revision. The view version + layer_meta fingerprint alone do NOT change
    when only the viewer code changes, so a deploy that alters compiled output
    (new default popups, style tweaks, MVT/GeoJSON shape) would otherwise keep
    serving a stale style to any page holding a cached copy. Hashing the compiler
    sources makes every such deploy bust client caches automatically."""
    here = pathlib.Path(__file__).parent
    h = hashlib.sha1()
    for name in ("compile_maplibre.py", "compile_origo.py", "dbq.py"):
        try:
            h.update((here / name).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:8]


CODE_VERSION = _code_version()

POSITRON_TILES = "https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}@2x.png"
CARTO_ATTRIBUTION = "© OpenStreetMap contributors © CARTO"

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


def wms_proxy_tile_url(dataset_id, view_id, external_id):
    """Same GetMap template, addressed at the viewer's /wmsref proxy (relative URL,
    same origin — like the /data URLs) for upstreams that need Basic auth."""
    return (f"/wmsref/{dataset_id}?view={view_id}"
            + "&SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0"
            + "&LAYERS=" + quote(external_id or "", safe="")
            + "&STYLES=&FORMAT=image/png&TRANSPARENT=true&CRS=EPSG:3857"
            + "&WIDTH=256&HEIGHT=256&BBOX={bbox-epsg-3857}")


_WEB_MERCATOR_HINTS = ("3857", "900913")


def wmts_tile_url(ds):
    """GetTile KVP template for a WMTS raster_ref, or None when the layer has no
    Web-Mercator matrix set MapLibre could address (raster sources always tile on
    the standard 3857 grid). Matrix identifiers must be plain zoom integers or
    '<set>:<zoom>' (the GeoServer GWC convention) for '{z}' substitution to hold.
    """
    meta = (ds.get("schema_summary") or {}).get("wmts") or {}
    for set_name, info in (meta.get("matrix_sets") or {}).items():
        crs = str((info or {}).get("crs") or "")
        if not (set_name == "WebMercatorQuad"
                or any(h in crs or h in set_name for h in _WEB_MERCATOR_HINTS)):
            continue
        ids = [str(m) for m in (info or {}).get("matrix_ids") or []]
        if ids and all(m.isdigit() for m in ids):
            tile_matrix = "{z}"
        elif ids and all(m == f"{set_name}:{n}" for n, m in enumerate(ids)):
            tile_matrix = f"{set_name}:{{z}}"
        else:
            continue
        formats = meta.get("formats") or []
        fmt = "image/png" if "image/png" in formats else (formats[0] if formats else "image/png")
        sep = "&" if "?" in ds["url"] else "?"
        return (ds["url"] + sep
                + "SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
                + "&LAYER=" + quote(ds["external_id"] or "", safe="")
                + "&STYLE=" + quote(meta.get("default_style") or "", safe="")
                + "&TILEMATRIXSET=" + quote(set_name, safe=":")
                + "&TILEMATRIX=" + tile_matrix
                + "&TILEROW={y}&TILECOL={x}"
                + "&FORMAT=" + quote(fmt, safe=""))
    return None


def _num(value, default):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return default


def resolve_popup_attrs(entry, meta, cols):
    """Attribute names to expose in a vector layer's feature-info popup.

    Precedence: an explicit ``popup`` on the map entry wins (including ``[]``,
    which deliberately disables the popup); then the layer's stored popup; then
    a default of every non-geometry column, so any layer is click-to-inspect
    without needing to be configured first. Shared by both renderers.
    """
    if "popup" in entry:
        return [str(a) for a in entry["popup"]] if isinstance(entry["popup"], list) else []
    if meta["popup"]:
        return [str(a) for a in meta["popup"]]
    return dbq.non_geom_columns(cols)


def _compare_metadata(compare, layer_ids_by_ref):
    """Resolve a spec['compare'] block (ref-based, from map_ops) into the layer ids the
    viewer's imagery inspector toggles. Returns None when neither vintage rendered (e.g.
    the imagery was not in the catalog), so the inspector simply does not appear."""
    if not isinstance(compare, dict):
        return None
    out = {}
    for side in ("before", "after"):
        info = compare.get(side)
        if isinstance(info, dict) and info.get("ref") in layer_ids_by_ref:
            out[side] = {"id": layer_ids_by_ref[info["ref"]][0],
                         "label": info.get("label") or side,
                         "year": info.get("year")}
    cref = compare.get("changes_ref")
    if cref in layer_ids_by_ref:
        out["change_layer_ids"] = layer_ids_by_ref[cref]
    return out if (out.get("before") or out.get("after")) else None


def _basemap(sources, layers):
    # The MapLibre renderer always shows Carto Positron: it renders in EPSG:3857,
    # where the CDN tiles are aligned and far faster than any municipal WMS.
    # spec['basemap'] is not consulted — the backdrop is per-renderer, not
    # per-view: Origo always shows the official municipal WMS instead (it renders
    # in EPSG:3014, where XYZ tiles cannot be aligned; see compile_origo).
    sources["basemap"] = {"type": "raster", "tiles": [POSITRON_TILES],
                          "tileSize": 256, "attribution": CARTO_ATTRIBUTION}
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
    layer_ids_by_ref = {}  # spec ref -> [maplibre layer ids], for the compare inspector

    _basemap(sources, layers)

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
            if ds.get("source_kind") == "wmts":
                tiles = wmts_tile_url(ds)
                if not tiles:
                    continue  # no Web-Mercator matrix set — nothing MapLibre can address
            elif netauth.userpwd_for(ds["url"]) is not None:
                # The browser cannot hold the upstream credential; the proxy injects it.
                tiles = wms_proxy_tile_url(ref[4:], view_id, ds["external_id"])
            else:
                tiles = wms_tile_url(ds["url"], ds["external_id"])
            sid = f"wms_{i}"
            sources[sid] = {"type": "raster",
                            "tiles": [tiles],
                            "tileSize": 256,
                            "attribution": ds["attribution"]}
            layer = {"id": sid, "type": "raster", "source": sid}
            if entry.get("visible") is False:
                layer["layout"] = {"visibility": "none"}
            layers.append(layer)
            layer_ids_by_ref[ref] = [sid]
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

        layer_ids_by_ref[ref] = [lyr["id"] for lyr in layers
                                 if lyr.get("source") == ref]

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

    compare = _compare_metadata(spec.get("compare"), layer_ids_by_ref)
    if compare:
        metadata["compare"] = compare

    return {"version": 8,
            "name": title or view_id,
            "sources": sources,
            "layers": layers,
            "metadata": metadata}
