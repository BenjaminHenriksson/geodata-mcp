"""Lazy, lock-guarded local embedding model + the embed_catalog job handler.

The SentenceTransformer loads on first use only (from /embed or from a job that
embeds); /healthz never triggers a load. First use downloads the model weights
from HuggingFace into /root/.cache/huggingface (a docker volume) — expected to
take a while once.
"""

import logging
import os
import threading

import dbutil

log = logging.getLogger("worker.embedder")

EMBED_MODEL = os.environ.get("EMBED_MODEL", "unsloth/embeddinggemma-300m")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "256"))

BATCH_SIZE = 32

_lock = threading.Lock()
_model = None


def is_loaded() -> bool:
    return _model is not None


def get_model():
    """Load the model once, guarded by a lock; safe from any thread."""
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                log.info("loading embedding model %s (truncate_dim=%d)...", EMBED_MODEL, EMBED_DIM)
                from sentence_transformers import SentenceTransformer

                model = SentenceTransformer(EMBED_MODEL, truncate_dim=EMBED_DIM)
                log.info("embedding model loaded")
                _model = model
    return _model


def embed_texts(texts, task: str):
    """Embed texts with the local model; task is 'query' or 'document'.

    Returns a list of plain float lists (length EMBED_DIM), L2-normalized.
    """
    if task not in ("query", "document"):
        raise ValueError("task must be 'query' or 'document'")
    model = get_model()
    vecs = model.encode(
        list(texts),
        prompt_name=task,
        normalize_embeddings=True,
        batch_size=BATCH_SIZE,
    )
    return [[float(x) for x in vec] for vec in vecs]


def _dataset_text(row) -> str:
    title = (row["title"] or "").strip()
    description = (row["description"] or "").strip()
    keywords = " ".join(row["keywords"] or [])
    text = title
    if description:
        text = f"{text}. {description}" if text else description
    if keywords:
        text = f"{text} {keywords}" if text else keywords
    return text or " "


def _batches(items, size=BATCH_SIZE):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def embed_catalog(conn, job) -> dict:
    """Job handler: embed catalog.datasets and doc.chunks that are missing an
    embedding or were embedded with a different model."""
    datasets_embedded = 0
    chunks_embedded = 0

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, title, description, keywords
              FROM catalog.datasets
             WHERE embedding IS NULL OR embedding_model IS DISTINCT FROM %s
             ORDER BY created_at
            """,
            (EMBED_MODEL,),
        )
        dataset_rows = cur.fetchall()

    for batch in _batches(dataset_rows):
        vecs = embed_texts([_dataset_text(r) for r in batch], "document")
        with conn.cursor() as cur:
            cur.executemany(
                """
                UPDATE catalog.datasets
                   SET embedding = %s::vector, embedding_model = %s, updated_at = now()
                 WHERE id = %s
                """,
                [
                    (dbutil.vector_literal(vec), EMBED_MODEL, row["id"])
                    for row, vec in zip(batch, vecs)
                ],
            )
        conn.commit()  # persist progress batch-by-batch
        datasets_embedded += len(batch)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, text
              FROM doc.chunks
             WHERE embedding IS NULL OR embedding_model IS DISTINCT FROM %s
             ORDER BY id
            """,
            (EMBED_MODEL,),
        )
        chunk_rows = cur.fetchall()

    for batch in _batches(chunk_rows):
        vecs = embed_texts([r["text"] for r in batch], "document")
        with conn.cursor() as cur:
            cur.executemany(
                """
                UPDATE doc.chunks
                   SET embedding = %s::vector, embedding_model = %s
                 WHERE id = %s
                """,
                [
                    (dbutil.vector_literal(vec), EMBED_MODEL, row["id"])
                    for row, vec in zip(batch, vecs)
                ],
            )
        conn.commit()
        chunks_embedded += len(batch)

    return {"datasets_embedded": datasets_embedded, "chunks_embedded": chunks_embedded}
