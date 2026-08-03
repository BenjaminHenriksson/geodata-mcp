"""PDF ingestion: download → pdfplumber text + tables per page → chunk →
doc.documents + doc.chunks → embed chunks with the local model."""

import bisect
import logging
import os

from psycopg.types.json import Json

import dbutil
import embedder
from connectors import files

log = logging.getLogger("worker.pdf")

PDF_DOWNLOAD_CAP = 100 * 1024 * 1024  # 100 MB
PDF_TIMEOUT = 60.0
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150
MIN_TEXT_CHARS = 200


def _table_to_markdown(table) -> str:
    """Render a pdfplumber table (list of rows of cells) as a markdown grid."""
    rows = []
    for raw_row in table or []:
        cells = []
        for cell in raw_row or []:
            text = "" if cell is None else str(cell)
            cells.append(text.replace("\n", " ").replace("|", "\\|").strip())
        rows.append(cells)
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |",
             "| " + " | ".join(["---"] * width) + " |"]
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _extract_pages(path: str):
    """Returns (pages, empty_pages): pages = [(page_no, text)] with tables
    appended to each page's text as markdown grids."""
    import pdfplumber

    pages = []
    empty_pages = []
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:  # tolerate broken pages
                log.warning("page %d: extract_text failed: %s", number, exc)
                text = ""
            parts = [text]
            try:
                tables = page.extract_tables() or []
            except Exception as exc:
                log.warning("page %d: extract_tables failed: %s", number, exc)
                tables = []
            for table in tables:
                markdown = _table_to_markdown(table)
                if markdown:
                    parts.append(markdown)
            page_text = "\n\n".join(p for p in parts if p.strip())
            if not page_text.strip():
                empty_pages.append(number)
            pages.append((number, page_text))
    return pages, empty_pages


def _chunk_pages(pages, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Chunk the concatenated page texts (~size chars, overlap, split at
    whitespace). Returns [(page_no, chunk_text)] with the page holding each
    chunk's start offset."""
    page_starts = []
    page_numbers = []
    parts = []
    pos = 0
    for number, text in pages:
        if not text:
            continue
        page_starts.append(pos)
        page_numbers.append(number)
        parts.append(text)
        pos += len(text) + 2  # the "\n\n" joiner
    joined = "\n\n".join(parts)

    chunks = []
    start = 0
    total = len(joined)
    while start < total:
        end = min(start + size, total)
        cut = end
        if end < total:
            window = joined[start:end]
            split_at = max(window.rfind(" "), window.rfind("\n"), window.rfind("\t"))
            if split_at > size // 2:
                cut = start + split_at
        piece = joined[start:cut].strip()
        if piece:
            idx = bisect.bisect_right(page_starts, start) - 1
            page_no = page_numbers[idx] if idx >= 0 else None
            chunks.append((page_no, piece))
        if cut >= total:
            break
        next_start = cut - overlap
        start = next_start if next_start > start else cut
    return chunks


def ingest_pdf(conn, job) -> dict:
    """Job handler: {dataset_id?, url, title} → doc.documents + embedded
    doc.chunks."""
    payload = job["payload"]
    url = payload.get("url")
    if not url:
        raise ValueError("ingest_pdf payload requires url")
    title = payload.get("title") or url
    dataset_id = payload.get("dataset_id")

    tmp_path = f"/tmp/ingest_pdf_{job['id']}.pdf"
    try:
        size = files.download(url, tmp_path, PDF_DOWNLOAD_CAP, timeout=PDF_TIMEOUT)
        log.info("downloaded PDF %s (%d bytes)", url, size)
        pages, empty_pages = _extract_pages(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    total_chars = sum(len(text) for _, text in pages)
    chunks = _chunk_pages(pages)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO doc.documents (dataset_id, source_url, title, pages, meta)
            VALUES (%s::uuid, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                str(dataset_id) if dataset_id else None,
                url,
                title,
                len(pages),
                Json({"empty_pages": empty_pages}),
            ),
        )
        document_id = cur.fetchone()["id"]

        chunk_ids = []
        for index, (page_no, text) in enumerate(chunks):
            cur.execute(
                """
                INSERT INTO doc.chunks (document_id, page, chunk_index, text)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (document_id, page_no, index, text),
            )
            chunk_ids.append(cur.fetchone()["id"])
    conn.commit()  # document + chunks exist even if embedding fails below

    # Embed the chunks with the local model (task 'document'), batch 32.
    batch = embedder.BATCH_SIZE
    for offset in range(0, len(chunks), batch):
        chunk_batch = chunks[offset : offset + batch]
        id_batch = chunk_ids[offset : offset + batch]
        vecs = embedder.embed_texts([text for _, text in chunk_batch], "document")
        with conn.cursor() as cur:
            cur.executemany(
                """
                UPDATE doc.chunks
                   SET embedding = %s::vector, embedding_model = %s
                 WHERE id = %s
                """,
                [
                    (dbutil.vector_literal(vec), embedder.EMBED_MODEL, chunk_id)
                    for vec, chunk_id in zip(vecs, id_batch)
                ],
            )
        conn.commit()

    result = {"document_id": str(document_id), "chunks": len(chunks)}
    if total_chars < MIN_TEXT_CHARS:
        result["warning"] = "scanned PDF — no text layer; OCR model deferred by decision"
    return result
