from app import spotify
from tests.chrome import needs_chrome
from tests.test_tv_screen import _show_screen, _sp_card, _sp_setup, _sp_tap, _sp_until, fake  # noqa: F401


@needs_chrome
def test_no_device_shows_play_here_and_taps_play(chrome, server, fake):  # noqa: F811
    ids = _sp_setup(server)
    fake.now = spotify._empty()
    chrome.size(1024, 600)
    _show_screen(server, ids[(2, 2)])
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    full = _sp_card(chrome, "Music 2x2", lambda o: o["rows"] and o["transport"] == ["here"])
    label = chrome.eval("document.querySelector('[data-sp=\"here\"]').textContent")
    assert label == "Play here" and full["msg"] is None and full["toggle"] is None
    assert full["out"] == [] and full["small"] == []
    _sp_tap(chrome, '[data-sp="here"]')
    _sp_until(lambda: fake.sent("POST", "/api/spotify/play"), "the play call")
    assert not fake.sent("POST", "/api/spotify/pause")
    assert _sp_card(chrome, "Music 2x2", lambda o: True)["msg"] is None

    fake.now = _with_device()
    for (w, h), screen in ids.items():
        _show_screen(server, screen)
        out = _sp_card(chrome, f"Music {w}x{h}", lambda o: o["transport"] == [])
        assert out["track"] == "Nothing playing" and not chrome.eval(
            "!!document.querySelector('[data-sp=\"here\"]')"
        )

    fake.now = spotify._empty()
    _show_screen(server, ids[(1, 1)])
    chrome.wait("!!document.querySelector('.sp-cover [data-sp=\"here\"]')")
    tile = _sp_card(chrome, "Music 1x1", lambda o: o["track"] == "Nothing playing")
    assert tile["out"] == [] and tile["small"] == []


def _with_device():
    return {**spotify._empty(), "device": "Phone"}
