"""Worker entrypoint: FastAPI on 0.0.0.0:8100 (/embed, /healthz) plus the job
loop in a daemon thread started via the FastAPI lifespan."""

import logging
import sys
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import embedder
import jobs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("worker.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    thread = threading.Thread(target=jobs.run_forever, name="job-loop", daemon=True)
    thread.start()
    log.info("worker started: job loop thread running, serving on :8100")
    yield


app = FastAPI(title="geodata-worker", lifespan=lifespan)


class EmbedRequest(BaseModel):
    texts: list[str]
    task: str = "document"


@app.post("/embed")
def embed(req: EmbedRequest):
    """Embed texts with the local model. First call may take a long time while
    the model downloads/loads; runs in the threadpool so the event loop (and
    /healthz) stays responsive."""
    if req.task not in ("query", "document"):
        raise HTTPException(status_code=400, detail="task must be 'query' or 'document'")
    if not req.texts:
        return {"embeddings": []}
    return {"embeddings": embedder.embed_texts(req.texts, req.task)}


@app.get("/healthz")
async def healthz():
    """Liveness; reports model state WITHOUT triggering a load."""
    return {"ok": True, "model_loaded": embedder.is_loaded()}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8100, log_level="info")
