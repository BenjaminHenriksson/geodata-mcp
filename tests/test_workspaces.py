from unittest.mock import MagicMock

import pytest

from geodata_common import workspaces

WS_ID = "00000000-0000-0000-0000-000000000001"
ROW = (WS_ID, "example", "ws_12345678", True)


@pytest.mark.parametrize("operation", ["activate", "rename", "delete"])
def test_workspace_mutations_recheck_ownership(operation):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = None
    args = {"activate": (), "rename": ("new-name",), "delete": (ROW[2],)}[operation]
    result = getattr(workspaces, operation)(conn, "other-key", WS_ID, *args)
    assert result == {"activate": False, "rename": "unknown workspace", "delete": 0}[operation]
    queries = [str(call.args[0]) for call in conn.execute.call_args_list]
    assert not any(q.startswith(("UPDATE", "DELETE", "DROP", "Composed")) for q in queries)
    assert conn.execute.call_args_list[-1].args[1] == ("other-key", WS_ID)


def test_activation_is_atomic_and_serialized():
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = ROW
    assert workspaces.activate(conn, "key", WS_ID)
    conn.transaction.assert_called_once()
    queries = [call.args[0] for call in conn.execute.call_args_list]
    assert "pg_advisory_xact_lock" in queries[0]
    assert "api_key_id = %s" in queries[1]
    assert "is_active = false" in queries[2]
    assert "is_active = true" in queries[3]


@pytest.mark.parametrize("allow_same", [False, True])
def test_rename_preserves_each_interface_self_rename_behavior(allow_same):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = ROW
    result = workspaces.rename(conn, "key", WS_ID, "example", allow_same=allow_same)
    assert result == (None if allow_same else "a workspace named 'example' already exists")


def test_delete_rejects_mismatched_schema():
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = ROW
    with pytest.raises(ValueError, match="schema does not match"):
        workspaces.delete(conn, "key", WS_ID, "ws_87654321")


def test_delete_keeps_history_and_returns_deleted_map_count():
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = ROW
    conn.execute.return_value.fetchall.return_value = [("v_one",), ("v_two",)]
    assert workspaces.delete(conn, "key", WS_ID, ROW[2]) == 2
    queries = [str(call.args[0]) for call in conn.execute.call_args_list]
    assert any("DROP SCHEMA IF EXISTS" in q for q in queries)
    assert any("DELETE FROM app.workspaces" in q for q in queries)
    assert not any("DELETE FROM app.provenance" in q or "DELETE FROM app.query_log" in q for q in queries)
