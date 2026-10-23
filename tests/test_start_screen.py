import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent / "app"
WIN95 = (ROOT / "static" / "win95.css").read_text()
PAGES = ["/admin", "/admin/photos", "/admin/about"]
TUNNEL = {"X-Homeboard-Tunnel": "1", "CF-Connecting-IP": "203.0.113.7"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


@pytest.fixture
def tunnel_client(tmp_path, monkeypatch):
    hash_file = tmp_path / "tunnel-password"
    hash_file.write_text(auth.hash_password("letmein") + "\n")
    monkeypatch.setenv("HOMEBOARD_PASSWORD_FILE", str(hash_file))
    with TestClient(create_app(tmp_path / "data"), base_url="https://board.example.com") as c:
        yield c


@pytest.mark.parametrize("path", PAGES)
def test_admin_pages_return_200(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "/static/win95.css?v=" in r.text


def test_start_links_to_photos_and_about(client):
    page = client.get("/admin").text
    assert 'href="/admin/photos"' in page
    assert 'href="/admin/about"' in page
    for name in ("Calendar", "Notes", "Grocery List"):
        item = re.search(
            r'<span class="w95-icon disabled" aria-disabled="true">(?:(?!</li>).)*?'
            + re.escape(name)
            + r"(?:(?!</li>).)*?</span></li>",
            page,
            re.DOTALL,
        )
        assert item, name
        assert "Coming soon" in item.group(0)
        assert "href" not in item.group(0)
    assert page.count("Coming soon") == 3
    assert 'id="photo-count"' in page and 'id="display-state"' in page


def test_start_status_refreshes_every_ten_seconds():
    js = (ROOT / "static" / "start.js").read_text()
    assert '"/api/status"' in js
    assert "STATUS_MS = 10000" in js and "setInterval(loadStatus, STATUS_MS)" in js
    assert '"Display online"' in js and '"Display offline"' in js


def test_photos_keeps_controls_and_back_link(client):
    page = client.get("/admin/photos").text
    assert 'id="file-input" type="file"' in page
    assert 'id="grid"' in page and 'id="interval"' in page
    assert '<a class="w95-back" href="/admin">Start</a>' in page
    for el in ('id="host"', 'id="ip"', 'id="disk"', 'id="version"', 'href="/docs"'):
        assert el not in page


def test_about_page(client):
    page = client.get("/admin/about").text
    for el in ('id="host"', 'id="ip"', 'id="disk"', 'id="version"'):
        assert el in page
    assert 'href="/docs"' not in page
    assert '<a class="w95-back" href="/admin">Start</a>' in page


def test_win95_css_classes():
    for cls in (
        ".w95-window",
        ".w95-titlebar",
        ".w95-close",
        ".w95-group",
        ".w95-btn",
        ".w95-btn.default",
        ".w95-check",
        ".w95-input",
        ".w95-progress",
        ".w95-statusbar",
        ".w95-icon",
        ":focus-visible",
    ):
        assert cls in WIN95, cls
    assert "min-height: 44px" in WIN95 and "height: 44px" in WIN95


def test_no_dark_mode_or_rounded_corners():
    for text in (
        WIN95,
        (ROOT / "templates" / "start.html").read_text(),
        (ROOT / "templates" / "about.html").read_text(),
    ):
        assert "prefers-color-scheme" not in text
        assert "border-radius" not in text


@pytest.mark.parametrize("path", PAGES)
def test_tunnel_gate_demands_password(tunnel_client, path):
    r = tunnel_client.get(path, headers=TUNNEL, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?next=" + path)
    ok = tunnel_client.post(
        "/login",
        data={"password": "letmein", "next": path},
        headers=TUNNEL,
        follow_redirects=False,
    )
    assert ok.status_code == 303 and ok.headers["location"] == path
    assert tunnel_client.get(path, headers=TUNNEL).status_code == 200
