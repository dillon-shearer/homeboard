import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_admin_page(client):
    r = client.get("/admin")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in r.text
    assert '<link rel="manifest" href="/static/manifest.webmanifest">' in r.text
    assert 'name="apple-mobile-web-app-capable"' in r.text
    assert 'rel="apple-touch-icon"' in r.text
    assert 'accept="image/jpeg,image/png,image/heic,image/heif,image/webp" multiple' in r.text
    assert "/static/admin.js" in r.text and "/static/admin.css" in r.text
    assert 'type="submit"' not in r.text and "<form" not in r.text


def test_manifest(client):
    r = client.get("/static/manifest.webmanifest")
    assert r.status_code == 200
    body = json.loads(r.text)
    assert body["name"] == "Home Board"
    assert body["start_url"] == "/admin"
    assert body["display"] == "standalone"
    assert body["icons"][0]["src"] == "/static/icon-512.png"


def test_icon(client):
    r = client.get("/static/icon-512.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_admin_js_uses_xhr_progress_and_settings_put():
    js = (STATIC / "admin.js").read_text()
    assert "new XMLHttpRequest()" in js
    assert "xhr.upload.onprogress" in js
    assert '"/api/settings"' in js and 'method: "PUT"' in js
    assert "/api/photos?all=1" in js and "/api/status" in js
