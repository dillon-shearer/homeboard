import pytest

from tests.chrome import needs_chrome
from tests.test_tv_screen import _json, _show_screen, _sp_card, _sp_setup, _until, fake  # noqa: F401

COLUMNS = """JSON.stringify((() => {
  const c = document.querySelector('#screen .tv-card[data-kind="spotify"] .sp-full');
  if (!c) return null;
  const r = s => { const n = s === 'card' ? c : c.querySelector(s); if (!n) return null;
    const b = n.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom]; };
  const names = ['.sp-cover', '.sp-track', '.sp-artist', '.sp-progress', '[data-sp="previous"]',
    '[data-sp="toggle"]', '[data-sp="next"]', '.sp-volume', '.sp-vol', '[data-sp="speaker"]', '.sp-side',
    '.sp-pls'];
  const out = {};
  names.forEach(s => { out[s] = r(s); });
  out.card = r('card');
  return out;
})())"""

MIDDLE = ['.sp-progress', '[data-sp="previous"]', '[data-sp="toggle"]', '[data-sp="next"]', '.sp-volume',
          '.sp-vol', '[data-sp="speaker"]']


@needs_chrome
@pytest.mark.parametrize("size", [(1024, 600), (1920, 1080)])
def test_full_spotify_has_cover_controls_playlists_columns(chrome, server, fake, size):  # noqa: F811
    ids = _sp_setup(server, sizes=[(2, 2)])
    chrome.size(*size)
    _show_screen(server, ids[(2, 2)])
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    full = _sp_card(chrome, "Music 2x2", lambda o: o["rows"] and o["speaker"] and o["coverImg"])
    assert full["out"] == [] and full["small"] == []
    box = _until(lambda: _json(chrome, COLUMNS), lambda o: o and all(o.values()))
    assert box and all(box.values()), box
    cover, track, artist = box[".sp-cover"], box[".sp-track"], box[".sp-artist"]
    left_edge = cover[2]
    assert track[1] >= cover[3] - 1 and artist[1] >= track[3] - 1
    assert track[0] >= cover[0] - 1 and track[2] <= cover[2] + 1 and artist[2] <= cover[2] + 1
    side = box[".sp-side"]
    for name in MIDDLE:
        b = box[name]
        assert b[0] >= left_edge and b[2] <= side[0], (name, b, left_edge, side)
    assert box[".sp-pls"][0] >= side[0] and side[2] <= box["card"][2] + 1


@needs_chrome
def test_full_spotify_long_title_stays_inside_the_card(chrome, server, fake):  # noqa: F811
    ids = _sp_setup(server, sizes=[(2, 2)])
    long = "A Very Long Song Title That Wraps Onto Two Lines And Then Some More Words"
    fake.now = {**fake.now, "track": long, "artist": "An Artist With A Long Name Too"}
    chrome.size(1024, 600)
    _show_screen(server, ids[(2, 2)])
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    full = _sp_card(chrome, "Music 2x2", lambda o: o["rows"] and o["track"] == long and o["coverImg"])
    assert full["out"] == [] and full["small"] == []
    box = _until(lambda: _json(chrome, COLUMNS), lambda o: o and all(o.values()))
    assert box[".sp-artist"][3] <= box["card"][3] + 1, box
