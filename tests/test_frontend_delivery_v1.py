import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.frontend import render_index
from app.main import app, BUILD_ID


def test_index_versions_local_scripts_and_styles_only():
    source = '''<body data-app-build="__BUILD_ID__"><script src="/static/app.js"></script>
<script src='/static/markdown.js?v=old'></script><link href="/static/styles.css">
<a href="https://example.com/help">help</a><img src="/static/logo.png"></body>'''
    result = render_index(source, 'abc123')
    assert 'data-app-build="abc123"' in result
    assert '/static/app.js?v=abc123' in result
    assert '/static/markdown.js?v=abc123' in result
    assert '/static/styles.css?v=abc123' in result
    assert '?v=old' not in result
    assert 'https://example.com/help' in result
    assert '/static/logo.png"' in result


def test_application_shell_and_local_lexer_do_not_cache_stale_resources():
    with TestClient(app) as client:
        response = client.get('/')
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert '/static/app.js?v=' + BUILD_ID in response.text
        assert '__BUILD_ID__' not in response.text
        for path in ('/static/app.js', '/static/styles.css', '/static/vendor/marked/marked.umd.js'):
            asset = client.get(path)
            assert asset.status_code == 200
            assert asset.headers['cache-control'] == 'no-store'
        assert client.get('/api/profile').headers['cache-control'] == 'no-store'


def test_official_lexer_license_and_artifact_integrity_ship_together():
    root = Path(__file__).resolve().parents[1] / 'app/static/vendor/marked'
    notice = json.loads((root / 'NOTICE.json').read_text(encoding='utf-8'))
    assert notice['version'] == '18.2.0'
    assert hashlib.sha256((root / 'marked.umd.js').read_bytes()).hexdigest() == notice['sha256']
    assert 'MIT' in (root / 'LICENSE').read_text(encoding='utf-8')
