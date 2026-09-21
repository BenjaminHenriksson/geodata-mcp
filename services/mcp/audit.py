"""Append-only, principal-attributed MCP call audit.

Persist the start before dispatch. A separate completion record leaves interrupted
calls visible and preserves starts without granting UPDATE on either ledger.
Only argument names, explicit SQL, and selected result metadata are retained.
"""
import inspect
import logging
import time
import uuid
from contextvars import ContextVar
from functools import wraps

import sessions

import db

log = logging.getLogger(__name__)
_current = ContextVar("mcp_audit", default=None)


def current_workspace():
    state = _current.get()
    return state["workspace"] if state else None


def attribution():
    state = _current.get()
    return (state["workspace"].api_key_id, state["call_id"]) if state else (None, None)


def _target(workspace, tool_name, arguments):
    if tool_name == "workspace" and arguments.get("op") in {"use", "rename", "delete", "new"}:
        with db.app_pool().connection() as conn:
            row = conn.execute(
                "SELECT id::text, name FROM app.workspaces WHERE api_key_id = %s AND name = %s",
                (workspace.api_key_id, arguments.get("name")),
            ).fetchone()
        if row:
            return row
    return workspace.id, workspace.name


def tool(resolve):
    """Keep the registered signature intact; share exactly one workspace resolution."""
    def decorate(fn):
        signature = inspect.signature(fn)

        @wraps(fn)
        def wrapped(*args, **kwargs):
            arguments = signature.bind(*args, **kwargs)
            arguments.apply_defaults()
            arguments = arguments.arguments
            try:
                workspace = resolve(arguments.get("ctx"), arguments.get("workspace_id"))
            except sessions.AuthError as exc:
                return {"error": f"auth: {str(exc).strip()}"}
            call_id = str(uuid.uuid4())
            started = time.monotonic()
            try:
                target_id, target_name = _target(workspace, fn.__name__, arguments)
                with db.app_pool().connection() as conn:
                    conn.execute(
                        """INSERT INTO app.mcp_calls
                           (call_id, api_key_id, workspace_id, workspace_name, tool_name,
                            operation, argument_keys, sql_text)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (call_id, workspace.api_key_id, target_id, target_name, fn.__name__,
                         arguments.get("op"), sorted(k for k, v in arguments.items()
                                                     if k != "ctx" and v is not None),
                         arguments.get("sql")),
                    )
            except Exception:  # noqa: BLE001 -- never execute an unaudited operation
                log.error("MCP audit start failed for tool %s", fn.__name__)
                return {"error": "Audit storage unavailable; the tool was not executed."}

            token = _current.set({"workspace": workspace, "call_id": call_id})
            try:
                try:
                    result = fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 -- record unexpected failures too
                    result = {"error": f"{fn.__name__} failed: {type(exc).__name__}"}
                try:
                    if fn.__name__ == "workspace" and not result.get("error"):
                        # A newly created workspace did not exist at call start.
                        # Schema is stable across rename and never resolves another owner.
                        with db.app_pool().connection() as conn:
                            row = conn.execute(
                                "SELECT id::text, name FROM app.workspaces "
                                "WHERE api_key_id = %s AND ws_schema = %s",
                                (workspace.api_key_id, result.get("ws_schema")),
                            ).fetchone()
                        if row:
                            target_id, target_name = row
                    with db.app_pool().connection() as conn:
                        conn.execute(
                            """INSERT INTO app.mcp_call_results
                               (call_id, workspace_id, workspace_name, status, duration_ms,
                                query_id, job_id, row_count, error)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            (call_id, target_id, target_name,
                             "error" if result.get("error") else "success",
                             int((time.monotonic() - started) * 1000),
                             result.get("query_id"), result.get("job_id", arguments.get("job_id")),
                             result.get("row_count", result.get("rows_updated")),
                             # Error details already live in query_log/provenance/jobs.
                             # Avoid copying arbitrary source URLs or row data here.
                             "Tool returned an error; inspect the linked SQL or job."
                             if result.get("error") else None),
                        )
                except Exception:  # noqa: BLE001 -- mutation may already have committed
                    log.error("MCP audit completion failed for call %s", call_id)
                    result = dict(result, audit_warning=(
                        "Audit completion could not be saved. The tool may have completed; "
                        "inspect its result before retrying."))
                return dict(result, audit_call_id=call_id)
            finally:
                _current.reset(token)
        return wrapped
    return decorate
