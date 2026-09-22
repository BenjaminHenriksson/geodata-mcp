"""Panorama discovery and grounded perspective images for analyze."""

from dataclasses import dataclass

from geodata_common import imagery


@dataclass
class PerspectiveResult:
    """Trusted local image result; server converts this to MCP image/text content."""

    image: bytes
    metadata: dict


def run(workspace_id, params):
    operation = params.get("operation", "list")
    try:
        if operation == "list":
            return imagery.catalogue()
        if operation == "search":
            return imagery.search(params.get("site_id"), params.get("bbox"),
                                  params.get("limit", 30), params.get("offset", 0))
        if operation == "view":
            if not params.get("site_id") or not params.get("frame_id"):
                return {"error": "view requires site_id and frame_id from search"}
            kwargs = {key: params[key] for key in ("yaw", "pitch", "hfov", "width", "height") if key in params}
            data, metadata = imagery.render_view(params["site_id"], params["frame_id"], **kwargs)
            return PerspectiveResult(data, metadata)
        return {"error": "operation must be list, search or view"}
    except ValueError as exc:
        return {"error": str(exc)}
    except (OSError, KeyError):
        return {"error": "Published imagery is unavailable; check configured read-only assets"}


PROCESSOR = {
    "title": "Inspect georeferenced street imagery in perspective",
    "summary": "Find published panorama camera positions and inspect perspective viewpoints, with image-relative rays and explicit grounding limits.",
    "guide": """Use operation='list' to discover configured imagery sites and extents.
Use operation='search', optionally site_id or bbox=[west,south,east,north] (EPSG:4326),
to find GPS camera positions and dates. Paginate with offset/limit; keep returned frame ids.
Use operation='view' with site_id and frame_id to SEE a perspective image rather than
a flattened panorama. Specify yaw (-180..180), pitch (-85..85), hfov (30..120), and
width/height (default 1536x1024, maximum 2048 per side / 3 megapixels). Repeat with
different yaw to look around. This is a direct image result, not a background job.

Yaw is relative to the panorama's centre, not north. Unknown absolute heading remains
null. GPS travel course MUST NOT be substituted for camera heading. Metadata gives
camera GPS, acquisition time, pinhole intrinsics and rotation/pixel-to-ray formula.
These establish which camera and viewing direction supplied visual evidence, but
NO depth or ground intersection exists: do not claim precise object coordinates or
distances from image pixels. Camera altitude and scene display heights are not a
calibrated vertical datum. Redaction/stitching can hide or distort objects.
Compare adjacent viewpoints and existing map layers to corroborate observations;
cite the site, frame id, timestamp and viewing parameters, retaining uncertainties.
""",
    "schema": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "operation": {"type": "string", "enum": ["list", "search", "view"], "default": "list"},
            "site_id": {"type": "string"}, "frame_id": {"type": "string"},
            "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 30},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "yaw": {"type": "number", "minimum": -180, "maximum": 180, "default": 0},
            "pitch": {"type": "number", "minimum": -85, "maximum": 85, "default": 0},
            "hfov": {"type": "number", "minimum": 30, "maximum": 120, "default": 90},
            "width": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 1536},
            "height": {"type": "integer", "minimum": 64, "maximum": 2048, "default": 1024},
        },
    },
    "run": run,
}
