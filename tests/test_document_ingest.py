from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

import embedder
from connectors import pdf, textdoc


@pytest.fixture
def document_db():
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.side_effect = [{"id": 7}] + [{"id": n} for n in range(100, 165)]
    return conn, cur


@pytest.mark.parametrize("kind", ["pdf", "text"])
@pytest.mark.parametrize("count, fail_batch", [(0, None), (1, None), (65, None), (65, 2)])
def test_ingestion_preserves_documents_and_embedding_progress(
    monkeypatch, document_db, kind, count, fail_batch
):
    conn, cur = document_db
    module = pdf if kind == "pdf" else textdoc
    content = "Visible content. " * 20 if count else ""
    chunks = [(1 if kind == "pdf" else None, f"chunk {i}") for i in range(count)]
    downloads = []

    def download(url, path, cap, timeout):
        Path(path).write_text(content)
        downloads.append(path)
        return len(content)

    monkeypatch.setattr(module.files, "download", download)
    monkeypatch.setattr(module, "_chunk_pages", lambda *a, **kw: chunks)
    monkeypatch.setattr(pdf, "_extract_pages", lambda _: ([(1, content), (2, "")], [2]))
    batches = []

    def embed(texts, task):
        assert task == "document"
        assert conn.commit.call_count == len(batches) + 1
        batches.append(texts)
        if len(batches) == fail_batch:
            raise RuntimeError("embedding unavailable")
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(embedder, "embed_texts", embed)
    job = {"id": f"regression-{kind}", "payload": {"url": "https://example.test/doc"}}
    if fail_batch:
        with pytest.raises(RuntimeError, match="embedding unavailable"):
            getattr(module, f"ingest_{kind}")(conn, job)
    else:
        result = getattr(module, f"ingest_{kind}")(conn, job)
        expected = {"document_id": "7", "chunks": count}
        if kind == "text":
            expected.update(chars=len(content.strip()), format="text")
        if not count:
            expected["warning"] = (
                "scanned PDF — no text layer; OCR model deferred by decision"
                if kind == "pdf" else "very little visible text extracted from this page"
            )
        assert result == expected

    calls = [(" ".join(c.args[0].split()), c.args[1]) for c in cur.execute.call_args_list]
    deletes = [p for sql, p in calls if sql.startswith("DELETE")]
    assert deletes == ([(job["payload"]["url"],)] if kind == "text" else [])
    document = next(p for sql, p in calls if sql.startswith("INSERT INTO doc.documents"))
    assert document[:3] == (None, job["payload"]["url"], job["payload"]["url"])
    assert document[-1].obj == (
        {"empty_pages": [2]} if kind == "pdf"
        else {"format": "text", "chars": len(content.strip())}
    )
    if kind == "pdf":
        assert document[3] == 2
    inserted = [p for sql, p in calls if sql.startswith("INSERT INTO doc.chunks")]
    assert len(inserted) == count
    # Older text ingestion uses literal SQL NULL; the shared path binds None.
    normalized = [p if len(p) == 4 else (p[0], None, p[1], p[2]) for p in inserted]
    assert normalized == [(7, page, i, text) for i, (page, text) in enumerate(chunks)]
    expected_batches = [chunks[i:i + 32] for i in range(0, count, 32)]
    if fail_batch:
        expected_batches = expected_batches[:fail_batch]
    assert batches == [[text for _, text in batch] for batch in expected_batches]
    completed = len(batches) - bool(fail_batch)
    assert conn.commit.call_count == 1 + completed
    assert cur.executemany.call_count == completed
    updates = [row for call in cur.executemany.call_args_list for row in call.args[1]]
    assert updates == [("[1.0,0.0]", embedder.EMBED_MODEL, i)
                       for i in range(100, 100 + min(count, completed * 32))]
    assert all(not Path(path).exists() for path in downloads)


@pytest.mark.parametrize("kind", ["pdf", "text"])
def test_download_failure_removes_partial_file(monkeypatch, document_db, kind):
    module = pdf if kind == "pdf" else textdoc
    paths = []

    def download(url, path, *a, **kw):
        Path(path).write_text("partial")
        paths.append(path)
        raise RuntimeError("download failed")

    monkeypatch.setattr(module.files, "download", download)
    conn, cur = document_db
    with pytest.raises(RuntimeError, match="download failed"):
        getattr(module, f"ingest_{kind}")(
            conn, {"id": f"failure-{kind}", "payload": {"url": "https://example.test/doc"}}
        )
    cur.execute.assert_not_called()
    conn.commit.assert_not_called()
    assert all(not Path(path).exists() for path in paths)


@pytest.mark.parametrize("empty", [False, True])
def test_catalog_refresh_preserves_queries_batches_and_timestamps(monkeypatch, empty):
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    datasets = [] if empty else [{"id": i, "title": " Title ", "description": " Desc ",
                                 "keywords": ["geo", "data"]} for i in range(33)]
    chunks = [] if empty else [{"id": 100, "text": "body"}]
    cur.fetchall.side_effect = [datasets, chunks]
    embed = Mock(side_effect=lambda texts, task: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(embedder, "embed_texts", embed)
    assert embedder.embed_catalog(conn, {}) == {
        "datasets_embedded": len(datasets), "chunks_embedded": len(chunks)
    }
    queries = [" ".join(c.args[0].split()) for c in cur.execute.call_args_list]
    assert queries == [
        "SELECT id, title, description, keywords FROM catalog.datasets "
        "WHERE embedding IS NULL OR embedding_model IS DISTINCT FROM %s ORDER BY created_at",
        "SELECT id, text FROM doc.chunks "
        "WHERE embedding IS NULL OR embedding_model IS DISTINCT FROM %s ORDER BY id",
    ]
    assert all(c.args[1] == (embedder.EMBED_MODEL,) for c in cur.execute.call_args_list)
    assert conn.commit.call_count == (0 if empty else 3)
    if not empty:
        assert [c.args for c in embed.call_args_list] == [
            (["Title. Desc geo data"] * 32, "document"),
            (["Title. Desc geo data"], "document"), (["body"], "document"),
        ]
        updates = cur.executemany.call_args_list
        assert all("updated_at = now()" in c.args[0] for c in updates[:2])
        assert "updated_at" not in updates[2].args[0]
        assert [row[2] for c in updates for row in c.args[1]] == list(range(33)) + [100]
