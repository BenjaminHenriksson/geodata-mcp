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


def embed_rows(conn, rows, *, catalog=False) -> int:
    """Embed catalog records or document chunks, committing each completed batch."""
    table = "catalog.datasets" if catalog else "doc.chunks"
    updated_at = ", updated_at = now()" if catalog else ""
    for offset in range(0, len(rows), BATCH_SIZE):
        batch = rows[offset:offset + BATCH_SIZE]
        texts = [_dataset_text(row) if catalog else row["text"] for row in batch]
        vecs = embed_texts(texts, "document")
        with conn.cursor() as cur:
            cur.executemany(
                f"""UPDATE {table}
                       SET embedding = %s::vector, embedding_model = %s{updated_at}
                     WHERE id = %s""",
                [(dbutil.vector_literal(vec), EMBED_MODEL, row["id"])
                 for row, vec in zip(batch, vecs)],
            )
        conn.commit()
    return len(rows)


def embed_catalog(conn, job) -> dict:
    """Embed missing or model-mismatched catalog records and document chunks."""
    counts = {}
    for table, columns, order in (
        ("catalog.datasets", "id, title, description, keywords", "created_at"),
        ("doc.chunks", "id, text", "id"),
    ):
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT {columns} FROM {table}
                     WHERE embedding IS NULL OR embedding_model IS DISTINCT FROM %s
                     ORDER BY {order}""",
                (EMBED_MODEL,),
            )
            rows = cur.fetchall()
        counts[table.split(".")[1] + "_embedded"] = embed_rows(
            conn, rows, catalog=table == "catalog.datasets")
    return counts
