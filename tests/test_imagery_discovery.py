import json

import search_ops


def test_panorama_search_discovers_published_sites_without_unrelated_catalog(monkeypatch):
    monkeypatch.setattr(search_ops.imagery, "catalogue", lambda: {
        "sites": [{"id": "island", "label": "Island", "has_panoramas": True,
                   "bounds": [18, 59, 18.1, 59.1]}], "limitations": ["Camera GPS only"],
    })
    monkeypatch.setattr(search_ops, "embed_query", lambda q: (_ for _ in ()).throw(AssertionError("unexpected catalog search")))
    result = search_ops.hybrid_search("360 panorama virtuell rundvandring", None, 15)
    assert result["imagery"]["sites"][0]["id"] == "island"
    assert result["next_calls"][0]["arguments"]["params"]["site_id"] == "island"
    assert "frame_id" in result["inspection"]
    assert len(json.dumps(result)) < 10000


def test_unavailable_imagery_is_not_reported_as_no_images(monkeypatch):
    def unavailable():
        raise OSError("private path")
    monkeypatch.setattr(search_ops.imagery, "catalogue", unavailable)
    assert search_ops.hybrid_search("gatubilder", None, 15) == {
        "error": "Published street imagery catalogue is temporarily unavailable"
    }
