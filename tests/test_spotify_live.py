import time

import pytest

from app import events, spotify
from tests.chrome import needs_chrome
from tests.test_now_playing_cover import COVER, _js, _seed
from tests.test_now_playing_cover import _until as _cover_until
from tests.test_tv_screen import (
    _show_screen,
    _sp_card,
    _sp_setup,
    _until,
    fake,  # noqa: F401
)
from tests.test_tv_spotify import _playing, _png


def _publish_and_time(count):
    before = count()
    start = time.monotonic()
    events.publish("spotify_changed")
    got = _until(count, lambda n: n > before, tries=40)
    assert got > before, "no /api/spotify/now fetch after spotify_changed"
    return time.monotonic() - start


@needs_chrome
def test_tv_fetches_now_on_spotify_changed(chrome, server, fake):  # noqa: F811
    ids = _sp_setup(server, [(2, 2)])
    chrome.size(1024, 600)
    _show_screen(server, ids[(2, 2)])
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    _sp_card(chrome, "Music 2x2", lambda o: o["track"] == "Here Comes the Sun")
    chrome.wait("__test.waiting(15000) > 0 && window.__inflight === 0")

    def count():
        return len(fake.sent("GET", "/api/spotify/now"))

    fake.now = _playing(track="Two")
    assert _publish_and_time(count) < 1
    _sp_card(chrome, "Music 2x2", lambda o: o["track"] == "Two")
    assert chrome.eval("__test.waiting(15000)") == 1


@needs_chrome
def test_tv_polls_every_15_seconds_without_events(chrome, server, fake):  # noqa: F811
    ids = _sp_setup(server, [(2, 1)])
    chrome.size(1024, 600)
    _show_screen(server, ids[(2, 1)])
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    _sp_card(chrome, "Music 2x1", lambda o: o["track"] == "Here Comes the Sun")
    chrome.wait("__test.waiting(15000) > 0 && window.__inflight === 0")
    assert chrome.eval("__test.waiting(3000) + __test.waiting(5000)") == 0
    first = len(fake.sent("GET", "/api/spotify/now"))
    chrome.eval("__test.tick(1000)")
    time.sleep(0.5)
    assert len(fake.sent("GET", "/api/spotify/now")) == first
    chrome.eval("__test.fire(15000)")
    got = _until(lambda: len(fake.sent("GET", "/api/spotify/now")), lambda n: n > first)
    assert got == first + 1


@pytest.fixture
def counted(monkeypatch):
    state = {"now": _playing(cover=_png((200, 40, 40), 200)), "calls": 0}

    def now_playing(data_dir):
        state["calls"] += 1
        return state["now"]

    monkeypatch.setattr(spotify, "now_playing", now_playing)
    return state


@needs_chrome
def test_photo_cover_fetches_now_on_spotify_changed(chrome, server, counted):
    _seed(server)
    chrome.size(1024, 600)
    chrome.send("Page.navigate", {"url": f"{server}/"})
    c = _cover_until(lambda: _js(chrome, COVER), lambda v: v and v["shown"])
    assert c["shown"]
    chrome.wait("window.__inflight === 0")
    counted["now"] = _playing(cover=_png((40, 200, 40), 200))
    assert _publish_and_time(lambda: counted["calls"]) < 1
    src = counted["now"]["cover"]
    c = _cover_until(lambda: _js(chrome, COVER), lambda v: v["src"] == src)
    assert c["src"] == src
