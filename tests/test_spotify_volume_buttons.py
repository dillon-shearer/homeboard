import json

import pytest

from app import routes_sound, spotify
from tests.chrome import needs_chrome, needs_node
from tests.test_spotify import client, creds, data_dir, fake, linked
from tests.test_tv_screen import FakeSpotify, _show_screen, _sp_card, _sp_setup, _until
from tests.test_tv_spotify import _node, _playing

__all__ = ["client", "creds", "data_dir", "fake", "linked"]

FRESH = ("GET", "/api/spotify/now?fresh=1")


def test_fresh_now_skips_the_cache(linked, fake, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(spotify, "_clock", lambda: clock[0])
    linked.get("/api/spotify/now")
    clock[0] += 1
    linked.get("/api/spotify/now")
    assert fake.count("GET", "/me/player") == 1
    body = linked.get("/api/spotify/now?fresh=1").json()
    assert fake.count("GET", "/me/player") == 2
    assert body["volume"] == 40
    clock[0] += 1
    linked.get("/api/spotify/now")
    assert fake.count("GET", "/me/player") == 2


@needs_node
def test_volume_step_never_guesses():
    result = _node(
        "console.log(JSON.stringify([T.volumeStep(null, 5), T.volumeStep(undefined, -5),"
        " T.volumeStep('80', -5), T.volumeStep(80, -5), T.volumeStep(100, 5), T.volumeStep(0, -5)]))"
    )
    assert result == [None, None, None, 75, 100, 0]


class VolumeSpotify(FakeSpotify):
    def control(self, data_dir, method, path, payload=None, query=None):
        out = super().control(data_dir, method, path, payload, query)
        if path == "/me/player/volume":
            self.now = {**self.now, "volume": query["volume_percent"]}
        return out


@pytest.fixture
def sp(monkeypatch):
    fake_api = VolumeSpotify()
    monkeypatch.setattr(spotify, "now_playing", fake_api.now_playing)
    monkeypatch.setattr(spotify, "control", fake_api.control)
    monkeypatch.setattr(spotify, "play_playlist", fake_api.play_playlist)
    monkeypatch.setattr(spotify, "play_here", fake_api.play_here)
    monkeypatch.setattr(routes_sound, "helper", fake_api.helper)
    return fake_api


BUTTONS = """JSON.stringify([...document.querySelectorAll('.sp-vol-down, .sp-vol-up')]
  .map(b => b.disabled))"""


def _open(chrome, server, sp, volume):
    sp.now = _playing(volume=volume)
    ids = _sp_setup(server, [(2, 2)])
    chrome.size(1024, 600)
    _show_screen(server, ids[(2, 2)])
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    return _sp_card(chrome, "Music 2x2", lambda o: o["rows"] and o["speaker"] and o["vol"])


def _puts(sp):
    return [c[2]["volume_percent"] for c in sp.sent("PUT", "/api/spotify/volume")]


@needs_chrome
def test_unknown_volume_disables_buttons(chrome, server, sp):
    card = _open(chrome, server, sp, None)
    assert card["vol"] == "--%"
    assert json.loads(chrome.eval(BUTTONS)) == [True, True]
    chrome.eval("document.querySelector('.sp-vol-down').click()")
    chrome.eval("document.querySelector('.sp-vol-up').click()")
    chrome.wait("window.__inflight === 0")
    assert chrome.eval("window.__sent.length") == 0
    assert _puts(sp) == [] and FRESH not in sp.calls


@needs_chrome
def test_volume_down_reads_fresh_then_steps(chrome, server, sp):
    _open(chrome, server, sp, 80)
    assert json.loads(chrome.eval(BUTTONS)) == [False, False]
    chrome.eval("document.querySelector('.sp-vol-down').click()")
    assert _until(lambda: _puts(sp), bool) == [75]
    assert sp.calls.index(FRESH) < sp.calls.index(sp.sent("PUT", "/api/spotify/volume")[0])
    card = _sp_card(chrome, "Music 2x2", lambda o: o["vol"] == "75%")
    assert card["vol"] == "75%"
    assert chrome.eval("document.querySelector('.sp-volbar i').style.width") == "75%"
    sent = json.loads(chrome.eval("JSON.stringify(window.__sent)"))
    assert [c["url"] for c in sent] == ["/api/spotify/volume"]
    assert not [c for c in sp.calls if c[1] == "/api/sound/volume"]


@needs_chrome
def test_two_quick_taps_step_from_the_sent_value(chrome, server, sp):
    _open(chrome, server, sp, 80)
    chrome.eval(
        "document.querySelector('.sp-vol-down').click();"
        "document.querySelector('.sp-vol-down').click()"
    )
    assert _until(lambda: _puts(sp), lambda v: len(v) >= 2) == [75, 70]
    assert sp.calls.count(FRESH) == 1
    card = _sp_card(chrome, "Music 2x2", lambda o: o["vol"] == "70%")
    assert card["vol"] == "70%"
