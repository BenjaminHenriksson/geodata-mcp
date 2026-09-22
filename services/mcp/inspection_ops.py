"""Registry entry for document/image inspection; implementation lives in the worker."""
import job_ops
from geodata_common import inspection


def run(workspace_id, params):
    try:
        payload = inspection.validate(params)
    except ValueError as exc:
        return {"error": str(exc)}
    return job_ops.submit("inspect", payload, workspace_id,
                          "inspection running — poll analyze(op='status', job_id=...)")


PROCESSOR = {
    "title": "Inspect PDFs, scanned documents and images",
    "summary": "Read a PDF or image URL with Gemma, including scanned pages, diagrams and photographs. Returns cited findings, not map layers.",
    "guide": """Ask questions about a public PDF or image using its direct file URL.
Use this after internet search to actually read a linked PDF/image; snippets and
URLs alone are not visual evidence. An HTML page is not an image: find its direct
PDF/image URL first. No interactive browser or website screenshotting is provided.

Native PDF text and tables are extracted; selected pages are also rendered for
Gemma to inspect diagrams, maps and scanned text. Images (PNG/JPEG/WebP/TIFF/etc.)
are read visually. pages selects 1-based PDF pages or image frames, defaults to ALL.
Findings explicitly state which pages were examined; unselected pages were not read.
Answer language follows the question. Return values include per-page answers,
evidence, uncertainties and source URLs, plus the source hash and model usage.
Read every page answer before drawing cross-page conclusions. Failed or truncated
model responses fail the job; they are not interpreted as empty/no-evidence pages.

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
            "question": {"type": "string", "minLength": 1, "default": inspection.DEFAULT_QUESTION,
                         "description": "What to find or explain. Include the desired language; ask for transcription if needed."},
            "pages": {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 1,
                      "description": "Optional 1-based PDF pages or image frames. Omit to inspect the entire document."},
        },
    },
    "run": run,
}
