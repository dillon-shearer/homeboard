import json
import subprocess

from tests.chrome import api, needs_chrome, needs_node
from tests.test_now_playing_cover import COVER, _js, _seed, _until, now  # noqa: F401
from tests.test_tv_spotify import SCREEN_JS, _playing, _png


@needs_node
def test_tv_polls_spotify_again_when_the_song_ends():
    script = f"""const S = require({json.dumps(str(SCREEN_JS))});
    const p = (o) => Object.assign({{ playing: true, progress_ms: 0, duration_ms: 200000 }}, o);
    console.log(JSON.stringify([
      S.nextPollMs(p({{}})),
      S.nextPollMs(p({{ progress_ms: 196000 }})),
      S.nextPollMs(p({{ progress_ms: 200000 }})),
      S.nextPollMs(p({{ playing: false, progress_ms: 196000 }})),
      S.nextPollMs(p({{ duration_ms: null }})),
      S.nextPollMs(null),
    ]));"""
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30, check=True)
    assert json.loads(out.stdout) == [15000, 5000, 1000, 15000, 15000, 15000]


def _open(chrome, server):
    chrome.size(1024, 600)
    chrome.send("Page.navigate", {"url": f"{server}/"})
    return _until(lambda: _js(chrome, COVER), lambda v: v and v["shown"])


@needs_chrome
def test_cover_follows_the_next_song_at_track_end(chrome, server, now):
    _seed(server)
    first = _png((200, 40, 40), 200)
    second = _png((40, 40, 200), 200)
    now["now"] = _playing(cover=first, progress_ms=180500, duration_ms=185000)
    c = _open(chrome, server)
    waiting = chrome.eval("window.__test.waiting(5500)")
    now["now"] = _playing(cover=second, progress_ms=1000, duration_ms=185000)
    chrome.eval("window.__test.fire(5500)")
    after = _until(lambda: _js(chrome, COVER), lambda v: v["src"] == second)
    assert c["src"] == first
    assert waiting == 1
    assert after["src"] == second and after["shown"] is True


@needs_chrome
def test_cover_is_fresh_when_the_board_comes_back_to_photos(chrome, server, now):
    sid = _seed(server)
    first = _png((200, 40, 40), 200)
    second = _png((40, 40, 200), 200)
    now["now"] = _playing(cover=first)
    c = _open(chrome, server)
    chrome.wait("window.__test.heard('hello') > 0")
    api.put(f"{server}/api/settings", json={"channel": "screen", "screen_id": sid})
    chrome.wait("document.getElementById('channel').style.transform === 'translateX(0px)'")
    now["now"] = _playing(cover=second)
    api.put(f"{server}/api/settings", json={"channel": "photos"})
    after = _until(lambda: _js(chrome, COVER), lambda v: v["src"] == second)
    assert c["src"] == first
    assert after["src"] == second
