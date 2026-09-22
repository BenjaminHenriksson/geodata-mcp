import page
import ui


def test_map_pages_keep_title_and_renderer_without_application_navigation():
    for render in (page.maplibre_page, page.origo_page):
        html = render('v_' + 'a' * 24, title='Testkarta')
        assert '<header class="app-header"' not in html
        assert '<h1 id="titlebar">Testkarta</h1>' in html
        assert 'aria-label="Kartvisare"' in html
    assert '<header class="app-header"' in ui.document('Översikt', '<p>Innehåll</p>')
    assert 'href="/workspaces"' in ui.document('Översikt', '')
