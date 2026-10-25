import json

import pytest

from app import events, routes_sound, spotify
from tests.chrome import needs_chrome, needs_node
from tests.test_control_panel import DOWN, OPEN, _load, _node, _swipe
from tests.test_spotify_volume_buttons import FRESH, VolumeSpotify
from tests.test_tv_screen import _until
from tests.test_tv_spotify import _playing


@needs_node
def test_now_row_and_volume_step():
    r = _node(
        """
        console.log(JSON.stringify({
          none: S.nowRow(null),
          unlinked: S.nowRow({linked: false, track: "A"}),
          idle: S.nowRow({linked: true, playing: false, track: null}),
          paused: S.nowRow({linked: true, playing: false, track: "A", artist: "B", volume: 30}),
          playing: S.nowRow({linked: true, playing: true, track: "A", volume: null}),
          steps: [S.volumeStep(50, 5), S.volumeStep(98, 5), S.volumeStep(2, -5), S.volumeStep(null, 5)],
        }));
        """
    )
    assert r["none"] == {"shown": False}
    assert r["unlinked"] == {"shown": False}
    assert r["idle"] == {"shown": False}
    assert r["paused"] == {
        "shown": True,
        "line": "A - B",
        "toggle": "Play",
        "known": True,
        "volume": "30%",
    }
    assert r["playing"]["line"] == "A" and r["playing"]["toggle"] == "Pause"
    assert r["playing"]["known"] is False and r["playing"]["volume"] == "--%"
    assert r["steps"] == [55, 100, 0, None]


@pytest.fixture
def sp(monkeypatch):
    fake_api = VolumeSpotify()
    fake_api.now = {"linked": True, "playing": False, "track": None, "volume": None}
    monkeypatch.setattr(spotify, "now_playing", fake_api.now_playing)
    monkeypatch.setattr(spotify, "control", fake_api.control)
    monkeypatch.setattr(routes_sound, "helper", fake_api.helper)
    return fake_api


ROW = """JSON.stringify({
  hidden: document.getElementById('cp-now').hidden,
  line: document.getElementById('cp-now-line').textContent,
  toggle: document.getElementById('cp-now-toggle').textContent,
  volume: document.getElementById('cp-now-volume').textContent,
  up: document.getElementById('cp-now-up').disabled,
})"""


def _row(chrome):
    return json.loads(chrome.eval(ROW))


def _changed(chrome, sp, now):
    sp.now = now
    before = chrome.eval("__test.heard('spotify_changed')")
    events.publish("spotify_changed")
    chrome.wait(f"__test.heard('spotify_changed') > {before}")
    chrome.run("__test.settle()")
    return _row(chrome)


@needs_chrome
def test_now_playing_row(chrome, server, sp):
    _load(chrome, server)
    _swipe(chrome, 500, DOWN)
    chrome.wait(OPEN)
    chrome.wait("window.__inflight === 0")
    idle = _row(chrome)
    reads = sp.calls.count(("GET", "/api/spotify/now"))

    shown = _changed(chrome, sp, _playing(volume=50))

    chrome.eval("document.getElementById('cp-now-next').click()")
    assert _until(lambda: sp.sent("POST", "/api/spotify/next"), bool)
    chrome.eval("document.getElementById('cp-now-toggle').click()")
    optimistic = chrome.eval("document.getElementById('cp-now-toggle').textContent")
    assert _until(lambda: sp.sent("POST", "/api/spotify/pause"), bool)

    chrome.eval("document.getElementById('cp-now-up').click()")
    puts = _until(lambda: sp.sent("PUT", "/api/spotify/volume"), bool)
    fresh_first = sp.calls.index(FRESH) < sp.calls.index(puts[0])
    chrome.wait("window.__inflight === 0")
    sent = json.loads(chrome.eval("JSON.stringify(window.__sent)"))

    changed = _changed(chrome, sp, _playing(track="Let It Be", volume=55))
    fits = chrome.eval(
        "(p => p.scrollHeight <= p.clientHeight)(document.getElementById('control-panel'))"
    )
    errors = chrome.eval("window.__errors")

    assert idle["hidden"] is True
    assert reads >= 1
    assert shown["hidden"] is False
    assert shown["line"] == "Here Comes the Sun - The Beatles"
    assert shown["toggle"] == "Pause"
    assert shown["volume"] == "50%" and shown["up"] is False
    assert optimistic == "Play"
    assert puts[0][2] == {"volume_percent": 55}
    assert fresh_first
    assert {"method": "PUT", "url": "/api/spotify/volume", "body": {"percent": 55}} in sent
    assert not [c for c in sp.calls if c[1] == "/api/sound/volume"]
    assert changed["line"] == "Let It Be - The Beatles"
    assert fits is True
    assert errors == []
