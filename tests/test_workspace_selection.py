from unittest.mock import MagicMock, Mock

import pytest

import sessions
import server

WS_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def principal(monkeypatch):
    conn = MagicMock()
    pool = MagicMock()
    pool.connection.return_value.__enter__.return_value = conn
    monkeypatch.setattr(sessions.db, "app_pool", lambda: pool)
    monkeypatch.setattr(sessions, "raw_bearer_from_context", lambda _: "fixture")
    monkeypatch.setattr(sessions, "principal_id_from_raw", lambda _: "key")
    fallback = Mock()
    monkeypatch.setattr(sessions, "get_or_create_workspace", fallback)
    return conn, fallback


def test_explicit_selection_does_not_activate(principal):
    conn, fallback = principal
    conn.execute.return_value.fetchone.return_value = (WS_ID, "analysis", "ws_12345678", False)
    workspace = sessions.resolve(None, WS_ID)
    assert (workspace.id, workspace.api_key_id, workspace.is_active) == (WS_ID, "key", False)
    query, params = conn.execute.call_args.args
    assert "WHERE id = %s AND api_key_id = %s" in query
    assert "SET last_used" in query and "SET is_active" not in query
    assert params == (WS_ID, "key")
    fallback.assert_not_called()


@pytest.mark.parametrize("selector", ["", "not-a-uuid", WS_ID])
def test_invalid_or_unowned_selection_never_falls_back(principal, selector):
    conn, fallback = principal
    conn.execute.return_value.fetchone.return_value = None
    with pytest.raises(sessions.AuthError):
        sessions.resolve(None, selector)
    fallback.assert_not_called()


def test_omitting_selector_keeps_legacy_default(principal):
    conn, fallback = principal
    conn.execute.return_value.fetchone.return_value = (WS_ID, "default", "ws_12345678")
    assert sessions.resolve(None).is_active
    assert "AND is_active" in conn.execute.call_args.args[0]
    fallback.assert_not_called()


def test_all_eight_tools_accept_optional_selector():
    import inspect
    for name in ("workspace", "search", "load", "query", "layer", "map", "analyze", "export"):
        parameter = inspect.signature(getattr(server, name)).parameters["workspace_id"]
        assert parameter.default is None
