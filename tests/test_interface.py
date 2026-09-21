"""Interface integration: navigation, public map access, and manager actions."""
import uuid

import pytest
import test_workspace_dashboards as support
from fastapi.testclient import TestClient

client = support.client
principal = support.principal
records = support.records
viewer = support.viewer



@pytest.fixture
def map_id(principal):
    view_id = "v_" + uuid.uuid4().hex[:24]
    records("INSERT INTO app.map_views(view_id, workspace_id, title, spec) VALUES (%s,%s,%s,%s)",
            (view_id, principal[2].id, 'Map <img src=x onerror=alert(1)>', '{}'))
    return view_id


@pytest.mark.parametrize("renderer", ["maplibre", "origo"])
def test_public_map_navigation_and_escaped_title(map_id, renderer, principal):
    with TestClient(viewer.app) as public:
        response = public.get(f"/v/{map_id}?renderer={renderer}")
    assert response.status_code == 200
    assert 'class="app-header"' in response.text
    assert 'aria-label="Kartvisare"' in response.text
    assert "Map &lt;img" in response.text and "<img src=x" not in response.text
    assert 'href="/dashboard"' in response.text
    assert 'href="/admin"' not in response.text
    assert principal[1] not in response.text and principal[2].id not in response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["referrer-policy"] == "strict-origin"
    scripts = response.headers["content-security-policy"].split("style-src")[0]
    assert "unsafe-inline" not in scripts
    assert ("unsafe-eval" in scripts) == (renderer == "maplibre")


def test_admin_navigation_consistent_on_manager_and_maps(client, principal, map_id):
    records("UPDATE app.api_keys SET is_admin=true WHERE id=%s", (principal[1],))
    for path in ("/dashboard", "/admin", "/admin/audit", "/workspaces",
                 f"/workspaces/{principal[2].id}", f"/v/{map_id}", f"/v/{map_id}?renderer=origo"):
        response = client.get(path)
        assert response.status_code == 200
        assert 'class="app-header"' in response.text
        assert 'href="/admin"' in response.text
        assert "Logga ut" in response.text


def test_manager_actions_preserve_csrf_and_confirmation(client, principal):
    wid = principal[2].id
    page = client.get("/workspaces").text
    assert 'name="csrf"' in page
    assert 'Lager och kartor tas bort permanent.' in page
    assert client.post("/workspaces/action", data={"action": "rename", "workspace_id": wid,
                                                   "new_name": "renamed"}).status_code == 403
    from viewer_auth import csrf_token
    response = client.post("/workspaces/action", data={
        "action": "rename", "workspace_id": wid, "new_name": "renamed",
        "csrf": csrf_token(principal[1])})
    assert response.status_code == 200 and "renamed" in response.text


def test_empty_views_do_not_show_redundant_pagination(client, principal):
    page = client.get(f"/workspaces/{principal[2].id}").text
    assert "Sida 1" not in page
    assert "Visar högst" not in page
    assert 'id="maps"' in page and 'id="audit"' in page
