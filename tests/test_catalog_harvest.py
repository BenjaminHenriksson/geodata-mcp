from unittest.mock import MagicMock, Mock

import pytest

from connectors import ogcapi, stac, wfs, wmts


@pytest.mark.parametrize("handler", [wfs.harvest_wfs, wfs.harvest_wms,
                                     wmts.harvest_wmts, ogcapi.harvest_ogcapi,
                                     stac.harvest_stac])
@pytest.mark.parametrize("source, error", [
    (None, "unknown source_id: missing"),
    ({"slug": "fixture", "url": None}, "source fixture has no url"),
])
def test_source_errors(handler, source, error):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = source
    with pytest.raises(ValueError, match=error):
        handler(conn, {"payload": {"source_id": "missing"}})


@pytest.mark.parametrize("module", [ogcapi, stac])
@pytest.mark.parametrize("box, expected", [
    (None, None), ([], None), ([17, 60, 18], None),
    ([17, 60, 18, 61], (17, 60, 18, 61)),
    ([17, 60, 0, 18, 61, 100], (17, 60, 18, 61)),
    ([-180, -90, 180, 90], None),
])
def test_collection_bounding_boxes(monkeypatch, module, box, expected):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = {
        "id": "source", "slug": "fixture", "url": "https://example.test"
    }
    get = Mock(return_value={"collections": [
        {"id": "first", "extent": {"spatial": {"bbox": [box] if box else []}}}
    ]})
    upsert = Mock()
    monkeypatch.setattr(module, "_get_json", get)
    monkeypatch.setattr(module, "_upsert_dataset", upsert)
    handler = module.harvest_ogcapi if module is ogcapi else module.harvest_stac
    assert handler(conn, {"payload": {"source_id": "source"}}) == {"datasets": 1}
    assert upsert.call_args.args[8] == expected
    if module is stac:
        raw = ([17, 60, 18, 61] if box and len(box) == 6
               else box if box and len(box) >= 4 else None)
        assert upsert.call_args.kwargs["schema_summary"]["stac"]["bbox_4326"] == raw
    get.assert_called_once_with("https://example.test/collections",
                                {"f": "json"} if module is ogcapi else None)
