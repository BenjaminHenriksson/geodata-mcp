"""Keep the generated API contract consistent with actual HTTP behavior."""
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
import pytest

import main as viewer


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(viewer.dbq, 'get_pool', MagicMock())
    monkeypatch.setattr(viewer.viewer_auth, 'enabled', lambda: True)
    monkeypatch.setattr(viewer.viewer_auth, 'parse_cookie', lambda _: None)
    return TestClient(viewer.app, follow_redirects=False)


def test_schema_covers_the_public_api_and_auth(client):
    schema = client.get('/openapi.json').json()
    paths = schema['paths']
    assert set(paths) == {'/', '/healthz', '/login', '/logout', '/workspaces', '/workspaces/action',
                          '/v/{view_id}', '/v/{view_id}/style.json', '/v/{view_id}/origo.json',
                          '/data/{layer}.geojson', '/tiles/{layer}/{z}/{x}/{y}.mvt', '/wmsref/{dataset_id}',
                          '/dashboard', '/admin', '/admin/audit', '/admin/services',
                          '/admin/services/action', '/workspaces/{workspace_id}'}
    assert schema['components']['securitySchemes']['managerCookie']['name'] == 'gdw_auth'
    assert paths['/workspaces']['get']['security'] == [{'managerCookie': []}]
    assert paths['/workspaces/action']['post']['security'] == [{'managerCookie': []}]
    assert not paths['/data/{layer}.geojson']['get'].get('security')
    assert '304' in paths['/v/{view_id}/style.json']['get']['responses']
    assert '204' in paths['/tiles/{layer}/{z}/{x}/{y}.mvt']['get']['responses']
    for route in paths.values():
        for operation in route.values():
            assert operation['summary'] and operation['tags']
    assert client.get('/docs').status_code == 200
    assert client.get('/redoc').status_code == 200


@pytest.mark.parametrize('method,url,path,status', [
    ('get', '/', '/', 302), ('post', '/logout', '/logout', 303),
    ('get', '/workspaces', '/workspaces', 302), ('post', '/workspaces/action', '/workspaces/action', 302),
    ('get', '/data/ref.buildings.geojson', '/data/{layer}.geojson', 400),
    ('get', '/tiles/ref.buildings/23/0/0.mvt', '/tiles/{layer}/{z}/{x}/{y}.mvt', 400),
    ('get', '/wmsref/missing', '/wmsref/{dataset_id}', 400),
    ('get', '/v/missing?renderer=invalid', '/v/{view_id}', 400),
])
def test_documented_status_and_content_type_match_http(client, method, url, path, status):
    response = getattr(client, method)(url)
    assert response.status_code == status
    documented = viewer.app.openapi()['paths'][path][method]['responses'][str(status)]
    if 'content-type' in response.headers:
        assert response.headers['content-type'].split(';')[0] in documented['content']


def test_geojson_examples_and_existing_limit_clamping(client, monkeypatch):
    body = '{"type":"FeatureCollection","features":[]}'
    monkeypatch.setattr(viewer, '_checked_layer', lambda *args: ('ref', 'buildings', ['name']))
    monkeypatch.setattr(viewer.dbq, 'feature_count', lambda *args: 1)
    query = MagicMock(return_value=body)
    monkeypatch.setattr(viewer.dbq, 'geojson_feature_collection', query)
    response = client.get('/data/ref.buildings.geojson?view=fixture&limit=100000')
    assert response.status_code == 200
    assert query.call_args.args[-2] == 50000  # Preserve clamping, rather than new validation errors.
    documented = viewer.app.openapi()['paths']['/data/{layer}.geojson']['get']['responses']['200']
    assert response.headers['content-type'] == 'application/geo+json'
    assert documented['content']['application/geo+json']['example'] == response.json()


def test_disabled_manager_error_remains_json(client, monkeypatch):
    monkeypatch.setattr(viewer.viewer_auth, 'enabled', lambda: False)
    response = client.get('/login')
    assert response.status_code == 503
    assert 'detail' in response.json()
    assert 'application/json' in viewer.app.openapi()['paths']['/login']['get']['responses']['503']['content']
