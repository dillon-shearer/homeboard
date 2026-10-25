from app import events
from tests.chrome import needs_chrome
from tests.test_control_panel import DOWN, OPEN, _load, _swipe
from tests.test_now_playing_cover import _seed
from tests.test_sound_player import _open
from tests.test_tv_screen import _show_screen, _sp_card, _sp_setup, _sp_tap, _sp_until, fake
from tests.test_tv_spotify import _playing

__all__ = ["fake"]

AWAY = {"device": "Kitchen Phone", "on_board": False}


def _text(chrome, selector):
    return chrome.eval(f"document.querySelector('{selector}').innerText")


def _no_here(fake):
    return not fake.sent("POST", "/api/spotify/here")


@needs_chrome
def test_phone_names_other_device_and_play_here_transfers(chrome, server, fake):
    fake.now = _playing(**AWAY)
    _open(chrome, server)
    chrome.wait("document.querySelector('.player-device')?.textContent === 'On Kitchen Phone'")
    assert not chrome.eval("document.querySelector('[data-sp=\"here\"]').parentNode.hidden")
    _sp_tap(chrome, '[data-sp="next"]')
    _sp_until(lambda: fake.sent("POST", "/api/spotify/next"), "next")
    _sp_tap(chrome, '[data-sp="toggle"]')
    _sp_until(lambda: fake.sent("POST", "/api/spotify/pause"), "pause")
    assert _no_here(fake)
    _sp_tap(chrome, '[data-sp="here"]')
    _sp_until(lambda: fake.sent("POST", "/api/spotify/here"), "play here")

    fake.now = _playing()
    events.publish("spotify_changed")
    chrome.wait("document.querySelector('.player-device').hidden")
    assert chrome.eval("window.__errors") == []


@needs_chrome
def test_tv_names_other_device_at_every_size(chrome, server, fake):
    ids = _sp_setup(server)
    fake.now = _playing(**AWAY)
    chrome.size(1024, 600)
    _show_screen(server, ids[(2, 2)])
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    for (w, h), screen in ids.items():
        _show_screen(server, screen)
        out = _sp_card(chrome, f"Music {w}x{h}", lambda o: o["track"] == "Here Comes the Sun")
        assert out["out"] == [] and out["small"] == [], out
        assert "On Kitchen Phone" in _text(chrome, "#screen .tv-card[data-kind=spotify]")
        if (w, h) == (1, 1):
            assert out["toggle"] is None
            assert chrome.eval("!!document.querySelector('.sp-cover [data-sp=\"here\"]')")
        else:
            assert out["transport"] == ["previous", "toggle", "next", "here"]

    _show_screen(server, ids[(2, 2)])
    _sp_card(chrome, "Music 2x2", lambda o: o["transport"][-1:] == ["here"])
    _sp_tap(chrome, '[data-sp="next"]')
    _sp_until(lambda: fake.sent("POST", "/api/spotify/next"), "next")
    assert _no_here(fake)
    _sp_tap(chrome, '[data-sp="here"]')
    _sp_until(lambda: fake.sent("POST", "/api/spotify/here"), "play here")

    fake.now = _playing()
    events.publish("spotify_changed")
    full = _sp_card(chrome, "Music 2x2", lambda o: o["transport"] == ["previous", "toggle", "next"])
    assert "Kitchen Phone" not in _text(chrome, "#screen .tv-card[data-kind=spotify]")
    assert full["out"] == []
    assert chrome.eval("window.__errors") == []


@needs_chrome
def test_panel_names_other_device_and_play_here_transfers(chrome, server, fake):
    fake.now = _playing(**AWAY)
    _seed(server)
    _load(chrome, server)
    _swipe(chrome, 500, DOWN)
    chrome.wait(OPEN)
    chrome.wait("document.getElementById('cp-now-device').textContent === 'On Kitchen Phone'")
    assert not chrome.eval("document.getElementById('cp-now-here').hidden")
    assert chrome.eval(
        "(p => p.scrollHeight <= p.clientHeight)(document.getElementById('control-panel'))"
    )
    chrome.eval("document.getElementById('cp-now-next').click()")
    _sp_until(lambda: fake.sent("POST", "/api/spotify/next"), "next")
    assert _no_here(fake)
    chrome.eval("document.getElementById('cp-now-here').click()")
    _sp_until(lambda: fake.sent("POST", "/api/spotify/here"), "play here")

    fake.now = _playing()
    events.publish("spotify_changed")
    chrome.wait("document.getElementById('cp-now-here').hidden")
    assert chrome.eval("document.getElementById('cp-now-device').hidden")
    assert chrome.eval("window.__errors") == []
