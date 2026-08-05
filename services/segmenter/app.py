"""Standalone SAM3 segmenter: FastAPI on 0.0.0.0:8200 (/segment, /healthz).

Runs natively on the host — MLX cannot run in Linux containers — and the worker
reaches it via SAM3_URL (default http://host.docker.internal:8200). The HTTP
contract is the compatibility seam between backends: nothing else in the system
knows whether mlx or transformers is running. See README.md.
"""

import base64
import io
import logging
import os
import sys
import threading

import uvicorn
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel

import backends

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("segmenter.app")

app = FastAPI(title="geodata-segmenter")

_lock = threading.Lock()
_backend = None


def _get_backend():
    """Load the backend/model once, guarded by a lock; concurrent first
    requests queue behind the load."""
    global _backend
    if _backend is None:
        with _lock:
            if _backend is None:
                _backend = backends.get_backend()
    return _backend


class SegmentRequest(BaseModel):
    image_png_b64: str
    concepts: list[str]
    threshold: float = 0.5


@app.post("/segment")
def segment(req: SegmentRequest):
    """Concept segmentation. First call loads the model (mlx: downloads
    ~3.4 GB once); runs in the threadpool so /healthz stays responsive."""
    concepts = [c.strip() for c in req.concepts]
    if not concepts or not all(concepts):
        raise HTTPException(status_code=422, detail="concepts must be 1+ non-empty strings")
    if not 0.0 <= req.threshold <= 1.0:
        raise HTTPException(status_code=422, detail="threshold must be in [0, 1]")
    try:
        raw = base64.b64decode(req.image_png_b64, validate=True)
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception:
        raise HTTPException(status_code=422, detail="image_png_b64 is not a valid base64-encoded image")
    width, height = image.size
    if image.mode != "RGB":
        image = image.convert("RGB")

    try:
        backend = _get_backend()
    except backends.BackendUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))

    detections = []
    for det in backend.segment(image, concepts, req.threshold):
        buf = io.BytesIO()
        det["mask"].save(buf, format="PNG")
        detections.append(
            {
                "concept": det["concept"],
                "score": det["score"],
                "box": det["box"],
                "mask_png_b64": base64.b64encode(buf.getvalue()).decode("ascii"),
            }
        )
    return {
        "width": width,
        "height": height,
        "model": backend.model_info(),
        "detections": detections,
    }


@app.get("/healthz")
async def healthz():
    """Liveness; reports backend + model state WITHOUT triggering a load."""
    return {
        "ok": True,
        "backend": os.environ.get("SAM3_BACKEND", "mlx").strip().lower(),
        "model_loaded": _backend is not None,
    }


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("SAM3_PORT", "8200")),
        log_level="info",
    )
