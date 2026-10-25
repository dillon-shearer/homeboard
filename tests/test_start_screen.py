import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent / "app"
WIN95 = (ROOT / "static" / "win95.css").read_text()
PAGES = ["/admin", "/admin/photos", "/admin/lists", "/admin/calendar", "/admin/tv", "/admin/about"]
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
    assert 'href="/admin/calendar"' in page
    assert "Coming soon" not in page
    assert ">Notes<" not in page and "Grocery List" not in page


def test_start_has_no_app_group_and_a_small_about_link(client):
    page = client.get("/admin").text
    assert "group-app" not in page and ">App<" not in page
    assert page.count('href="/admin/about"') == 1
    assert '<a href="/admin/about">About this board...</a>' in page
    for icons in re.findall(r'<ul class="w95-icons">.*?</ul>', page, re.DOTALL):
        assert "/admin/about" not in icons


def test_enabled_icons_have_no_subtext(client):
    page = client.get("/admin").text
    enabled = re.findall(r'<a class="w95-icon".*?</a>', page, re.DOTALL)
    assert enabled
    for icon in enabled:
        assert "w95-icon-note" not in icon


def test_start_status_refreshes_every_ten_seconds():
    js = (ROOT / "static" / "start.js").read_text()
    assert '"/api/status"' in js
    assert "STATUS_MS = 10000" in js and "setInterval(loadStatus, STATUS_MS)" in js
    assert '"Display online"' in js and '"Display offline"' in js


def test_photos_keeps_controls_and_back_link(client):
    page = client.get("/admin/photos").text
    assert 'type="file"' in page
    assert '<a class="w95-back" href="/admin">Start</a>' in page
    for el in ("<dt>Name</dt>", "<dt>IP address</dt>", "<dt>Free space</dt>", 'href="/docs"'):
        assert el not in page


def test_about_page(client):
    page = client.get("/admin/about").text
    for label in ("Name", "IP address", "Free space", "Version"):
        assert f"<dt>{label}</dt>" in page
    assert 'href="/docs"' not in page
    assert '<a class="w95-back" href="/admin">Start</a>' in page


def test_no_dark_mode_or_rounded_corners():
    for text in (
        WIN95,
        (ROOT / "templates" / "start.html").read_text(),
        (ROOT / "templates" / "about.html").read_text(),
        (ROOT / "templates" / "admin.html").read_text(),
        (ROOT / "static" / "admin.css").read_text(),
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


def test_start_has_five_icons_and_no_tv_controls(client):
    page = client.get("/admin").text
    icons = re.findall(
        r'<a class="w95-icon" href="([^"]+)">.*?<span class="w95-icon-label">([^<]+)<',
        page,
        re.DOTALL,
    )
    assert icons == [
        ("/admin/photos", "Photos"),
        ("/admin/calendar", "Calendar"),
        ("/admin/lists", "Lists"),
        ("/admin/tv", "TV"),
        ("/admin/sound", "Sound"),
    ]
    assert page.count('class="w95-icon"') == 5
    for gone in ('id="channel-timeout"', 'id="weather-location"', 'id="clock-size"'):
        assert gone not in page
    tv = re.search(r'<a class="w95-icon" href="/admin/tv">.*?</a>', page, re.DOTALL).group(0)
    assert '<svg class="w95-icon-img" viewBox="0 0 32 32" aria-hidden="true">' in tv


def test_start_js_is_only_the_status_poll():
    js = (ROOT / "static" / "start.js").read_text()
    assert "/api/status" in js
    for gone in ("/api/screens", "/api/settings", "tv-stops", "channel-timeout", "name-dialog"):
        assert gone not in js


def test_screen_dialog_styles_live_in_win95():
    for sel in (".modal", ".modal-backdrop", ".dialog", ".title-close", ".buttons"):
        assert re.search(r"(?m)^" + re.escape(sel) + r" \{", WIN95), sel
    assert ".modal[hidden]" in WIN95


def test_screen_rename_and_order_api(client):
    first = client.get("/api/screens").json()[0]["id"]
    second = client.post("/api/screens", json={"name": "Chores"}).json()["id"]
    r = client.put("/api/screens/order", json={"ids": [second, first]})
    assert [s["id"] for s in r.json()][:2] == [second, first]
    r = client.patch(f"/api/screens/{second}", json={"name": "Fridge"})
    assert r.json()["name"] == "Fridge"
    assert client.get("/api/screens").json()[0]["name"] == "Fridge"
    r = client.put("/api/settings", json={"channel": "screen", "screen_id": second})
    assert (r.json()["channel"], r.json()["screen_id"]) == ("screen", second)
