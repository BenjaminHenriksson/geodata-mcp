"""Keep the architecture guide private and its interactive graph connected."""
from html.parser import HTMLParser
import json

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
        self.language = None
        self.language_links = {}
        self.flow_data = {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "html":
            self.language = attrs.get("lang")
        if "data-language" in attrs:
            self.language_links[attrs["data-language"]] = attrs
        if "data-flows" in attrs:
            self.flow_data = json.loads(attrs["data-flows"])
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
    assert page.scripts == ["/static/architecture/architecture.js?v=3"]
    assert not page.handlers
    assert page.language == "sv"


@pytest.mark.parametrize("query,lang,heading,flow", [
    ("", "sv", "Så blir en fråga till en karta.", "Förändringsanalys · Gemma"),
    ("?lang=sv", "sv", "Så blir en fråga till en karta.", "Förändringsanalys · Gemma"),
    ("?lang=en", "en", "How a question becomes a map.", "Detect changes · Gemma"),
])
def test_language_choice(monkeypatch, query, lang, heading, flow):
    monkeypatch.setattr(viewer, "_dashboard_principal", lambda request: {"id": "fixture", "name": "Reader", "is_admin": False})
    monkeypatch.setattr(viewer.viewer_auth, "csrf_token", lambda _: "fixture-csrf")
    response = TestClient(viewer.app).get("/architecture" + query)
    assert response.status_code == 200
    page = Page(response.text)
    assert page.language == lang
    assert f"<h1>{heading}</h1>" in response.text
    assert page.flow_data["gemma"]["title"] == flow
    assert page.language_links[lang]["aria-current"] == "page"
    for language, link in page.language_links.items():
        assert link["href"] == "/architecture?lang=" + language
        if language != lang:
            assert "aria-current" not in link


def test_translations_preserve_graph_and_analysis_steps():
    english = architecture_page.content("en")
    swedish = architecture_page.content("sv")
    assert set(architecture_page.sv.NODES) == {n[0] for n in english[0]}
    assert set(architecture_page.sv.EDGES) == {e[0] for e in english[1]}
    assert set(architecture_page.sv.FLOWS) == set(english[2])
    assert set(architecture_page.sv.TOOLS) == {t[0] for t in english[3]}
    for en, sv in zip(english[0], swedish[0], strict=True):
        assert en[:3] == sv[:3] and en[-1] == sv[-1]
        assert len(en[6]) == len(sv[6])
    for en, sv in zip(english[1], swedish[1], strict=True):
        assert en[:3] == sv[:3] and en[-1] == sv[-1]
    for key in english[2]:
        assert [s[2] for s in english[2][key][2]] == [s[2] for s in swedish[2][key][2]]


def test_languages_render_independently():
    principal = {"id": "fixture", "name": "Reader", "is_admin": False}
    before = architecture_page.render(principal, "csrf", lang="en")
    architecture_page.render(principal, "csrf", lang="sv")
    assert architecture_page.render(principal, "csrf", lang="en") == before


@pytest.mark.parametrize("asset,mime", [("architecture.js", "javascript"), ("architecture.css", "text/css")])
def test_local_assets_are_served(asset, mime):
    response = TestClient(viewer.app).get("/static/architecture/" + asset)
    assert response.status_code == 200
    assert mime in response.headers["content-type"]
