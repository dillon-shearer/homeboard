import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.chrome import needs_chrome
from tests.test_lists_page import _wait
from tests.test_settings_sound import _fake


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def _open(chrome, server):
    chrome.size(390, 844)
    chrome.send("Page.navigate", {"url": f"{server}/admin/sound"})


def test_sound_page_shows_only_the_state_lines(client):
    r = client.get("/admin/sound")
    assert r.status_code == 200
    page = r.text
    assert 'id="spotify-state"' in page and 'id="speaker-state"' in page
    assert ">Spotify<" in page and ">Speaker<" in page
    assert "/static/sound.js?v=" in page and "/static/sound.css?v=" in page
    for gone in ("idle-minutes", "playlists", "spotify-unlink", "spotify-login", "speaker-connect"):
        assert gone not in page, gone


def test_start_has_sound_icon(client):
    page = client.get("/admin").text
    assert '<a class="w95-icon" href="/admin/sound">' in page
    assert '<span class="w95-icon-label">Sound</span>' in page


@needs_chrome
def test_sound_page_shows_spotify_and_speaker_state(chrome, server, monkeypatch):
    _fake(monkeypatch)
    _open(chrome, server)
    state = _wait(
        chrome,
        "(t => t === 'Linked to Spotify' && t)(document.getElementById('spotify-state').textContent)",
    )
    speaker = _wait(
        chrome,
        "(t => t === 'Connected to Soundbar' && t)"
        "(document.getElementById('speaker-state').textContent)",
    )
    errors = chrome.eval("window.__errors")
    assert state == "Linked to Spotify"
    assert speaker == "Connected to Soundbar"
    assert errors == []


@needs_chrome
def test_sound_page_says_helper_not_running(chrome, server, monkeypatch):
    _fake(monkeypatch, linked=False, helper_status=503)
    _open(chrome, server)
    speaker = _wait(
        chrome,
        "(t => t === 'Helper not running' && t)"
        "(document.getElementById('speaker-state').textContent)",
    )
    state = chrome.eval("document.getElementById('spotify-state').textContent")
    errors = chrome.eval("window.__errors")
    assert speaker == "Helper not running"
    assert state == "Not linked"
    assert errors == []
