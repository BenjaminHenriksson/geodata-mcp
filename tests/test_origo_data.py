import json
from unittest.mock import MagicMock

import dbq
import main as viewer
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def data_api(monkeypatch):
    monkeypatch.setattr(dbq, "get_pool", MagicMock())
    monkeypatch.setattr(viewer, "_checked_layer", lambda *args: ("ref", "buildings", ["fid", "name", "unused"]))
    monkeypatch.setattr(dbq, "feature_count", lambda *args: 20258)
    query = MagicMock(return_value='{"type":"FeatureCollection","features":[]}')
    monkeypatch.setattr(dbq, "geojson_feature_collection", query)
    return TestClient(viewer.app), query


def test_ordered_pages_select_only_existing_requested_properties(data_api):
    client, query = data_api
    result = client.get("/data/ref.buildings.geojson", params={
        "view": "fixture", "limit": 5000, "offset": 20000, "crs": 3014,
        "properties": json.dumps(["name", "fid", "geom", "missing", 'x"; DROP TABLE ref.buildings;--']),
    })
    assert result.status_code == 200
    assert query.call_args.args[3:] == (["fid", "name"], 3014, 5000, True)
    assert query.call_args.kwargs == {"offset": 20000, "order_by": "fid"}


@pytest.mark.parametrize("properties", ["invalid", "{}", '"name"', "[1]", "null"])
def test_invalid_property_selection_is_rejected(data_api, properties):
    client, query = data_api
    result = client.get("/data/ref.buildings.geojson", params={"view": "fixture", "properties": properties})
    assert result.status_code == 400
    query.assert_not_called()


def test_pages_still_require_map_capability(data_api, monkeypatch):
    client, query = data_api
    def denied(*args):
        raise HTTPException(403, "layer is not part of this view")
    monkeypatch.setattr(viewer, "_checked_layer", denied)
    assert client.get("/data/ref.other.geojson?view=fixture&offset=20000").status_code == 403
    query.assert_not_called()


@pytest.mark.parametrize("order_by, expected", [("fid", 'ORDER BY t."fid"'), (None, "ORDER BY t::text")])
def test_geojson_query_uses_stable_parameterized_pages(order_by, expected):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = ('{"features":[]}',)
    dbq.geojson_feature_collection(conn, "ref", "buildings", ["fid"], 3014, 5000, True,
                                   offset=20000, order_by=order_by)
    query, params = conn.execute.call_args.args
    assert expected in query.as_string()
    assert "LIMIT %s OFFSET %s" in query.as_string()
    assert params == [5000, 20000]
