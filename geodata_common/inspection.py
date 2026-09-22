"""Inspection arguments shared by the MCP registry and worker."""
from urllib.parse import urlsplit

DEFAULT_QUESTION = "Describe this document or image, including its visible text and important visual details."


def validate(params):
    url = params.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url must be a direct HTTP(S) URL to a PDF or image")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise ValueError("invalid URL") from None
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("url must be HTTP(S), without embedded credentials")
    if port not in (None, 80, 443):
        raise ValueError("inspection supports public web URLs on ports 80 and 443")
    mode = params.get("mode", "answer")
    if mode not in ("answer", "transcribe"):
        raise ValueError("mode must be 'answer' or 'transcribe'")
    if mode == "transcribe":
        if params.get("question") is not None:
            raise ValueError("transcribe returns full text without answering a question; omit question or use mode='answer'")
        question = None
    else:
        question = params.get("question", DEFAULT_QUESTION)
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be non-empty text")
        question = question.strip()
    pages = params.get("pages")
    if isinstance(pages, list):
        # Some tool clients serialize numbers inside generic params as strings.
        pages = [int(p) if isinstance(p, str) and p.isascii() and p.isdecimal() else p
                 for p in pages]
    if pages is not None and (not isinstance(pages, list) or not pages or
                             any(type(p) is not int or p < 1 for p in pages)):
        raise ValueError("pages must be a non-empty list of 1-based page numbers")
    return {"url": url.strip(), "mode": mode, "question": question,
            "pages": sorted(set(pages)) if pages is not None else None}
