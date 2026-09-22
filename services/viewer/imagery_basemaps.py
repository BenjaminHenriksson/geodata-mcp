"""Read-only Stockholm demonstration backgrounds from the city's WMS services."""

import math

import httpx

BOUNDS = [17.73540698970735, 59.20019773730185, 18.26459301029265, 59.46949251212496]
BASE = "https://kartor.stockholm.se/bios/wms/app/baggis/web/"
SOURCES = {
    "map": ("WMS_STHLM_STOCKHOLMSKARTA_GRA_FORENKLAD", "p_1002770"),
    "ortho2024": ("WMS_STHLM_ORTOFOTO_2024", "p_1003240"),
}
ATTRIBUTION = '© Stockholms stad · <a href="https://openstreetgs.stockholm.se/home/Guide/TutQGis_SE" target="_blank" rel="noopener">Kartkälla och information</a>'


def description():
    return {"bounds": BOUNDS, "attribution": ATTRIBUTION,
            "layers": [{"id": "map", "label": "Stockholmskarta"}, {"id": "ortho2024", "label": "Ortofoto 2024"}]}


def tile_bounds(z, x, y):
    if not 0 <= z <= 20 or not 0 <= x < 2**z or not 0 <= y < 2**z:
        raise ValueError("Invalid tile coordinate")
    half = math.pi * 6378137
    step = 2 * half / 2**z
    return [x * step - half, half - (y + 1) * step, (x + 1) * step - half, half - y * step]


def fetch_tile(kind, z, x, y):
    if kind not in SOURCES:
        raise ValueError("Unknown demonstration background")
    bounds = tile_bounds(z, x, y)
    service, layer = SOURCES[kind]
    response = httpx.get(BASE + service, params={
        "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetMap", "LAYERS": layer,
        "STYLES": "", "SRS": "EPSG:3857", "BBOX": ",".join(str(v) for v in bounds),
        "WIDTH": 256, "HEIGHT": 256, "FORMAT": "image/png", "TRANSPARENT": "TRUE",
    }, timeout=15, follow_redirects=False)
    response.raise_for_status()
    if not response.content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Background service did not return an image")
    return response.content
