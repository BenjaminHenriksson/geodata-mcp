"""PDF ingestion: download → native text/tables or Gemma OCR per page → chunk →
doc.documents + doc.chunks → embed chunks with the local model."""

import logging
import os

from connectors import files
from connectors.visual_documents import extract
from connectors.documents import store_document

log = logging.getLogger("worker.pdf")

PDF_DOWNLOAD_CAP = 100 * 1024 * 1024  # 100 MB
PDF_TIMEOUT = 60.0
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150
MIN_TEXT_CHARS = 200


def _chunk_pages(pages, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Split at whitespace with overlap, keeping each chunk on its cited page."""
    chunks = []
    for number, text in pages:
        start = 0
        total = len(text)
        while start < total:
            end = min(start + size, total)
            cut = end
            if end < total:
                window = text[start:end]
                split_at = max(window.rfind(" "), window.rfind("\n"), window.rfind("\t"))
                if split_at > size // 2:
                    cut = start + split_at
            piece = text[start:cut].strip()
            if piece:
                chunks.append((number, piece))
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

    tmp_path = f"/tmp/ingest_pdf_{job['id']}.pdf"
    try:
        size = files.download(url, tmp_path, PDF_DOWNLOAD_CAP, timeout=PDF_TIMEOUT)
        log.info("downloaded PDF %s (%d bytes)", url, size)
        extracted = extract(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    pages = [(page["page"], page["text"]) for page in extracted["pages"]]
    chunks = _chunk_pages(pages)
    if not chunks:
        raise ValueError("No readable text found in this PDF, including OCR; no empty document was indexed")
    meta = {"empty_pages": [n for n, text in pages if not text.strip()],
            "ocr_pages": [p["page"] for p in extracted["pages"] if p["text_method"] == "gemma_ocr"],
            "page_uncertainties": [{"page": p["page"], "uncertainties": p["uncertainties"]}
                                   for p in extracted["pages"] if p["uncertainties"]],
            "model": extracted["model"]}
    result = store_document(conn, payload, chunks, pages=extracted["total_pages"], meta=meta)
    result.update(ocr_pages=meta["ocr_pages"], empty_pages=meta["empty_pages"])
    if meta["page_uncertainties"]:
        result["warnings"] = meta["page_uncertainties"]
    return result
