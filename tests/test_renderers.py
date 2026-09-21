import copy

import pytest

import compile_maplibre
import compile_origo
import dbq
from geodata_common import netauth


@pytest.fixture
def render(monkeypatch):
    """Compile both renderers from the same catalog and layer metadata."""
    metadata = {
        "style": {"fill": "#123456", "opacity": 0.3},
        "popup": [],
        "visible": True,
        "label": "<Buildings>",
    }
    monkeypatch.setattr(
        dbq,
        "columns",
        lambda *args: [
            ("fid", "int4"),
            ("name", "text"),
            ("geom", "geometry"),
        ],
    )
    monkeypatch.setattr(dbq, "layer_meta", lambda *args: copy.deepcopy(metadata))
    monkeypatch.setattr(dbq, "geometry_class", lambda *args: "polygon")
    monkeypatch.setattr(dbq, "feature_count", lambda *args: 2)
    monkeypatch.setattr(
        dbq, "view_extent_3014", lambda *args: [150000, 6900000, 150100, 6900100]
    )
    monkeypatch.setattr(dbq, "transform_extent_to_4326", lambda *args: [17, 62, 18, 63])
    monkeypatch.setattr(dbq, "layer_meta_fingerprint", lambda *args: "metadata")
    monkeypatch.setattr(dbq, "wms_dataset_by_external_id", lambda *args: None)
    monkeypatch.setattr(netauth, "userpwd_for", lambda *args: None)
    monkeypatch.setattr(compile_origo, "CODE_VERSION", "compiler")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://example.test")

    def compile_pair(entry=None, **spec):
        view = {
            "view_id": "test-view",
            "title": "Test",
            "version": 3,
            "spec": {"layers": [entry or {"ref": "ref.buildings"}], **spec},
        }
        return (
            compile_maplibre.compile_style(None, view),
            compile_origo.compile_origo(None, view),
        )

    return compile_pair, metadata


@pytest.mark.parametrize(
    "popup, expected",
    [
        ({}, ["fid", "name"]),
        ({"popup": []}, []),
        ({"popup": None}, []),
        ({"popup": ["name", 5]}, ["name", "5"]),
    ],
)
def test_popup_precedence(render, popup, expected):
    pair, metadata = render
    ml, origo = pair({"ref": "ref.buildings", **popup})
    assert ml["metadata"]["popups"].get("ref.buildings__fill", []) == expected
    layer = origo["layers"][0]
    assert [attr["name"] for attr in layer.get("attributes", [])] == expected
    assert layer["queryable"] is bool(expected)
    metadata["popup"] = ["stored"]
    ml, origo = pair({"ref": "ref.buildings", **popup})
    assert ml["metadata"]["popups"].get("ref.buildings__fill", []) == (
        expected if "popup" in popup else ["stored"]
    )


@pytest.mark.parametrize("visible", [True, False, None, "false"])
def test_style_and_visibility_overrides(render, visible):
    pair, _ = render
    ml, origo = pair(
        {
            "ref": "ref.buildings",
            "visible": visible,
            "style": {"fill": None, "opacity": 0, "width": False},
        }
    )
    assert ml["layers"][1]["paint"] == {"fill-color": "#123456", "fill-opacity": 0}
    assert ml["layers"][2]["paint"]["line-width"] == 1
    assert bool(ml["metadata"]["legend"]) is (visible is not False)
    assert origo["layers"][0]["visible"] is (visible is not False)
    assert origo["layers"][0]["title"] == "&lt;Buildings&gt;"
    assert (
        origo["styles"]["ref__buildings_style"][0][0]["fill"]["color"]
        == "rgba(18,52,86,0)"
    )


@pytest.mark.parametrize(
    "geometry, suffix, style_key",
    [
        ("polygon", "fill", "fill"),
        ("line", "line", "stroke"),
        ("point", "circle", "circle"),
        ("unknown", "circle", "circle"),
    ],
)
@pytest.mark.parametrize("count", [None, 20000, 20001])
def test_geometry_and_tile_threshold(
    render, monkeypatch, geometry, suffix, style_key, count
):
    pair, _ = render
    monkeypatch.setattr(dbq, "geometry_class", lambda *args: geometry)
    monkeypatch.setattr(dbq, "feature_count", lambda *args: count)
    ml, origo = pair()
    assert ml["layers"][1]["id"] == f"ref.buildings__{suffix}"
    assert ml["sources"]["ref.buildings"]["type"] == (
        "vector" if count is not None and count > 20000 else "geojson"
    )
    assert style_key in origo["styles"]["ref__buildings_style"][0][0]


def test_missing_layers_do_not_change_palette(render, monkeypatch):
    pair, _ = render
    monkeypatch.setattr(dbq, "columns", lambda *args: None)
    ml, origo = pair()
    assert list(ml["sources"]) == ["basemap"]
    assert not ml["metadata"]["popups"]
    assert origo["layers"] == []
