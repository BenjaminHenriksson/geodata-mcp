"""Read-only imagery routes scoped to an existing map's capability."""

import logging

import httpx
import imagery_basemaps
from fastapi import HTTPException, Query
from fastapi.responses import FileResponse, Response

from geodata_common import imagery

log = logging.getLogger(__name__)
IMAGE = {200: {"description": "Rendered or published image", "content": {"image/jpeg": {"schema": {"type": "string", "format": "binary"}}}}}


def register(app, require_view):
    def checked(view_id, operation, *args, **kwargs):
        require_view(view_id)
        try:
            return operation(*args, **kwargs)
        except (ValueError, OSError, KeyError) as exc:
            log.warning("Imagery request unavailable: %s", type(exc).__name__)
            raise HTTPException(status_code=404, detail="Imagery unavailable") from exc

    @app.get("/v/{view_id}/imagery/catalogue", tags=["imagery"])
    def catalogue(view_id: str):
        result = checked(view_id, imagery.catalogue)
        if result["sites"]:
            result["basemaps"] = imagery_basemaps.description()
        return result

    @app.get("/v/{view_id}/imagery/basemap/{kind}/{z}/{x}/{y}.png", tags=["imagery"])
    def background(view_id: str, kind: str, z: int, x: int, y: int):
        require_view(view_id)
        if not imagery.sites():
            raise HTTPException(404, "Imagery is not configured")
        try:
            data = imagery_basemaps.fetch_tile(kind, z, x, y)
        except ValueError as exc:
            raise HTTPException(400, "Invalid background request") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(502, "Background temporarily unavailable") from exc
        return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/v/{view_id}/imagery/{site_id}/route", tags=["imagery"])
    def route(view_id: str, site_id: str):
        return checked(view_id, imagery.route, site_id)

    @app.get("/v/{view_id}/imagery/{site_id}/scene", tags=["imagery"])
    def scene(view_id: str, site_id: str):
        result = checked(view_id, imagery.scene, site_id)
        result["dataUrl"] = f"/v/{view_id}/imagery/{site_id}/splat/lod-meta.json"
        return result

    @app.get("/v/{view_id}/imagery/{site_id}/splat/{asset:path}", tags=["imagery"])
    def splat(view_id: str, site_id: str, asset: str):
        path = checked(view_id, imagery.splat_path, site_id, asset)
        return FileResponse(path, headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})

    @app.get("/v/{view_id}/imagery/{site_id}/panorama/{frame_id}", tags=["imagery"], response_class=FileResponse)
    def panorama(view_id: str, site_id: str, frame_id: str):
        path = checked(view_id, imagery.panorama_path, site_id, frame_id)
        return FileResponse(path, headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})

    @app.get("/v/{view_id}/imagery/{site_id}/perspective/{frame_id}.jpg", tags=["imagery"], response_class=Response, responses=IMAGE)
    def perspective(view_id: str, site_id: str, frame_id: str,
                    yaw: float = Query(0, ge=-180, le=180), pitch: float = Query(0, ge=-85, le=85),
                    hfov: float = Query(90, ge=30, le=120), width: int = Query(1536, ge=64, le=2048),
                    height: int = Query(1024, ge=64, le=2048)):
        data, _ = checked(view_id, imagery.render_view, site_id, frame_id,
                          yaw=yaw, pitch=pitch, hfov=hfov, width=width, height=height)
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})
