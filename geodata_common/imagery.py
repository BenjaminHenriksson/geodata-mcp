"""Explicitly published panorama/scene catalogue and perspective camera geometry.

Only operator-configured, redacted previews are exposed. GPS is the camera
location, never a detected object's location; uncalibrated yaw stays uncalibrated.
"""

import hashlib
import io
import json
import math
import os
import re
from functools import lru_cache
from pathlib import Path

ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
LIMITATIONS = [
    "GPS describes the camera position, not the objects visible in the image.",
    "Panorama yaw is image-relative unless camera_heading_deg is calibrated; GPS course is not heading.",
    "No depth or surveyed ground intersection is available: image pixels yield rays, not ground coordinates.",
    "Camera altitude and scene display height may use different, uncalibrated vertical references.",
    "Preview media is redacted; stitching, motion and occlusion can hide or distort details.",
]


def _json(path):
    return json.loads(Path(path).read_text())


def _inside(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root or not path.is_file():
        raise ValueError("Unknown imagery asset")
    return path


@lru_cache(maxsize=8)
def _read_catalog(path, revision):
    result = {}
    for site in _json(path).get("sites", []):
        if site.get("published") is not True:
            continue
        ident = site.get("id", "")
        if not ID.fullmatch(ident) or ident in result:
            raise ValueError("Invalid imagery catalogue site identifier")
        extent = site.get("bounds", [])
        if len(extent) != 4 or not all(isinstance(v, (float, int)) and math.isfinite(v) for v in extent):
            raise ValueError("Imagery catalogue requires geographic bounds")
        if not (-180 <= extent[0] < extent[2] <= 180 and -90 <= extent[1] < extent[3] <= 90):
            raise ValueError("Invalid imagery catalogue bounds")
        result[ident] = site
    return result


def sites():
    path = os.environ.get("GEODATA_IMAGERY_CATALOG", "")
    if not path:
        return {}
    stat = Path(path).stat()
    return _read_catalog(path, (stat.st_mtime_ns, stat.st_size))


def site_config(site_id):
    site = sites().get(site_id)
    if site is None:
        raise ValueError("Unknown imagery site")
    return site


def catalogue():
    """Never return filesystem paths or unfiltered source manifests to clients."""
    public = []
    for site in sites().values():
        entry = {key: site[key] for key in ("id", "label", "bounds", "overview") if key in site}
        entry["has_splats"] = bool(site.get("splat_root"))
        entry["has_panoramas"] = bool(site.get("manifest"))
        public.append(entry)
    return {"sites": public, "limitations": LIMITATIONS}


@lru_cache(maxsize=8)
def _frames(path, revision):
    frames = []
    for index, item in enumerate(_json(path).get("frames", [])):
        lat, lon = item.get("latitude"), item.get("longitude")
        if not isinstance(lat, (float, int)) or not isinstance(lon, (float, int)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        # Never fall back to the full-resolution/original `image` field.
        preview = item.get("preview_image")
        if not isinstance(preview, str):
            continue
        frame = {key: item[key] for key in ("latitude", "longitude", "altitude_m", "utc", "timestamp", "gps_segment") if key in item}
        frame.update(id=hashlib.sha256(preview.encode()).hexdigest()[:20],
                     index=index, preview=preview, source=item.get("source"),
                     camera_heading_deg=item.get("camera_heading_deg"))
        frames.append(frame)
    return frames


def frames(site_id):
    path = site_config(site_id).get("manifest")
    if not path:
        return []
    stat = Path(path).stat()
    return _frames(path, (stat.st_mtime_ns, stat.st_size))


def public_frame(frame):
    return {key: value for key, value in frame.items() if key not in ("preview", "source")}


def route(site_id):
    source = frames(site_id)
    result, previous = [], None
    for frame in source:
        item = public_frame(frame)
        item["break_before"] = previous is None or previous.get("source") != frame.get("source") or previous.get("gps_segment") != frame.get("gps_segment") or abs(frame.get("timestamp", 0) - previous.get("timestamp", 0)) > 45
        result.append(item)
        previous = frame
    return {"site_id": site_id, "frames": result, "limitations": LIMITATIONS}


def frame_by_id(site_id, frame_id):
    frame = next((f for f in frames(site_id) if f["id"] == frame_id), None)
    if frame is None:
        raise ValueError("Unknown panorama")
    return frame


def panorama_path(site_id, frame_id):
    site = site_config(site_id)
    frame = frame_by_id(site_id, frame_id)
    relative = Path(frame["preview"])
    if relative.is_absolute() or ".." in relative.parts or len(relative.parts) < 2:
        raise ValueError("Invalid published panorama path")
    root = site.get("preview_roots", {}).get(relative.parts[0])
    if not root or relative.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
        raise ValueError("Unknown published preview root")
    return _inside(root, Path(*relative.parts[1:]))


def splat_path(site_id, relative):
    root = site_config(site_id).get("splat_root")
    path = Path(relative)
    if not root or path.is_absolute() or ".." in path.parts or path.suffix.lower() not in (".json", ".webp", ".sog"):
        raise ValueError("Unknown scene asset")
    return _inside(root, relative)


def scene(site_id):
    site = site_config(site_id)
    return {key: site[key] for key in ("id", "label", "origin", "altitude", "georeference", "sogToLocal", "bounds") if key in site}


def search(site_id=None, bbox=None, limit=30, offset=0):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if bbox is not None:
        if not isinstance(bbox, list) or len(bbox) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in bbox) or not (-180 <= bbox[0] < bbox[2] <= 180 and -90 <= bbox[1] < bbox[3] <= 90):
            raise ValueError("bbox must be [west, south, east, north] in longitude/latitude")
    selected = [site_config(site_id)] if site_id else sites().values()
    matches = []
    for site in selected:
        for frame in frames(site["id"]):
            if bbox and not (bbox[0] <= frame["longitude"] <= bbox[2] and bbox[1] <= frame["latitude"] <= bbox[3]):
                continue
            matches.append({"site_id": site["id"], **public_frame(frame)})
    return {"panoramas": matches[offset:offset + limit], "total": len(matches),
            "next_offset": offset + limit if offset + limit < len(matches) else None,
            "limitations": LIMITATIONS}


def camera(yaw=0, pitch=0, hfov=90, width=1536, height=1024):
    for name, value, low, high in (("yaw", yaw, -180, 180), ("pitch", pitch, -85, 85), ("hfov", hfov, 30, 120), ("width", width, 64, 2048), ("height", height, 64, 2048)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{name} must be between {low} and {high}")
    if int(width) != width or int(height) != height or width * height > 3_145_728:
        raise ValueError("Image dimensions must be integers, at most 3 megapixels")
    yaw, pitch = math.radians(yaw), math.radians(pitch)
    # Row-major rotation from pinhole camera (+X right,+Y up,+Z forward)
    # into panorama-local axes. Yaw zero is the equirectangular image centre.
    rotation = [[math.cos(yaw), -math.sin(yaw) * math.sin(pitch), math.sin(yaw) * math.cos(pitch)],
                [0, math.cos(pitch), math.sin(pitch)],
                [-math.sin(yaw), -math.cos(yaw) * math.sin(pitch), math.cos(yaw) * math.cos(pitch)]]
    focal = width / (2 * math.tan(math.radians(hfov) / 2))
    return {"width": int(width), "height": int(height), "yaw_deg": math.degrees(yaw), "pitch_deg": math.degrees(pitch), "hfov_deg": hfov,
            "vfov_deg": math.degrees(2 * math.atan(height / (2 * focal))),
            "focal_px": focal, "principal_point_px": [width / 2, height / 2],
            "camera_to_panorama_rotation": rotation,
            "pixel_to_ray": "For pixel column u,row v (zero-based), normalize R @ [(u+0.5-cx)/f, -(v+0.5-cy)/f, 1]. Ray is in panorama-local coordinates; it has no distance."}


def render_view(site_id, frame_id, *, yaw=0, pitch=0, hfov=90, width=1536, height=1024):
    """Return (JPEG bytes, metadata); no network, model calls or persistent writes."""
    import numpy as np
    from PIL import Image

    model = camera(yaw, pitch, hfov, width, height)
    frame = frame_by_id(site_id, frame_id)
    with Image.open(panorama_path(site_id, frame_id)) as image:
        if image.width * image.height > 50_000_000 or abs(image.width / image.height - 2) > .05:
            raise ValueError("Published preview must be a bounded 2:1 equirectangular panorama")
        pixels = np.asarray(image.convert("RGB"))
    h, w = pixels.shape[:2]
    width, height = model["width"], model["height"]
    output_pixels = np.empty((height, width, 3), dtype=np.uint8)
    rotation = np.asarray(model["camera_to_panorama_rotation"], dtype=np.float32).T
    # Render strips to bound working memory independently of output resolution.
    for row in range(0, height, 128):
        stop = min(row + 128, height)
        u, v = np.meshgrid(np.arange(width, dtype=np.float32) + .5,
                           np.arange(row, stop, dtype=np.float32) + .5)
        rays = np.stack(((u - width / 2) / model["focal_px"],
                         -(v - height / 2) / model["focal_px"], np.ones_like(u)), axis=-1) @ rotation
        x = (np.arctan2(rays[..., 0], rays[..., 2]) / (2 * np.pi) + .5) * w - .5
        y = (.5 - np.arctan2(rays[..., 1], np.hypot(rays[..., 0], rays[..., 2])) / np.pi) * h - .5
        # Bilinear wrap at the panorama seam, clamp at the poles.
        y = np.clip(y, 0, h - 1)
        x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
        dx, dy = (x - x0).astype(np.float32)[..., None], (y - y0).astype(np.float32)[..., None]
        a = pixels[y0, x0 % w] * (1 - dx) + pixels[y0, (x0 + 1) % w] * dx
        b = pixels[np.minimum(y0 + 1, h - 1), x0 % w] * (1 - dx) + pixels[np.minimum(y0 + 1, h - 1), (x0 + 1) % w] * dx
        output_pixels[row:stop] = np.uint8(np.clip(a * (1 - dy) + b * dy, 0, 255))
    output = Image.fromarray(output_pixels)
    stream = io.BytesIO()
    output.save(stream, format="JPEG", quality=92)
    heading = frame.get("camera_heading_deg")
    valid_heading = not isinstance(heading, bool) and isinstance(heading, (int, float)) and math.isfinite(heading)
    metadata = {"site_id": site_id, "panorama": public_frame(frame), "projection": "perspective",
                "camera": model, "absolute_heading_deg": (heading + yaw) % 360 if valid_heading else None,
                "heading_calibrated": valid_heading, "ground_coordinates_available": False,
                "source_preview_size": [w, h], "limitations": LIMITATIONS}
    return stream.getvalue(), metadata
