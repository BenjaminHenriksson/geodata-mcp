"""The inspect analysis processor: URL → visual findings with source references."""
import hashlib
import tempfile
from pathlib import Path
from urllib.parse import urldefrag

import dbutil
from connectors import public_download, visual_documents
from geodata_common.inspection import validate


def inspect_document(conn, job):
    params = validate(job["payload"])
    with tempfile.TemporaryDirectory(prefix="inspect-") as directory:
        path = Path(directory) / "source"
        resolved, size = public_download.download(params["url"], path)
        with path.open("rb") as source:
            hasher = hashlib.sha256()
            for chunk in iter(lambda: source.read(65536), b""):
                hasher.update(chunk)
            digest = hasher.hexdigest()
        result = visual_documents.extract(path, pages=params["pages"], question=params["question"])
    for page in result["pages"]:
        page["source_url"] = (urldefrag(resolved)[0] + f"#page={page['page']}"
                              if result["format"] == "pdf" else resolved)
        # Inspection answers carry evidence. Full OCR belongs in searchable ingestion;
        # avoid duplicating the entire page alongside each answer in chat context.
        page.pop("text")
        page.pop("text_method")
        page["method"] = "gemma_vision"
    result.update(result_type="inspection", source_url=params["url"], resolved_url=resolved,
                  source_sha256=digest, bytes=size, question=params["question"],
                  note="Findings are model interpretations. Cite the page source URLs; use load to index the document for later search.")
    with conn.cursor() as cur:
        dbutil.insert_provenance(cur, kind="inspect", object_ref=f"job:{job['id']}",
                                 workspace_id=job["workspace_id"], job_id=job["id"],
                                 details={k: result[k] for k in ("source_url", "source_sha256", "selected_pages", "model")})
    return result
