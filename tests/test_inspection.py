"""Exercise real PDF rendering plus the model/download and job boundaries."""
from pathlib import Path
from unittest.mock import MagicMock, Mock

import httpx
import pytest
from PIL import Image, ImageDraw
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

import analysis_ops
import inspection_ops
from connectors import gemma_api, inspect_document, pdf, public_download, visual_documents


@pytest.fixture
def mixed_pdf(tmp_path):
    path = tmp_path / "mixed.pdf"
    scan = Image.new("RGB", (1000, 500), "white")
    ImageDraw.Draw(scan).text((30, 50), "Byggstart 2019. Invigning 2022.", fill="black", font_size=40)
    doc = canvas.Canvas(str(path))
    for i in range(10):
        doc.drawString(40, 760 - i * 20, "Native source text with an opening date of 2018.")
    doc.showPage()
    doc.drawImage(ImageReader(scan), 40, 400, width=500, height=250)
    doc.showPage()
    doc.save()
    return path


def model_reply(request):
    assert request["max_tokens"] == 16384
    assert request["response_format"]["type"] == "json_schema"
    schema = request["response_format"]["json_schema"]
    assert schema["strict"] and schema["schema"]["additionalProperties"] is False
    assert "uncertainties" in schema["schema"]["required"]
    parts = request["messages"][0]["content"]
    assert parts[1]["image_url"]["detail"] == "high"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    values = {"text": "Byggstart 2019. Invigning 2022.", "answer": "Bygget startade 2019.",
              "evidence": ["Byggstart 2019"], "uncertainties": []}
    return {key: values[key] for key in schema["schema"]["required"]}, {"prompt_tokens": 100, "completion_tokens": 30, "cost": .001}


def test_scanned_pages_use_ocr_and_native_pages_do_not(monkeypatch, mixed_pdf):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")
    call = Mock(side_effect=lambda client, key, request: model_reply(request))
    monkeypatch.setattr(gemma_api, "stream_json", call)
    result = visual_documents.extract(mixed_pdf)
    assert result["selected_pages"] == [1, 2]
    assert call.call_count == 1
    assert result["pages"][0]["text_method"] == "native"
    assert "2018" in result["pages"][0]["text"]
    assert result["pages"][1]["text_method"] == "gemma_ocr"
    assert "2019" in result["pages"][1]["text"]


def test_inspection_sees_native_page_visuals_and_preserves_selection(monkeypatch, mixed_pdf):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")
    call = Mock(side_effect=lambda client, key, request: model_reply(request))
    monkeypatch.setattr(gemma_api, "stream_json", call)
    result = visual_documents.extract(mixed_pdf, pages=[1], question="Vad visar bilden?")
    assert result["total_pages"] == 2 and result["selected_pages"] == [1]
    assert call.call_count == 1
    assert result["pages"][0]["page"] == 1
    assert result["pages"][0]["answer"] == "Bygget startade 2019."
    assert "2018" in result["pages"][0]["text"]
    with pytest.raises(ValueError, match="outside"):
        visual_documents.extract(mixed_pdf, pages=[3], question="Read it")


def test_native_pdf_needs_no_key(monkeypatch, mixed_pdf):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = visual_documents.extract(mixed_pdf, pages=[1])
    assert result["model"] is None
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        visual_documents.extract(mixed_pdf, pages=[2])


@pytest.mark.parametrize("reply", [None, {}, {"text": "made up", "uncertainties": "bad"}])
def test_invalid_ocr_never_indexes_an_empty_document(monkeypatch, mixed_pdf, reply):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")
    monkeypatch.setattr(gemma_api, "stream_json", lambda *args: (reply, {}))
    monkeypatch.setattr(pdf.files, "download", lambda url, path, *a, **kw: Path(path).write_bytes(mixed_pdf.read_bytes()))
    store = Mock()
    monkeypatch.setattr(pdf, "store_document", store)
    with pytest.raises(RuntimeError, match="page 2"):
        pdf.ingest_pdf(MagicMock(), {"id": "bad-ocr", "payload": {"url": "https://example.test/scan.pdf"}})
    store.assert_not_called()


def test_ingestion_indexes_ocr_with_page_metadata(monkeypatch, mixed_pdf):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")
    monkeypatch.setattr(gemma_api, "stream_json", lambda c, k, r: model_reply(r))
    monkeypatch.setattr(pdf.files, "download", lambda url, path, *a, **kw: Path(path).write_bytes(mixed_pdf.read_bytes()))
    store = Mock(return_value={"document_id": "fixture", "chunks": 1})
    monkeypatch.setattr(pdf, "store_document", store)
    result = pdf.ingest_pdf(MagicMock(), {"id": "good-ocr", "payload": {"url": "https://example.test/scan.pdf"}})
    assert result["ocr_pages"] == [2]
    assert "Byggstart 2019" in " ".join(text for _, text in store.call_args.args[2])
    assert store.call_args.kwargs["meta"]["model"]["requests"] == 1


def test_image_inspection_returns_citations_and_provenance(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture")
    monkeypatch.setattr(gemma_api, "stream_json", lambda c, k, r: model_reply(r))
    def download(url, path):
        Image.new("RGB", (300, 200), "white").save(path, format="PNG")
        return url, path.stat().st_size
    monkeypatch.setattr(public_download, "download", download)
    provenance = Mock()
    monkeypatch.setattr(inspect_document.dbutil, "insert_provenance", provenance)
    result = inspect_document.inspect_document(MagicMock(), {
        "id": 2, "workspace_id": "owned-workspace", "payload": {"url": "https://example.test/picture.png"}})
    assert result["result_type"] == "inspection" and result["format"] == "image"
    assert result["pages"][0]["source_url"] == "https://example.test/picture.png"
    assert "text" not in result["pages"][0]
    assert len(result["source_sha256"]) == 64
    assert provenance.call_args.kwargs["workspace_id"] == "owned-workspace"


@pytest.mark.parametrize("params", [
    {"url": "file:///etc/passwd"}, {"url": "https://user:password@x.test/a"},
    {"url": "http://x.test:8100/image"}, {"url": "https://x.test/a", "pages": [True]},
    {"url": "https://x.test/a", "pages": [0]}, {"url": "https://x.test/a", "question": ""},
    {"url": "https://x.test/a", "unknown": 1},
])
def test_bad_inspection_arguments_do_not_enqueue(monkeypatch, params):
    submit = Mock()
    monkeypatch.setattr(inspection_ops.job_ops, "submit", submit)
    assert "error" in analysis_ops.run("workspace", "inspect", params)
    submit.assert_not_called()


def test_inspection_discovery_and_workspace_submission(monkeypatch):
    submit = Mock(return_value={"job_id": 8})
    monkeypatch.setattr(inspection_ops.job_ops, "submit", submit)
    assert "inspect" in {p["id"] for p in analysis_ops.list_processors()["processors"]}
    assert analysis_ops.describe("inspect")["params_schema"]["required"] == ["url"]
    assert analysis_ops.run("owned", "inspect", {"url": "https://x.test/a", "pages": [2, 1, 2]}) == {"job_id": 8}
    assert submit.call_args.args[1]["pages"] == [1, 2]
    assert submit.call_args.args[2] == "owned"


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "100.64.0.9", "169.254.169.254", "::1"])
def test_public_download_rejects_internal_addresses(monkeypatch, tmp_path, address):
    monkeypatch.setattr(public_download.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", (address, 443))])
    with pytest.raises(ValueError, match="public internet"):
        public_download.download("https://example.test/image", tmp_path / "download")


def test_redirects_are_rechecked_and_connection_uses_resolved_ip(monkeypatch, tmp_path):
    resolves = iter(["93.184.216.34", "127.0.0.1"])
    monkeypatch.setattr(public_download.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", (next(resolves), 443))])
    def handle(request):
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "example.test"
        assert request.extensions["sni_hostname"] == "example.test"
        assert "authorization" not in request.headers
        return httpx.Response(302, headers={"Location": "https://internal.test/private"})
    client_class = httpx.Client
    monkeypatch.setattr(public_download.httpx, "Client", lambda **kw: client_class(transport=httpx.MockTransport(handle), **kw))
    with pytest.raises(ValueError, match="public internet"):
        public_download.download("https://example.test/image", tmp_path / "download")


def test_oversized_download_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(public_download, "MAX_BYTES", 4)
    monkeypatch.setattr(public_download.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", ("93.184.216.34", 443))])
    client_class = httpx.Client
    monkeypatch.setattr(public_download.httpx, "Client", lambda **kw: client_class(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"12345")), **kw))
    with pytest.raises(ValueError, match="exceeds"):
        public_download.download("https://example.test/image", tmp_path / "download")


def test_html_url_does_not_get_treated_as_an_image(tmp_path):
    path = tmp_path / "page"
    path.write_text("<html><body>not an image</body></html>")
    with pytest.raises(ValueError, match="direct file URL"):
        visual_documents.extract(path, question="What is here?")
