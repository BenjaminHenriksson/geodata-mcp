"""Layer metadata and defaults shared by the two map renderers."""

import hashlib
import pathlib
from dataclasses import dataclass

import dbq


def _code_version():
    """Short fingerprint of the compiler code, folded into the style ETag and the
    Origo revision. The view version + layer_meta fingerprint alone do NOT change
    when only the viewer code changes, so a deploy that alters compiled output
    (new default popups, style tweaks, MVT/GeoJSON shape) would otherwise keep
    serving a stale style to any page holding a cached copy. Hashing the compiler
    sources makes every such deploy bust client caches automatically."""
    here = pathlib.Path(__file__).parent
    h = hashlib.sha1()
    for name in (
        "compile_common.py",
        "compile_maplibre.py",
        "compile_origo.py",
        "dbq.py",
    ):
        try:
            h.update((here / name).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:8]


CODE_VERSION = _code_version()

DEFAULT_PALETTE = [
    "#1f78b4",
    "#e31a1c",
    "#33a02c",
    "#ff7f00",
    "#6a3d9a",
    "#b15928",
    "#a6cee3",
    "#fb9a99",
]
DEFAULT_POLYGON_STROKE = "#333333"
DEFAULT_FILL_OPACITY = 0.45
DEFAULT_LINE_WIDTH = 1.5
DEFAULT_POLYGON_OUTLINE_WIDTH = 1
DEFAULT_CIRCLE_RADIUS = 5


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
        return (
            [str(a) for a in entry["popup"]] if isinstance(entry["popup"], list) else []
        )
    if meta["popup"]:
        return [str(a) for a in meta["popup"]]
    return dbq.non_geom_columns(cols)


@dataclass
class VectorLayer:
    schema: str
    table: str
    style: dict
    visible: bool
    label: str
    popup: list[str]
    geometry: str


def resolve_vector_layer(conn, entry) -> VectorLayer | None:
    parsed = dbq.split_layer_ref(entry["ref"])
    if parsed is None:
        return None
    schema, table = parsed
    cols = dbq.columns(conn, schema, table)
    if cols is None or not dbq.has_geom(cols):
        return None
    meta = dbq.layer_meta(conn, schema, table)
    style = dict(meta["style"])
    entry_style = entry.get("style")
    if isinstance(entry_style, dict):
        style.update({k: v for k, v in entry_style.items() if v is not None})
    return VectorLayer(
        schema=schema,
        table=table,
        style=style,
        visible=entry["visible"]
        if isinstance(entry.get("visible"), bool)
        else meta["visible"],
        label=entry.get("label") or meta["label"] or table,
        popup=resolve_popup_attrs(entry, meta, cols),
        geometry=dbq.geometry_class(conn, schema, table),
    )
