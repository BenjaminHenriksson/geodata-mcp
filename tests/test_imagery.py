import io
import json
from pathlib import Path

import imagery_basemaps
import imagery_ops
import imagery_routes
import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

from geodata_common import imagery


@pytest.fixture
def published(tmp_path, monkeypatch):
    preview = tmp_path / "preview"
    preview.mkdir()
    # Independent longitude/latitude colour ramp: red increases to the right,
    # green toward the south. This detects flipped pitch/yaw and wrong projection.
    image = np.empty((512, 1024, 3), dtype=np.uint8)
    image[:, :, 0] = np.arange(1024)[None, :] / 1023 * 255
    image[:, :, 1] = np.arange(512)[:, None] / 511 * 255
    image[:, :, 2] = 64
    Image.fromarray(image).save(preview / "frame.png")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"summary": {"private": "NEVER EXPOSE"}, "frames": [
        {"image": "original.jpg", "preview_image": "preview/frame.png", "latitude": 59.3, "longitude": 18.1,
         "camera_heading_deg": None, "course_deg": 99, "timestamp": 1, "source": "private source"},
        {"image": "original-only.jpg", "latitude": 59.3, "longitude": 18.1},
    ]}))
    scene = tmp_path / "scene"
    scene.mkdir()
    (scene / "lod-meta.json").write_text("{}")
    site = {"id": "demo", "label": "Demo", "published": True, "bounds": [18, 59, 19, 60],
            "manifest": str(manifest), "preview_roots": {"preview": str(preview)}, "splat_root": str(scene)}
    catalogue = tmp_path / "catalogue.json"
    catalogue.write_text(json.dumps({"sites": [site, {"id": "private", "published": False}]}))
    monkeypatch.setenv("GEODATA_IMAGERY_CATALOG", str(catalogue))
    return site, imagery.frames("demo")[0]["id"]


def test_catalogue_and_search_expose_only_published_metadata(published):
    encoded = json.dumps(imagery.catalogue()) + json.dumps(imagery.search()) + json.dumps(imagery.route("demo"))
    assert "NEVER EXPOSE" not in encoded and "private source" not in encoded
    assert "preview/frame.png" not in encoded and "original" not in encoded
    assert "course_deg" not in encoded
    assert imagery.search()["total"] == 1
    assert imagery.search(bbox=[1, 1, 2, 2])["total"] == 0
    assert imagery.catalogue()["sites"][0]["has_splats"] is True


@pytest.mark.parametrize("yaw,pitch,expected", [(0, 0, [128, 128]), (90, 0, [191, 128]), (-90, 0, [64, 128]), (0, 45, [128, 64]), (0, -45, [128, 191])])
def test_perspective_renders_independent_panorama_coordinates(published, yaw, pitch, expected):
    _, frame = published
    data, meta = imagery.render_view("demo", frame, yaw=yaw, pitch=pitch, width=257, height=257)
    pixels = np.asarray(Image.open(io.BytesIO(data)))
    assert np.max(np.abs(pixels[128, 128, :2].astype(int) - expected)) < 3
    assert meta["heading_calibrated"] is False and meta["absolute_heading_deg"] is None
    assert meta["ground_coordinates_available"] is False
    assert meta["panorama"]["longitude"] == 18.1


def test_perspective_edges_match_pinhole_fov_and_seam_wrap(published):
    _, frame = published
    data, meta = imagery.render_view("demo", frame, width=513, height=257, hfov=90)
    pixels = np.asarray(Image.open(io.BytesIO(data)))
    assert abs(int(pixels[128, 0, 0]) - 96) < 3
    assert abs(int(pixels[128, -1, 0]) - 159) < 3
    for yaw in (-180, 180):
        data, _ = imagery.render_view("demo", frame, width=256, height=128, yaw=yaw)
        pixels = np.asarray(Image.open(io.BytesIO(data)))
        assert int(pixels[64, 110, 0]) > 240
        assert int(pixels[64, 145, 0]) < 15
    rotation = np.asarray(meta["camera"]["camera_to_panorama_rotation"])
    np.testing.assert_allclose(rotation @ rotation.T, np.eye(3), atol=1e-12)


@pytest.mark.parametrize("kwargs", [{"hfov": 180}, {"yaw": float("nan")}, {"width": 2048, "height": 2048}, {"width": True}, {"width": 200.5}])
def test_render_parameters_are_bounded(published, kwargs):
    with pytest.raises(ValueError):
        imagery.render_view("demo", published[1], **kwargs)


def test_asset_allowlist_rejects_traversal_symlinks_and_originals(published, tmp_path):
    site, frame = published
    secret = tmp_path / "secret.json"
    secret.write_text("secret")
    (Path(site["splat_root"]) / "escape.json").symlink_to(secret)
    for relative in ("../secret.json", str(secret), "escape.json", "private.py"):
        with pytest.raises(ValueError):
            imagery.splat_path("demo", relative)
    assert imagery.panorama_path("demo", frame).name == "frame.png"
    with pytest.raises(ValueError):
        imagery.panorama_path("demo", "original.jpg")


def test_every_http_imagery_route_requires_view_capability(published):
    app = FastAPI()
    def capability(view):
        if view != "allowed":
            raise HTTPException(404, "unknown view")
    imagery_routes.register(app, capability)
    client = TestClient(app)
    suffixes = ["catalogue", "demo/route", "demo/scene", "demo/splat/lod-meta.json",
                f"demo/panorama/{published[1]}", f"demo/perspective/{published[1]}.jpg"]
    for suffix in suffixes:
        assert client.get(f"/v/unknown/imagery/{suffix}").status_code == 404
        assert client.get(f"/v/allowed/imagery/{suffix}").status_code == 200
    response = client.get("/v/allowed/imagery/demo/splat/missing.json")
    assert str(published[0]["splat_root"]) not in response.text


def test_mcp_returns_trusted_perspective_bytes_and_no_remote_fetch(published):
    result = imagery_ops.run("workspace", {"operation": "view", "site_id": "demo", "frame_id": published[1], "width": 128, "height": 128})
    assert isinstance(result, imagery_ops.PerspectiveResult)
    assert result.image[:2] == b"\xff\xd8"
    assert result.metadata["projection"] == "perspective"
    assert "error" in imagery_ops.run("workspace", {"operation": "view"})


def test_unconfigured_catalogue_is_empty(monkeypatch):
    monkeypatch.delenv("GEODATA_IMAGERY_CATALOG", raising=False)
    assert imagery.catalogue()["sites"] == []


def test_demo_basemap_uses_fixed_wms_source_and_projected_tile_bounds(monkeypatch):
    from unittest.mock import MagicMock
    response = MagicMock(content=b"\x89PNG\r\n\x1a\nfixture")
    get = MagicMock(return_value=response)
    monkeypatch.setattr(imagery_basemaps.httpx, "get", get)
    assert imagery_basemaps.fetch_tile("ortho2024", 1, 1, 0).startswith(b"\x89PNG")
    assert get.call_args.args[0].endswith("WMS_STHLM_ORTOFOTO_2024")
    params = get.call_args.kwargs["params"]
    assert params["LAYERS"] == "p_1003240" and params["SRS"] == "EPSG:3857"
    assert [float(v) for v in params["BBOX"].split(",")][:2] == [0, 0]
    for kind, z, x, y in (("http://localhost", 1, 1, 1), ("map", 21, 1, 1), ("map", 1, -1, 0)):
        with pytest.raises(ValueError):
            imagery_basemaps.fetch_tile(kind, z, x, y)
