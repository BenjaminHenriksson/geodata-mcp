"""Read-only map traceability. Capability data and owner-only audit stay separate."""
import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from psycopg import sql
from psycopg.rows import dict_row

import dbq

# Only recorded, map-relevant fields are returned. Never raw SQL, request bodies,
# credentials, actor IDs, internal paths, or arbitrary job/provenance dictionaries.
DETAIL_KEYS = frozenset({
    "backend", "method", "geometry_kind", "concepts", "threshold", "min_area_m2",
    "proc_gsd", "wms_gsd", "collections", "source_kinds", "season_note", "tile_counts",
    "model", "detail", "max_output_tokens", "requests", "request_attempts",
    "concurrency", "elapsed_seconds", "tile_failures", "complete", "reconciliation",
    "raw_candidates", "candidates", "reported_usage_only", "usage_cumulative",
    "prompt_tokens", "completion_tokens", "cost", "image_token_budget",
    "reason", "attempts", "analyzed", "error", "cancelled", "missing_a", "missing_b",
    "a", "b", "collection", "datetime_min", "datetime_max", "months", "gsd",
    "source_url", "resolved_url", "source_sha256", "selected_pages", "total_pages",
    "mode", "result_type", "bytes", "format", "warning", "warnings", "temporal",
    "ocr_pages", "empty_pages", "page_uncertainties", "page", "uncertainties",
    "tiles_analyzed", "tiles_skipped", "appeared", "disappeared", "changed",
    "vintage_a", "vintage_b", "source_version", "document_id",
})
FEATURE_KEYS = frozenset({
    "fid", "id", "name", "title", "concept", "change_class", "change_type",
    "confidence_label", "confidence_a", "confidence_b", "before_description",
    "after_description", "evidence", "geometry_kind", "area_m2", "vintage_a",
    "vintage_b", "datetime_a", "datetime_b", "source_tiles", "observation_count",
    "merge_method", "observations", "review_required", "conflicting_observations", "tile_id", "status", "gsd_m",
    "source_url", "document_url", "source_title", "document_title", "document_id",
    "page", "page_number", "source_version", "source_sha256", "uncertainties",
    "warnings", "warning", "source_type", "decision_id", "permit_id",
    "citation", "citations", "acquired_at", "captured_at",
})
OBSERVATION_KEYS = frozenset({
    "tile_id", "bounds_3006", "clipped_edges", "concept", "change_type",
    "confidence_label", "before", "after", "evidence",
})
CITATION_KEYS = frozenset({
    "source_url", "document_url", "url", "title", "document_title",
    "document_id", "page", "page_number", "version", "source_version", "quote",
})


PUBLIC_QUERY_KEYS = frozenset({
    "id", "documentid", "document_id", "docid", "fileid", "file_id",
    "version", "page", "service", "request", "layers", "typenames",
})


def safe_url(value):
    """External links: keep public document/service identifiers, never credentials."""
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return None
        # Do not expose even the hostname from a credential-bearing URL.
        if parts.username is not None or parts.password is not None:
            return None
        fragment = parts.fragment if parts.fragment.startswith("page=") and parts.fragment[5:].isdigit() else ""
        query = urlencode([(k, v) for k, v in parse_qsl(parts.query, max_num_fields=100)
                           if k.lower() in PUBLIC_QUERY_KEYS and len(v) <= 500])
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, fragment))
    except ValueError:
        return None


def _clean(value, keys=DETAIL_KEYS, depth=0):
    if depth > 7:
        return None
    if isinstance(value, dict):
        return {k: _clean(v, keys, depth + 1) for k, v in value.items()
                if k in keys and v is not None
                and (k != "model" or isinstance(v, dict))}
    if isinstance(value, list):
        return [_clean(v, keys, depth + 1) for v in value[:100]]
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            return safe_url(value)
        return value[:4000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:100]


def clean_details(value):
    result = _clean(value) if isinstance(value, dict) else {}
    # Tile IDs are dynamic keys; keep only the documented, sanitized fields.
    failures = value.get("tile_failures", {}) if isinstance(value, dict) else {}
    if isinstance(failures, dict) and "tile_failures" in value:
        result["tile_failures"] = {
            str(k)[:100]: _clean(v) for k, v in list(failures.items())[:100]
            if isinstance(v, dict)
        } if failures else {}
    model = value.get("model") if isinstance(value, dict) else None
    if isinstance(model, dict) and isinstance(model.get("tile_failures"), dict):
        result.setdefault("model", {})["tile_failures"] = clean_details(model)["tile_failures"]
    return result


def feature_details(properties):
    """Structured evidence also works when MVT encodes JSON properties as text."""
    result = {}
    for key, value in properties.items():
        if key not in FEATURE_KEYS or value is None:
            continue
        if key in ("observations", "conflicting_observations", "source_tiles", "uncertainties", "warnings", "citations") and isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        if key in ("source_url", "document_url"):
            value = safe_url(value)
        elif key in ("observations", "conflicting_observations"):
            value = _clean(value, OBSERVATION_KEYS)
        elif key in ("citation", "citations"):
            value = _clean(value, CITATION_KEYS)
        else:
            value = _clean(value, FEATURE_KEYS)
        if value is not None:
            result[key] = value
    return result


def _rows(conn, query, params=()):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _stamp(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def map_details(conn, view, *, owner=False):
    entries = [e for e in view["spec"].get("layers", []) if isinstance(e, dict) and isinstance(e.get("ref"), str)]
    refs = list(dict.fromkeys(e["ref"] for e in entries))
    shared = [ref for ref in refs if ref.startswith("ref.") and dbq.split_layer_ref(ref)]
    sources = _rows(conn, """
        SELECT d.id::text, d.ref_table, d.title, d.external_id, d.updated_at,
               d.schema_summary, s.title AS source_title, s.kind, s.url, s.attribution
        FROM catalog.datasets d JOIN catalog.sources s ON s.id = d.source_id
        WHERE d.ref_table = ANY(%s::text[]) OR ('wms:' || d.id::text) = ANY(%s::text[])
        ORDER BY d.title LIMIT 100
    """, (refs, refs))
    provenance = _rows(conn, """
        SELECT id, ts, kind, object_ref, input_tables, job_id, details
        FROM app.provenance
        WHERE object_ref = ANY(%s::text[])
          AND (workspace_id = %s OR (workspace_id IS NULL AND object_ref = ANY(%s::text[])))
        ORDER BY ts DESC, id DESC LIMIT 51
    """, (refs, view.get("workspace_id"), shared))
    jobs_ids = sorted({p["job_id"] for p in provenance[:50] if p.get("job_id") is not None})
    jobs = _rows(conn, """
        SELECT id, kind, status, attempts, created_at, started_at, finished_at, result
        FROM app.jobs WHERE workspace_id = %s AND id = ANY(%s::bigint[])
        ORDER BY id DESC LIMIT 50
    """, (view.get("workspace_id"), jobs_ids)) if jobs_ids else []
    layers = []
    for ref in refs:
        entry = next(e for e in entries if e["ref"] == ref)
        layers.append({"ref": ref, "label": str(entry.get("label") or ref)})
    result = {
        "view_id": view["view_id"], "title": view.get("title") or "", "version": view["version"],
        "layers": layers,
        "sources": [{
            "ref": row["ref_table"] if row["ref_table"] in refs else "wms:" + row["id"],
            "title": row["title"], "source_title": row["source_title"], "kind": row["kind"],
            "url": safe_url(row["url"]), "attribution": row["attribution"],
            "catalog_updated_at": _stamp(row["updated_at"]),
            "dates": clean_details(row.get("schema_summary", {})),
        } for row in sources],
        "processing": [{
            "id": p["id"], "at": _stamp(p["ts"]), "kind": p["kind"],
            "ref": p["object_ref"], "inputs": [r for r in p.get("input_tables") or [] if r in refs],
            "job_id": p.get("job_id"), "details": clean_details(p.get("details", {})),
        } for p in provenance[:50]],
        "processing_more": len(provenance) > 50,
        "jobs": [{
            **{k: _stamp(job.get(k)) for k in ("id", "kind", "status", "attempts", "created_at", "started_at", "finished_at")},
            "details": clean_details(job.get("result", {})),
        } for job in jobs],
        "audit": {"access": "owner" if owner else "owner_login_required", "events": []},
    }
    if owner:
        events = _rows(conn, """
            SELECT 'sql' AS kind, q.query_id::text AS id, q.ts,
                   CASE WHEN q.error IS NULL THEN 'success' ELSE 'error' END AS status,
                   'query' AS action, q.duration_ms, q.row_count, NULL::bigint AS job_id
            FROM app.query_log q
            WHERE q.workspace_id = %s AND q.referenced_tables && %s::text[]
            UNION ALL
            SELECT 'mcp', c.call_id::text, c.ts, COALESCE(r.status, 'incomplete'),
                   c.tool_name || COALESCE(' / ' || c.operation, ''), r.duration_ms, r.row_count, r.job_id
            FROM app.mcp_calls c LEFT JOIN app.mcp_call_results r USING(call_id)
            WHERE COALESCE(r.workspace_id, c.workspace_id) = %s AND r.job_id = ANY(%s::bigint[])
            ORDER BY ts DESC LIMIT 26
        """, (view.get("workspace_id"), refs, view.get("workspace_id"), jobs_ids))
        result["audit"].update(
            events=[{k: _stamp(v) for k, v in row.items()} for row in events[:25]],
            more=len(events) > 25,
            workspace_url="/workspaces/" + str(view["workspace_id"]) + "#audit",
        )
    return result


def feature_record(conn, schema, table, columns, key, identity):
    if key not in ("fid", "id") or key not in columns:
        return None
    props = [column for column in columns if column in FEATURE_KEYS]
    if not props:
        return None
    conn.execute("SET LOCAL statement_timeout = '5s'")
    query = sql.SQL("SELECT {props} FROM {table} WHERE {key}::text = %s LIMIT 1").format(
        props=sql.SQL(", ").join(map(sql.Identifier, props)),
        table=sql.Identifier(schema, table), key=sql.Identifier(key))
    rows = _rows(conn, query, (identity,))
    if not rows:
        return None
    result = feature_details(rows[0])
    # Resolve only a document ID actually stored on the authorized feature.
    if result.get("document_id"):
        documents = _rows(conn, "SELECT id::text, title, source_url FROM doc.documents WHERE id::text = %s",
                          (str(result["document_id"]),))
        if documents:
            result.setdefault("document_title", documents[0]["title"])
            if safe_url(documents[0]["source_url"]):
                result.setdefault("document_url", safe_url(documents[0]["source_url"]))
    return result
