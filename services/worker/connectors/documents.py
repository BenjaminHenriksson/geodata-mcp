"""Document storage shared by PDF and text ingestion."""

from psycopg.types.json import Json

import embedder


def store_document(conn, payload, chunks, *, pages=None, meta=None, replace=False):
    """Persist text before embedding, then commit embedding progress per batch."""
    url = payload["url"]
    dataset_id = payload.get("dataset_id")
    with conn.cursor() as cur:
        if replace:
            cur.execute("DELETE FROM doc.documents WHERE source_url = %s", (url,))
        cur.execute(
            """INSERT INTO doc.documents (dataset_id, source_url, title, pages, meta)
               VALUES (%s::uuid, %s, %s, %s, %s) RETURNING id""",
            (str(dataset_id) if dataset_id else None, url, payload.get("title") or url,
             pages, Json(meta)),
        )
        document_id = cur.fetchone()["id"]
        rows = []
        for index, (page, text) in enumerate(chunks):
            cur.execute(
                """INSERT INTO doc.chunks (document_id, page, chunk_index, text)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (document_id, page, index, text),
            )
            rows.append({"id": cur.fetchone()["id"], "text": text})
    conn.commit()  # Preserve the document even if embedding fails.
    embedder.embed_rows(conn, rows)
    return {"document_id": str(document_id), "chunks": len(chunks)}
