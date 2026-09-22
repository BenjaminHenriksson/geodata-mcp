"""Keep the architecture guide private and its interactive graph connected."""
from html.parser import HTMLParser

from fastapi.testclient import TestClient
import pytest

import architecture_page
import main as viewer


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids = []
        self.scripts = []
        self.handlers = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "script":
            self.scripts.append(attrs.get("src"))
        self.handlers.extend(key for key in attrs if key.startswith("on"))


def test_graph_and_flows_reference_real_components():
    nodes = {node[0] for node in architecture_page.NODES}
    edges = {edge[0] for edge in architecture_page.EDGES}
    assert len(nodes) == len(architecture_page.NODES)
    assert len(edges) == len(architecture_page.EDGES)
    for _, start, end, _, _ in architecture_page.EDGES:
        assert start in nodes and end in nodes and start != end
    for _, _, steps in architecture_page.FLOWS.values():
        for _, _, links in steps:
            assert links and set(links) <= edges


@pytest.mark.parametrize("principal", [None, {"id": "fixture", "name": "<img src=x onerror=alert(1)>", "is_admin": False}])
def test_architecture_requires_login_and_preserves_csp(monkeypatch, principal):
    monkeypatch.setattr(viewer, "_dashboard_principal", lambda request: principal)
    monkeypatch.setattr(viewer.viewer_auth, "csrf_token", lambda _: "fixture-csrf")
    client = TestClient(viewer.app, follow_redirects=False)
    response = client.get("/architecture")
    if principal is None:
        assert response.status_code == 302 and response.headers["location"] == "/login"
        return
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    script_policy = response.headers["content-security-policy"].split("style-src")[0]
    assert "unsafe-inline" not in script_policy and "unsafe-eval" not in script_policy
    assert "<img src=x" not in response.text and "&lt;img" in response.text
    assert 'href="/architecture" aria-current="page"' in response.text
    page = Page(response.text)
    assert len(page.ids) == len(set(page.ids))
    assert page.scripts == ["/static/architecture/architecture.js?v=1"]
    assert not page.handlers
    assert 'lang="en"' in response.text


@pytest.mark.parametrize("asset,mime", [("architecture.js", "javascript"), ("architecture.css", "text/css")])
def test_local_assets_are_served(asset, mime):
    response = TestClient(viewer.app).get("/static/architecture/" + asset)
    assert response.status_code == 200
    assert mime in response.headers["content-type"]
