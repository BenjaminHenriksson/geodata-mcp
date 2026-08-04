"""Text/HTML ingestion: download → strip markup (stdlib HTMLParser, no new
deps) → chunk like the PDF pipeline → doc.documents + embedded doc.chunks.

Unlike ingest_pdf, re-ingesting the same URL replaces the prior document row
(delete-then-insert on source_url) instead of accumulating duplicates — the
job loop retries handlers after the mid-job commit, and a page re-ingested
after an edit should supersede its old chunks, not sit beside them."""

import logging
import os
from html.parser import HTMLParser

from psycopg.types.json import Json

import dbutil
import embedder
from connectors import files
from connectors.pdf import CHUNK_OVERLAP, CHUNK_SIZE, MIN_TEXT_CHARS, _chunk_pages

log = logging.getLogger("worker.textdoc")

TEXT_DOWNLOAD_CAP = 20 * 1024 * 1024  # 20 MB
TEXT_TIMEOUT = 60.0

_SKIP_TAGS = {"script", "style", "noscript", "template", "head", "svg"}
_BLOCK_TAGS = {"p", "div", "section", "article", "li", "tr", "table", "br",
               "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "blockquote",
               "header", "footer", "nav", "main", "aside", "pre", "figure"}


class _TextExtractor(HTMLParser):
    """Visible-text extractor: drops script/style subtrees, breaks lines at
    block-level tags, collapses runs of whitespace later."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0 and data:
            self.parts.append(data)


def _looks_like_html(text: str) -> bool:
    head = text.lstrip()[:300].lower()
    return head.startswith("<!doctype") or "<html" in head or "<body" in head \
        or "<div" in head or "<p>" in head


def _to_plain_text(raw: str) -> tuple[str, str]:
    """Returns (plain_text, detected_format)."""
    if _looks_like_html(raw):
        parser = _TextExtractor()
        parser.feed(raw)
        parser.close()
        raw, fmt = "".join(parser.parts), "html"
    else:
        fmt = "text"
    lines = [" ".join(line.split()) for line in raw.splitlines()]
    out = []
    blank = 0
    for line in lines:
        if line:
            out.append(line)
            blank = 0
        else:
            blank += 1
            if blank == 1:
                out.append("")
    return "\n".join(out).strip(), fmt


def ingest_text(conn, job) -> dict:
    """Job handler: {dataset_id?, url, title} → doc.documents + embedded
    doc.chunks, replacing any prior document for the same source_url."""
    payload = job["payload"]
    url = payload.get("url")
    if not url:
        raise ValueError("ingest_text payload requires url")
    title = payload.get("title") or url
    dataset_id = payload.get("dataset_id")

    tmp_path = f"/tmp/ingest_text_{job['id']}"
    try:
        size = files.download(url, tmp_path, TEXT_DOWNLOAD_CAP, timeout=TEXT_TIMEOUT)
        log.info("downloaded %s (%d bytes)", url, size)
        with open(tmp_path, encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    text, fmt = _to_plain_text(raw)
    chunks = _chunk_pages([(None, text)], size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)

    with conn.cursor() as cur:
        cur.execute("DELETE FROM doc.documents WHERE source_url = %s", (url,))
        cur.execute(
            """
            INSERT INTO doc.documents (dataset_id, source_url, title, pages, meta)
            VALUES (%s::uuid, %s, %s, NULL, %s)
            RETURNING id
            """,
            (str(dataset_id) if dataset_id else None, url, title,
             Json({"format": fmt, "chars": len(text)})),
        )
        document_id = cur.fetchone()["id"]
        chunk_ids = []
        for index, (_page, chunk_text) in enumerate(chunks):
            cur.execute(
                """
                INSERT INTO doc.chunks (document_id, page, chunk_index, text)
                VALUES (%s, NULL, %s, %s)
                RETURNING id
                """,
                (document_id, index, chunk_text),
            )
            chunk_ids.append(cur.fetchone()["id"])
    conn.commit()  # document + chunks exist even if embedding fails below

    batch = embedder.BATCH_SIZE
    for offset in range(0, len(chunks), batch):
        chunk_batch = chunks[offset:offset + batch]
        id_batch = chunk_ids[offset:offset + batch]
        vecs = embedder.embed_texts([t for _, t in chunk_batch], "document")
        with conn.cursor() as cur:
            cur.executemany(
                """
                UPDATE doc.chunks
                   SET embedding = %s::vector, embedding_model = %s
                 WHERE id = %s
                """,
                [(dbutil.vector_literal(vec), embedder.EMBED_MODEL, chunk_id)
                 for vec, chunk_id in zip(vecs, id_batch)],
            )
        conn.commit()

    result = {"document_id": str(document_id), "chunks": len(chunks),
              "chars": len(text), "format": fmt}
    if len(text) < MIN_TEXT_CHARS:
        result["warning"] = "very little visible text extracted from this page"
    return result
