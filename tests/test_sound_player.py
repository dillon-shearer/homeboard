import json

from app import events
from tests.chrome import api, needs_chrome
from tests.test_tv_screen import SP_SHOWN, _sp_until, fake
from tests.test_tv_spotify import _playing

__all__ = ["fake"]

FRESH = ("GET", "/api/spotify/now?fresh=1")

PLAYER = """JSON.stringify((() => {
  const q = s => document.querySelector(s);
  const t = s => q(s) ? q(s).textContent : null;
  return {
    track: t('.player-track'), artist: t('.player-artist'),
    cover: q('.player-cover img') ? q('.player-cover img').src : null,
    toggle: t('[data-sp="toggle"]'), vol: t('.volume-value'),
    sp: document.querySelectorAll('[data-sp]').length,
    tiles: [...document.querySelectorAll('[data-playlist]')].map(b => b.getAttribute('data-playlist')),
  };
})())"""


def _player(chrome):
    return json.loads(chrome.eval(PLAYER))


def _open(chrome, server, width=390):
    chrome.size(width, 844)
    chrome.send("Page.navigate", {"url": f"{server}/admin/sound"})
    chrome.wait("__test.heard('hello') > 0")


def _playing_page(chrome, server, fake, **over):
    fake.now = _playing(**over)
    _open(chrome, server)
    chrome.wait("document.querySelector('.player-track')?.textContent === 'Here Comes the Sun'")


@needs_chrome
def test_not_linked_shows_settings_link_and_no_controls(chrome, server, fake):
    fake.now = {"linked": False, "reason": "not linked"}
    _open(chrome, server)
    chrome.wait("!document.getElementById('not-linked').hidden")
    link = chrome.eval("document.querySelector('#not-linked a').getAttribute('href')")
    assert link == "/admin/settings"
    assert _player(chrome)["sp"] == 0
    assert chrome.eval("document.getElementById('speaker-group').hidden")
    assert chrome.eval("document.getElementById('playlists-group').hidden")
    assert chrome.eval("window.__errors") == []


@needs_chrome
def test_playing_shows_track_cover_and_pause(chrome, server, fake):
    _playing_page(chrome, server, fake)
    out = _player(chrome)
    assert out["track"] == "Here Comes the Sun" and out["artist"] == "The Beatles"
    assert out["cover"] == fake.now["cover"]
    assert out["toggle"] == "Pause" and out["vol"] == "40%"
    assert chrome.eval("document.querySelector('[data-sp=\"here\"]').parentNode.hidden")
    small = chrome.eval(
        "[...document.querySelectorAll('.player-transport [data-sp]')]"
        ".filter(b => b.offsetParent && b.getBoundingClientRect().height < 48).length"
    )
    assert small == 0
    chrome.eval("document.querySelector('[data-sp=\"toggle\"]').click()")
    assert _player(chrome)["toggle"] == "Play"
    _sp_until(lambda: fake.sent("POST", "/api/spotify/pause"), "the pause call")
    assert chrome.eval("window.__errors") == []


@needs_chrome
def test_play_here_when_playing_elsewhere(chrome, server, fake):
    _playing_page(chrome, server, fake, on_board=False, device="Phone")
    chrome.wait("!document.querySelector('[data-sp=\"here\"]').parentNode.hidden")
    chrome.eval("document.querySelector('[data-sp=\"here\"]').click()")
    _sp_until(lambda: fake.sent("POST", "/api/spotify/play"), "the play call")


@needs_chrome
def test_next_posts_next(chrome, server, fake):
    _playing_page(chrome, server, fake)
    chrome.eval("document.querySelector('[data-sp=\"next\"]').click()")
    _sp_until(lambda: fake.sent("POST", "/api/spotify/next"), "the next call")
    sent = json.loads(chrome.eval("JSON.stringify(window.__sent)"))
    assert [c["url"] for c in sent] == ["/api/spotify/next"]


@needs_chrome
def test_volume_up_reads_fresh_then_puts(chrome, server, fake):
    _playing_page(chrome, server, fake, volume=50)
    chrome.eval("document.querySelector('[data-sp=\"vol-up\"]').click()")
    _sp_until(lambda: fake.sent("PUT", "/api/spotify/volume"), "the volume call")
    put = fake.sent("PUT", "/api/spotify/volume")[0]
    assert put[2] == {"volume_percent": 55}
    assert fake.calls.index(FRESH) < fake.calls.index(put)
    sent = json.loads(chrome.eval("JSON.stringify(window.__sent)"))
    assert sent == [{"method": "PUT", "url": "/api/spotify/volume", "body": {"percent": 55}}]
    assert _player(chrome)["vol"] == "55%"


@needs_chrome
def test_unknown_volume_disables_controls(chrome, server, fake):
    _playing_page(chrome, server, fake, volume=None)
    assert _player(chrome)["vol"] == "--%"
    disabled = chrome.eval(
        "[...document.querySelectorAll('.player-volume button, .player-volume input')]"
        ".every(b => b.disabled)"
    )
    assert disabled is True


@needs_chrome
def test_playlist_tile_plays_it(chrome, server, fake):
    api.put(f"{server}/api/spotify/shown", json=SP_SHOWN).raise_for_status()
    _playing_page(chrome, server, fake)
    chrome.wait(f"document.querySelectorAll('[data-playlist]').length === {len(SP_SHOWN)}")
    assert _player(chrome)["tiles"] == [p["id"] for p in SP_SHOWN]
    assert chrome.eval("document.getElementById('playlists-none').hidden")
    chrome.eval("document.querySelector('[data-playlist=\"pl-dinner\"]').click()")
    _sp_until(lambda: fake.sent("POST", "/api/spotify/play-playlist"), "the playlist call")
    assert fake.sent("POST", "/api/spotify/play-playlist") == [
        ("POST", "/api/spotify/play-playlist", "pl-dinner")
    ]


@needs_chrome
def test_no_shown_playlists_points_to_settings(chrome, server, fake):
    _playing_page(chrome, server, fake)
    chrome.wait("!document.getElementById('playlists-none').hidden")
    link = chrome.eval("document.querySelector('#playlists-none a').getAttribute('href')")
    assert link == "/admin/settings"


@needs_chrome
def test_spotify_changed_updates_title_and_cover(chrome, server, fake):
    _playing_page(chrome, server, fake)
    chrome.eval("window.__marker = 1")
    fake.now = _playing(track="Something", cover=SP_SHOWN[0]["cover"])
    events.publish("spotify_changed")
    chrome.wait("document.querySelector('.player-track').textContent === 'Something'")
    assert _player(chrome)["cover"] == SP_SHOWN[0]["cover"]
    assert chrome.eval("window.__marker") == 1


@needs_chrome
def test_fits_a_360px_phone(chrome, server, fake):
    api.put(f"{server}/api/spotify/shown", json=SP_SHOWN).raise_for_status()
    fake.now = _playing()
    _open(chrome, server, 360)
    chrome.wait("document.querySelectorAll('[data-playlist]').length > 0")
    assert chrome.eval("document.scrollingElement.scrollWidth") <= 360
    assert chrome.eval("window.__errors") == []
