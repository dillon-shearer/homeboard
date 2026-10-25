import json
import time

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app import routes_sound, spotify
from app.main import create_app
from tests.chrome import _Chrome, needs_chrome, server  # noqa: F401
from tests.test_lists_page import _wait

PLAYLISTS = [
    {"id": "p1", "name": "Morning", "cover": None, "tracks": 10},
    {"id": "p2", "name": "Dinner", "cover": "https://i.scdn.co/2", "tracks": 5},
    {"id": "p3", "name": "Party", "cover": None, "tracks": 20},
]

SPY = """
window.__sent = [];
window.__errors = [];
window.addEventListener('error', e => window.__errors.push(String(e.message)));
window.addEventListener('unhandledrejection', e => window.__errors.push(String(e.reason)));
const realError = console.error;
console.error = function () { window.__errors.push([].join.call(arguments, ' ')); realError.apply(console, arguments); };
const realFetch = window.fetch;
window.fetch = function (url, opts) {
  opts = opts || {};
  if (opts.method && opts.method !== 'GET') window.__sent.push({method: opts.method, url: String(url), body: opts.body ? JSON.parse(opts.body) : null});
  return realFetch.apply(this, arguments);
};
"""


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def _fake(monkeypatch, linked=True, helper_status=200, reason="not linked"):
    def now(data_dir):
        if not linked:
            raise spotify.NotLinked(reason)
        return {"linked": True, "playing": True, "track": "Song", "artist": "Band"}

    def lists(data_dir):
        if not linked:
            raise spotify.NotLinked("not linked")
        return PLAYLISTS

    def helper(method, path, timeout=5):
        if helper_status == 503:
            return JSONResponse({"detail": "helper down"}, status_code=503)
        return JSONResponse({"speaker": {"connected": True, "name": "Soundbar"}})

    monkeypatch.setattr(spotify, "now_playing", now)
    monkeypatch.setattr(spotify, "playlists", lists)
    monkeypatch.setattr(routes_sound, "helper", helper)


def _open(server, tmp_path):  # noqa: F811
    chrome = _Chrome(tmp_path / "chrome", 390, 844)
    chrome.send("Page.enable")
    chrome.send("Page.addScriptToEvaluateOnNewDocument", {"source": SPY})
    chrome.send("Page.navigate", {"url": f"{server}/admin/sound"})
    return chrome


def _sent(chrome):
    return json.loads(chrome.eval("JSON.stringify(window.__sent)"))


def test_sound_page_has_groups_and_sign_in_link(client):
    r = client.get("/admin/sound")
    assert r.status_code == 200
    page = r.text
    for group in ('id="group-spotify"', 'id="group-speaker"', 'id="group-playlists"'):
        assert group in page
    assert ">Spotify<" in page and ">Speaker<" in page and ">Playlists on the board<" in page
    assert 'href="/api/spotify/login"' in page and ">Sign in to Spotify<" in page
    assert "/static/sound.js?v=" in page and "/static/sound.css?v=" in page
    assert 'min="1" max="120"' in page


def test_start_has_sound_icon(client):
    page = client.get("/admin").text
    assert '<a class="w95-icon" href="/admin/sound">' in page
    assert '<span class="w95-icon-label">Sound</span>' in page


@needs_chrome
def test_ticking_two_playlists_puts_them_in_page_order(server, tmp_path, monkeypatch):  # noqa: F811
    _fake(monkeypatch)
    chrome = _open(server, tmp_path)
    try:
        _wait(chrome, "document.querySelectorAll('#playlists input').length === 3")
        state = chrome.eval("document.getElementById('spotify-state').textContent")
        track = chrome.eval("document.getElementById('spotify-track').textContent")
        chrome.eval("document.querySelector('#playlists input[data-id=\"p3\"]').click()")
        chrome.eval("document.querySelector('#playlists input[data-id=\"p1\"]').click()")
        count = chrome.eval("document.getElementById('shown-count').textContent")
        _wait(chrome, "window.__sent.length === 2")
        sent = _sent(chrome)
        chrome.send("Page.reload")
        _wait(chrome, "document.querySelectorAll('#playlists input').length === 3")
        checked = chrome.eval(
            "[...document.querySelectorAll('#playlists input')].filter(b => b.checked)"
            ".map(b => b.dataset.id).join(',')"
        )
        errors = chrome.eval("window.__errors")
    finally:
        chrome.close()
    assert state == "Linked to Spotify"
    assert track == "Playing: Song - Band"
    assert count == "Shown on the board: 2"
    assert sent[-1]["method"] == "PUT" and sent[-1]["url"] == "/api/spotify/shown"
    assert [p["id"] for p in sent[-1]["body"]] == ["p1", "p3"]
    assert checked == "p1,p3"
    assert errors == []


@needs_chrome
def test_idle_stepper_puts_setting_and_refuses_out_of_range(server, tmp_path, monkeypatch):  # noqa: F811
    _fake(monkeypatch)
    chrome = _open(server, tmp_path)
    try:
        _wait(chrome, "document.getElementById('idle-minutes').value !== ''")
        start = int(chrome.eval("document.getElementById('idle-minutes').value"))

        def typed(v):
            chrome.eval(
                f"(i => {{ i.focus(); i.value = {json.dumps(v)};"
                " i.dispatchEvent(new Event('input')); })"
                "(document.getElementById('idle-minutes'))"
            )

        typed("0")
        typed("121")
        time.sleep(0.8)
        refused = _sent(chrome)
        error = chrome.eval("document.getElementById('settings-error').textContent")
        typed("30")
        _wait(chrome, "window.__sent.length === 1")
        chrome.eval("document.getElementById('idle-minutes').blur()")
        chrome.eval(
            "(i => { i.value = 120; })(document.getElementById('idle-minutes'));"
            "document.getElementById('idle-minutes-plus').click()"
        )
        top = chrome.eval("document.getElementById('idle-minutes').value")
        _wait(chrome, "window.__sent.length === 2")
        sent = _sent(chrome)
    finally:
        chrome.close()
    assert start == 10
    assert refused == []
    assert "1 to 120" in error
    assert sent[0] == {
        "method": "PUT",
        "url": "/api/settings",
        "body": {"speaker_idle_minutes": 30},
    }
    assert top == "120"
    assert sent[1]["body"] == {"speaker_idle_minutes": 120}


@needs_chrome
def test_helper_down_shows_helper_not_running(server, tmp_path, monkeypatch):  # noqa: F811
    _fake(monkeypatch, linked=False, helper_status=503)
    chrome = _open(server, tmp_path)
    try:
        speaker = _wait(
            chrome,
            "(t => t === 'Helper not running' && t)"
            "(document.getElementById('speaker-state').textContent)",
        )
        note = _wait(
            chrome,
            "(t => t === 'Sign in to Spotify first' && t)"
            "(document.getElementById('playlists-note').textContent)",
        )
        login = chrome.eval("!document.getElementById('spotify-login').hidden")
        chrome.eval("document.getElementById('speaker-connect').click()")
        _wait(chrome, "window.__sent.length === 1")
        _wait(chrome, "!document.getElementById('speaker-connect').disabled")
        after = chrome.eval("document.getElementById('speaker-state').textContent")
        errors = chrome.eval("window.__errors")
    finally:
        chrome.close()
    assert speaker == "Helper not running"
    assert note == "Sign in to Spotify first"
    assert login is True
    assert after == "Helper not running"
    assert errors == []


@needs_chrome
def test_no_credentials_shows_setup_line_and_no_button(server, tmp_path, monkeypatch):  # noqa: F811
    _fake(monkeypatch, linked=False, reason="no credentials")
    chrome = _open(server, tmp_path)
    try:
        state = _wait(
            chrome,
            "(t => t.startsWith('Spotify is not') && t)"
            "(document.getElementById('spotify-state').textContent)",
        )
        buttons = chrome.eval(
            "[document.getElementById('spotify-login').hidden,"
            " document.getElementById('spotify-unlink').hidden]"
        )
        errors = chrome.eval("window.__errors")
    finally:
        chrome.close()
    assert state == "Spotify is not set up on the Pi yet. See PI_SETUP.md, Spotify."
    assert buttons == [True, True]
    assert errors == []
