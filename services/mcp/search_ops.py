"""Implementation of the search tool: hybrid trigram + vector search over the catalog."""

import httpx
from psycopg.rows import dict_row

import config
import db
import geometry
import provenance

TRGM_THRESHOLD = 0.05
CHUNK_LIMIT = 8


def embed_query(query: str) -> list[float] | None:
    """POST the query to the embed service; None on any failure (silent fallback)."""
    try:
        resp = httpx.post(
            config.EMBED_URL,
            json={"texts": [query], "task": "query"},
            timeout=6.0,
        )
        resp.raise_for_status()
        embeddings = resp.json().get("embeddings")
        if embeddings and len(embeddings[0]) == config.EMBED_DIM:
            return embeddings[0]
    except Exception:
        pass
    return None


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


def dataset_detail(dataset_id: str) -> dict:
    """Full dataset row (minus embedding) + source + provenance + document row when relevant."""
    with db.app_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """SELECT d.id, d.source_id, d.external_id, d.kind, d.title, d.description,
                          d.keywords, d.crs_native, d.schema_summary,
                          ST_AsText(d.extent_3014) AS extent_3014_wkt,
                          d.feature_count, d.ref_table, d.embedding_model,
                          d.created_at, d.updated_at
                     FROM catalog.datasets d WHERE d.id = %s""",
                (dataset_id,),
            )
            ds = cur.fetchone()
            if ds is None:
                return {"error": f"no dataset with id {dataset_id!r} — search by query first"}
            cur.execute(
                """SELECT id, slug, kind, url, title, description, license, attribution,
                          trust, auth_note, added_by, created_at
                     FROM catalog.sources WHERE id = %s""",
                (ds["source_id"],),
            )
            source = cur.fetchone()
            document = None
            if ds["kind"] == "document":
                cur.execute(
                    """SELECT id, source_url, title, pages, fetched_at, meta
                         FROM doc.documents WHERE dataset_id = %s""",
                    (dataset_id,),
                )
                document = cur.fetchone()
    prov = provenance.for_object(ds["ref_table"], limit=20) if ds["ref_table"] else []
    out = {
        "dataset": geometry.jsonable_row(ds),
        "source": geometry.jsonable_row(source) if source else None,
        "provenance": [geometry.jsonable_row(p) for p in prov],
    }
    if document is not None:
        out["document"] = geometry.jsonable_row(document)
    return out


def _merge_ranked(arms: list[list[dict]], key: str) -> dict:
    """Normalize each arm's rank to a score in (0,1]; keep the best score per item."""
    best: dict = {}
    for arm in arms:
        n = len(arm)
        for i, item in enumerate(arm):
            score = (n - i) / n
            prev = best.get(item[key])
            if prev is None or score > prev["score"]:
                item = dict(item)
                item["score"] = round(score, 4)
                best[item[key]] = item
    return best


def _set_trgm_threshold(cur) -> None:
    """Align the `%` operator with TRGM_THRESHOLD.

    The queries filter with `text % query` so the GIN trigram indexes are actually used
    (`similarity(...) > x` is not an indexable predicate and forces a seq scan). But `%`
    tests against pg_trgm.similarity_threshold, which defaults to 0.3 — far stricter than
    our 0.05 — so it must be set or the index would silently change what search returns.
    """
    # SET takes no bind parameters; the value is our own float constant, not user input.
    cur.execute(f"SET pg_trgm.similarity_threshold = {float(TRGM_THRESHOLD)}")


def _dataset_arms(query: str, kind: str | None, limit: int, vec: list[float] | None):
    fetch = max(limit * 2, 20)
    kind_sql = " AND d.kind = %(kind)s" if kind else ""
    params = {"q": query, "kind": kind, "n": fetch, "thr": TRGM_THRESHOLD}
    trgm_sql = (
        """SELECT d.id::text AS id, d.title, d.kind, d.external_id, d.description, d.ref_table,
                  s.slug AS source_slug
             FROM catalog.datasets d JOIN catalog.sources s ON s.id = d.source_id
            WHERE (d.title || ' ' || d.description) %% %(q)s
               AND similarity(d.title || ' ' || d.description, %(q)s) > %(thr)s"""
        + kind_sql
        + " ORDER BY similarity(d.title || ' ' || d.description, %(q)s) DESC LIMIT %(n)s"
    )
    arms = []
    with db.app_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            _set_trgm_threshold(cur)
            cur.execute(trgm_sql, params)
            arms.append(cur.fetchall())
            if vec is not None:
                vec_sql = (
                    """SELECT d.id::text AS id, d.title, d.kind, d.external_id, d.description, d.ref_table,
                              s.slug AS source_slug
                         FROM catalog.datasets d JOIN catalog.sources s ON s.id = d.source_id
                        WHERE d.embedding IS NOT NULL"""
                    + kind_sql
                    + " ORDER BY d.embedding <=> %(vec)s::vector LIMIT %(n)s"
                )
                cur.execute(vec_sql, {**params, "vec": _vector_literal(vec)})
                arms.append(cur.fetchall())
    return arms


def _chunk_arms(query: str, vec: list[float] | None):
    params = {"q": query, "n": CHUNK_LIMIT * 2, "thr": TRGM_THRESHOLD}
    arms = []
    with db.app_pool().connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            _set_trgm_threshold(cur)
            cur.execute(
                """SELECT c.id, c.page, c.chunk_index, c.text,
                          d.title AS document_title, d.source_url, d.id::text AS document_id
                     FROM doc.chunks c JOIN doc.documents d ON d.id = c.document_id
                    WHERE c.text %% %(q)s
                      AND similarity(c.text, %(q)s) > %(thr)s
                    ORDER BY similarity(c.text, %(q)s) DESC LIMIT %(n)s""",
                params,
            )
            arms.append(cur.fetchall())
            if vec is not None:
                cur.execute(
                    """SELECT c.id, c.page, c.chunk_index, c.text,
                              d.title AS document_title, d.source_url, d.id::text AS document_id
                         FROM doc.chunks c JOIN doc.documents d ON d.id = c.document_id
                        WHERE c.embedding IS NOT NULL
                        ORDER BY c.embedding <=> %(vec)s::vector LIMIT %(n)s""",
                    {**params, "vec": _vector_literal(vec)},
                )
                arms.append(cur.fetchall())
    return arms


def hybrid_search(query: str, kind: str | None, limit: int) -> dict:
    vec = embed_query(query)
    ds_best = _merge_ranked(_dataset_arms(query, kind, limit, vec), key="id")
    datasets = sorted(ds_best.values(), key=lambda d: d["score"], reverse=True)[:limit]
    for d in datasets:
        d["description"] = geometry.truncate_text(d["description"] or "")

    ch_best = _merge_ranked(_chunk_arms(query, vec), key="id")
    chunks = sorted(ch_best.values(), key=lambda c: c["score"], reverse=True)[:CHUNK_LIMIT]
    for c in chunks:
        c["text"] = geometry.truncate_text(c["text"])

    return {
        "datasets": [geometry.jsonable_row(d) for d in datasets],
        "chunks": [geometry.jsonable_row(c) for c in chunks],
        "embedding_used": vec is not None,
    }
