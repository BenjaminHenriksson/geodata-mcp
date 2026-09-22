"""Registry entry for document/image inspection; implementation lives in the worker."""
import job_ops
from geodata_common import inspection


def run(workspace_id, params):
    try:
        payload = inspection.validate(params)
    except ValueError as exc:
        return {"error": str(exc)}
    return job_ops.submit("inspect", payload, workspace_id,
                          "inspection running — wait with analyze(op='status', job_id=..., timeout_s=25)")


PROCESSOR = {
    "title": "Transcribe or inspect PDFs, scanned documents and images",
    "summary": "Read a PDF/image URL: mode='transcribe' returns full page text for you to reason over; mode='answer' answers a question from the original page images. Both include page citations.",
    "guide": """Choose how to read a public PDF or image using its direct file URL.
Use this after internet search to actually read a linked PDF/image; snippets and
URLs alone are not visual evidence. An HTML page is not an image: find its direct
PDF/image URL first. No interactive browser or website screenshotting is provided.

mode='transcribe': return the full text of each selected page, in its original
language, without a summary or question answering. Omit question. Use this when
you need the source wording, numbers or qualifications to answer the user yourself.
Native PDF text/tables are extracted directly; scans and raster images use Gemma
OCR. Each page has text, text_method (native or gemma_ocr), uncertainties and a source URL.
It is a transcription attempt, not a guarantee of character-perfect OCR.

mode='answer' (default): send rendered original pages to Gemma with your question.
Use this for diagrams, maps, photographs or to inspect unclear source text visually.
Even PDFs with native text are rendered in this mode. Returns per-page answer,
evidence and uncertainties in the question's language. It does not automatically
transcribe the full page first. Read all page answers before drawing conclusions.

pages selects 1-based PDF pages or image frames (PNG/JPEG/WebP/TIFF/etc.), defaults
to ALL. Results state which pages were read, plus the source hash and model usage.
Both modes persist results in the workspace's job history: keep the job_id and use
analyze(op='status', job_id=...) to retrieve the same text/findings without another
model call. A new run reads the source URL again. Failed or truncated model responses
fail the job; they are not interpreted as empty/no-evidence pages.

The worker sends rendered images/text to paid Gemma with detail=high, up to four
requests concurrently and up to 16,384 output tokens per page. Rendering targets
200 dpi (maximum 3,200 pixels per side); the provider controls visual token allocation.
PDF/image input is capped at 100 MiB. Public HTTP(S) URLs only, no credentials or
tailnet/private addresses. This is document interpretation, not georeferencing or
verified measurements. It does not create a map layer or index a source automatically.

For reusable searchable PDF text, use load(op='register', kind='pdf', url=..., title=...)
then load(op='ingest', dataset_id=...). That path shares extraction and automatically
OCRs pages with little native text. Inspect findings stay in the workspace's job history.
""",
    "schema": {
        "type": "object", "required": ["url"], "additionalProperties": False,
        "properties": {
            "url": {"type": "string", "description": "Direct public HTTP(S) URL to a PDF or raster image; not a web page."},
            "mode": {"type": "string", "enum": ["answer", "transcribe"], "default": "answer",
                     "description": "transcribe: full original-language page text, no summary. answer: visual question answering from original pages."},
            "question": {"type": "string", "minLength": 1,
                         "description": "For answer mode: what to find or explain, including the desired language. Omit in transcribe mode."},
            "pages": {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 1,
                      "description": "Optional 1-based PDF pages or image frames. Omit to inspect the entire document."},
        },
    },
    "run": run,
}
