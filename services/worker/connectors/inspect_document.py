"""The inspect processor: URL → page transcription or visual answers with citations."""
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
        if params["mode"] == "answer":
            # Answers carry evidence; transcription mode returns the full page text.
            page.pop("text")
            page.pop("text_method")
            page["method"] = "vision_vision"
    transcribe = params["mode"] == "transcribe"
    result.update(result_type="transcription" if transcribe else "inspection",
                  mode=params["mode"], source_url=params["url"], resolved_url=resolved,
                  source_sha256=digest, bytes=size,
                  note=("Full text of the selected pages, without a summary or question answering. "
                        "OCR may contain reading errors; use answer mode on the original pages to inspect unclear text or visuals. "
                        if transcribe else "Findings are model interpretations. ") +
                       "Cite the page source URLs. Retrieve this saved result again with analyze status and the same job_id; use load to index the document for search.")
    if not transcribe:
        result["question"] = params["question"]
    with conn.cursor() as cur:
        dbutil.insert_provenance(cur, kind="inspect", object_ref=f"job:{job['id']}",
                                 workspace_id=job["workspace_id"], job_id=job["id"],
                                 details={k: result[k] for k in ("mode", "source_url", "source_sha256", "selected_pages", "model")})
    return result
