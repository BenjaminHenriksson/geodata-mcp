from unittest.mock import MagicMock, Mock

import pytest

from connectors import files, ogcapi, wfs


@pytest.mark.parametrize("connector", [wfs.ingest_wfs, ogcapi.ingest_ogcapi])
@pytest.mark.parametrize(
    "dataset, error",
    [
        (None, "unknown dataset_id: dataset"),
        ({"source_url": None}, "dataset's source has no url"),
    ],
)
def test_dataset_errors(connector, dataset, error):
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = dataset
    job = {
        "payload": {
            "dataset_id": "dataset",
            "target_schema": "ref",
            "table_name": "test",
        }
    }
    with pytest.raises(ValueError, match=error):
        connector(conn, job)


@pytest.mark.parametrize(
    "connector, driver", [(wfs.ingest_wfs, "WFS"), (ogcapi.ingest_ogcapi, "OAPIF")]
)
def test_ingest_preserves_driver_options_and_provenance(monkeypatch, connector, driver):
    conn = MagicMock()
    dataset = {
        "id": "dataset",
        "external_id": "buildings",
        "source_url": "https://example.test/api?token=x",
    }
    conn.cursor.return_value.__enter__.return_value.fetchone.return_value = dataset
    job = {
        "payload": {
            "dataset_id": "dataset",
            "target_schema": "ref",
            "table_name": "test",
        }
    }
    run = Mock()
    finish = Mock(return_value={"table": "ref.test", "feature_count": 2})
    monkeypatch.setattr(files, "run_ogr2ogr_with_retry", run)
    monkeypatch.setattr(files, "finalize_load", finish)
    monkeypatch.setattr(
        wfs.netauth, "gdal_env_for", lambda _: {"GDAL_HTTP_USERPWD": "test:fixture"}
    )
    assert connector(conn, job) == {"table": "ref.test", "feature_count": 2}
    args = run.call_args.args[0]
    assert args[4] == (
        "WFS:https://example.test/api"
        if driver == "WFS"
        else "OAPIF:https://example.test/api?token=x"
    )
    assert args[5] == "buildings"
    assert f"OGR_{driver}_PAGE_SIZE" in args
    if driver == "WFS":
        assert run.call_args.kwargs == {
            "env_extra": {"GDAL_HTTP_USERPWD": "test:fixture"}
        }
    else:
        assert run.call_args.kwargs == {}
    assert finish.call_args.kwargs["dataset_id"] == "dataset"
    assert finish.call_args.kwargs["details"]["source_url"] == dataset["source_url"]
    assert "password=***" in finish.call_args.kwargs["details"]["ogr_cmd"]
