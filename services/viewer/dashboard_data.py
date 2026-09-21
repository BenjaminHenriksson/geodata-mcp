"""Read models for authenticated dashboards. Scope every query at the database."""
import uuid

from psycopg.rows import dict_row


def _rows(conn, query, params=()):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return cur.fetchall()


def principal(conn, key_id):
    rows = _rows(conn, "SELECT id::text, name, is_admin FROM app.api_keys "
                 "WHERE id = %s AND NOT disabled", (key_id,))
    return rows[0] if rows else None


def overview(conn, key_id, *, admin=False, page=1):
    scope = "true" if admin else "w.api_key_id = %s"
    params = () if admin else (key_id,)
    totals = _rows(conn, f"""
        SELECT count(*) AS workspaces,
          COALESCE(sum((SELECT count(*) FROM pg_class c JOIN pg_namespace n
                       ON n.oid = c.relnamespace
                       WHERE n.nspname = w.ws_schema AND c.relkind = 'r')), 0) AS layers,
          COALESCE(sum((SELECT count(*) FROM app.map_views m
                       WHERE m.workspace_id = w.id::text)), 0) AS maps,
          COALESCE(sum((SELECT count(*) FROM app.jobs j WHERE j.workspace_id = w.id::text
                       AND j.status IN ('queued', 'running'))), 0) AS pending
        FROM app.workspaces w WHERE {scope}""", params)[0]
    rows = _rows(conn, f"""
        SELECT w.id::text, w.name, w.ws_schema, w.is_active, w.last_used,
          k.name AS owner, k.disabled,
          (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = w.ws_schema AND c.relkind = 'r') AS layers,
          (SELECT count(*) FROM app.map_views m WHERE m.workspace_id = w.id::text) AS maps,
          (SELECT count(*) FROM app.jobs j WHERE j.workspace_id = w.id::text
           AND j.status IN ('queued', 'running')) AS pending,
          (SELECT count(*) FROM app.query_log q WHERE q.workspace_id = w.id::text
           AND q.ts >= now() - interval '24 hours') AS queries,
          (SELECT count(*) FROM app.mcp_calls c LEFT JOIN app.mcp_call_results r USING(call_id)
           WHERE COALESCE(r.workspace_id, c.workspace_id) = w.id::text
           AND c.ts >= now() - interval '24 hours') AS calls
        FROM app.workspaces w JOIN app.api_keys k ON k.id = w.api_key_id
        WHERE {scope} ORDER BY w.last_used DESC, w.id LIMIT 51 OFFSET %s
        """, (*params, (page - 1) * 50))
    users = []
    catalog = None
    if admin:
        users = _rows(conn, """
            SELECT k.id::text, k.name, k.is_admin, k.disabled, k.last_used,
                   count(w.id) AS workspaces
            FROM app.api_keys k LEFT JOIN app.workspaces w ON w.api_key_id = k.id
            GROUP BY k.id ORDER BY k.created_at DESC, k.id LIMIT 51 OFFSET %s
        """, ((page - 1) * 50,))
        catalog = _rows(conn, """
            SELECT (SELECT count(*) FROM catalog.sources) AS sources,
                   (SELECT count(*) FROM catalog.datasets) AS datasets,
                   (SELECT count(*) FROM doc.documents) AS documents,
                   (SELECT count(*) FROM app.api_keys WHERE NOT disabled) AS users
        """)[0]
    return {"totals": totals, "workspaces": rows[:50], "more": len(rows) > 50,
            "users": users[:50], "users_more": len(users) > 50, "catalog": catalog}


def workspace(conn, key_id, workspace_id, *, admin=False):
    try:
        workspace_id = str(uuid.UUID(workspace_id))
    except ValueError:
        return None
    rows = _rows(conn, """
        SELECT w.id::text, w.name, w.ws_schema, w.is_active, w.created_at, w.last_used,
               k.name AS owner
        FROM app.workspaces w JOIN app.api_keys k ON k.id = w.api_key_id
        WHERE w.id = %s AND (w.api_key_id = %s OR %s)
        """, (workspace_id, key_id, admin))
    if not rows:
        return None
    result = rows[0]
    result["layers"] = _rows(conn, """
        SELECT c.relname AS name, m.notes, m.label
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN app.layer_meta m ON m.schema_name = n.nspname AND m.table_name = c.relname
        WHERE n.nspname = %s AND c.relkind = 'r' ORDER BY c.relname LIMIT 200
        """, (result["ws_schema"],))
    result["maps"] = _rows(conn, """
        SELECT view_id, title, updated_at FROM app.map_views WHERE workspace_id = %s
        ORDER BY updated_at DESC LIMIT 100""", (workspace_id,))
    result["jobs"] = _rows(conn, """
        SELECT id, kind, status, attempts, created_at, finished_at
        FROM app.jobs WHERE workspace_id = %s ORDER BY id DESC LIMIT 25""", (workspace_id,))
    result["provenance"] = _rows(conn, """
        SELECT id, ts, kind, object_ref, sql_text, input_tables, job_id
        FROM app.provenance WHERE workspace_id = %s ORDER BY ts DESC, id DESC LIMIT 25
        """, (workspace_id,))
    result["activity"] = _rows(conn, """
        SELECT count(*) AS calls, count(*) FILTER (WHERE r.status = 'error') AS errors,
               count(*) FILTER (WHERE r.call_id IS NULL) AS incomplete
        FROM app.mcp_calls c LEFT JOIN app.mcp_call_results r USING(call_id)
        WHERE COALESCE(r.workspace_id, c.workspace_id) = %s
          AND c.ts >= now() - interval '24 hours'""", (workspace_id,))[0]
    return result


def audit_events(conn, *, workspace_id=None, kind="", status="", page=1):
    """Caller must authorize the workspace, or explicitly require administrator access."""
    rows = _rows(conn, """
        WITH events AS (
          SELECT 'mcp' AS kind, c.call_id::text AS id, c.ts, c.api_key_id,
            COALESCE(r.workspace_id, c.workspace_id) AS workspace_id,
            COALESCE(r.workspace_name, c.workspace_name) AS workspace_name,
            c.tool_name || COALESCE(' / ' || c.operation, '') AS action,
            COALESCE(r.status, 'incomplete') AS status, r.duration_ms, r.row_count,
            r.query_id::text, r.job_id, c.sql_text, r.error,
            c.argument_keys AS references, c.call_id::text AS call_id
          FROM app.mcp_calls c LEFT JOIN app.mcp_call_results r USING(call_id)
          UNION ALL
          SELECT 'sql', q.query_id::text, q.ts, q.api_key_id, q.workspace_id,
            COALESCE(w.name, '(borttagen arbetsyta)'), 'query',
            CASE WHEN q.error IS NULL THEN 'success' ELSE 'error' END,
            q.duration_ms, q.row_count, q.query_id::text, NULL::bigint,
            q.sql_text, q.error, q.referenced_tables, q.mcp_call_id::text
          FROM app.query_log q LEFT JOIN app.workspaces w ON w.id::text = q.workspace_id
          UNION ALL
          SELECT 'sql', c.call_id::text, c.ts, c.api_key_id,
            COALESCE(r.workspace_id, c.workspace_id),
            COALESCE(r.workspace_name, c.workspace_name),
            c.tool_name || COALESCE(' / ' || c.operation, ''),
            COALESCE(r.status, 'incomplete'), r.duration_ms, r.row_count,
            r.query_id::text, r.job_id, c.sql_text, r.error, NULL::text[], c.call_id::text
          FROM app.mcp_calls c LEFT JOIN app.mcp_call_results r USING(call_id)
          WHERE c.sql_text IS NOT NULL AND c.tool_name <> 'query'
        )
        SELECT e.*, k.name AS actor FROM events e LEFT JOIN app.api_keys k ON k.id = e.api_key_id
        WHERE (%s::text IS NULL OR e.workspace_id = %s)
          AND (%s = '' OR e.kind = %s) AND (%s = '' OR e.status = %s)
        ORDER BY e.ts DESC, e.kind, e.id DESC LIMIT 51 OFFSET %s
        """, (workspace_id, workspace_id, kind, kind, status, status, (page - 1) * 50))
    return {"events": rows[:50], "more": len(rows) > 50}
