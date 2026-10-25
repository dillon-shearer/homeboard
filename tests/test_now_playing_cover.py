import json
import re
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import spotify
from app.db import POSITIONS
from app.main import create_app
from tests import test_clock_caption_form as form
from tests.chrome import api, needs_chrome, needs_node
from tests.test_tv_spotify import _playing, _png

DISPLAY_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "display.js"
COVER_DEFAULTS = {
    "cover_size": 3,
    "cover_pos": "top-right",
    "cover_on_top": True,
    "cover_show": True,
}


def _node(body):
    script = f"const S = require({json.dumps(str(DISPLAY_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_cover_defaults_returned(client):
    s = client.get("/api/settings").json()
    assert {k: s[k] for k in COVER_DEFAULTS} == COVER_DEFAULTS


@pytest.mark.parametrize(
    "field,value",
    [
        ("cover_size", 2),
        ("cover_size", 12),
        ("cover_pos", "bottom-left"),
        ("cover_on_top", False),
        ("cover_show", False),
    ],
)
def test_cover_fields_round_trip(client, field, value):
    r = client.put("/api/settings", json={field: value})
    assert r.status_code == 200
    assert client.get("/api/settings").json()[field] == value


@pytest.mark.parametrize(
    "field,value",
    [("cover_size", 1), ("cover_size", 13), ("cover_size", "big"), ("cover_pos", "nowhere")],
)
def test_bad_cover_values_rejected(client, field, value):
    assert client.put("/api/settings", json={field: value}).status_code == 422
    assert client.get("/api/settings").json()[field] == COVER_DEFAULTS[field]


@needs_node
def test_cover_visibility_and_spotify_screen_helpers():
    r = _node(
        """
        const now = { linked: true, playing: true, on_board: true, cover: "/c.jpg" };
        const sp = [{ id: 3, widgets: [{ kind: "clock" }] }, { id: 7, widgets: [{ kind: "spotify" }] },
          { id: 9, widgets: [{ kind: "spotify" }] }];
        const none = [{ id: 3, widgets: [{ kind: "clock" }] }, { id: 4, widgets: [] }];
        console.log(JSON.stringify({
          shown: S.coverVisible(now, sp),
          unlinked: S.coverVisible({ ...now, linked: false }, sp),
          paused: S.coverVisible({ ...now, playing: false }, sp),
          elsewhere: S.coverVisible({ ...now, on_board: false }, sp),
          noScreen: S.coverVisible(now, none),
          noNow: S.coverVisible(null, sp),
          first: S.spotifyScreenId(sp),
          missing: S.spotifyScreenId(none),
          empty: S.spotifyScreenId(null),
        }));
        """
    )
    assert r == {
        "shown": True,
        "unlinked": False,
        "paused": False,
        "elsewhere": False,
        "noScreen": False,
        "noNow": False,
        "first": 7,
        "missing": None,
        "empty": None,
    }


@needs_node
def test_cover_settings_and_layout():
    r = _node(
        """
        const d = S.applySettings(null, null);
        const good = S.applySettings(d, { cover_size: 7, cover_pos: "middle-left", cover_on_top: false, cover_show: false });
        const bad = S.applySettings(good, { cover_size: 13, cover_pos: "nowhere" });
        console.log(JSON.stringify({ d, good, bad,
          below: S.overlayLayout({ cover_pos: "bottom-right", cover_on_top: false }) }));
        """
    )
    pick = lambda s: {k: s[k] for k in COVER_DEFAULTS}
    assert pick(r["d"]) == COVER_DEFAULTS
    want = {"cover_size": 7, "cover_pos": "middle-left", "cover_on_top": False, "cover_show": False}
    assert pick(r["good"]) == want
    assert pick(r["bad"]) == want
    assert r["below"]["coverCell"] == "bottom-right"
    assert r["below"]["order"] == ["clock", "date", "caption", "cover"]


COVER = """JSON.stringify((() => {
  const c = document.getElementById("cover");
  const r = c.getBoundingClientRect();
  return { hidden: c.hidden, cell: c.parentNode.id, src: c.getAttribute("src"),
    shown: !!c.offsetParent, x: r.left, y: r.top, w: r.width, h: r.height };
})())"""

FRAME = """JSON.stringify((() => {
  const f = document.getElementById("channel");
  return { left: f.getBoundingClientRect().left, sliding: f.classList.contains("slide") };
})())"""


@pytest.fixture
def now(monkeypatch):
    state = {"now": _playing(cover=_png((200, 40, 40), 200))}
    monkeypatch.setattr(spotify, "now_playing", lambda data_dir: state["now"])
    return state


def _seed(base):
    plain = api.post(f"{base}/api/screens", json={"name": "Kitchen"}).json()
    r = api.post(
        f"{base}/api/screens/{plain['id']}/widgets", json={"kind": "clock", "col": 0, "row": 0}
    )
    assert r.status_code == 201, r.text
    music = api.post(f"{base}/api/screens", json={"name": "Music"}).json()
    r = api.post(
        f"{base}/api/screens/{music['id']}/widgets", json={"kind": "spotify", "col": 0, "row": 0}
    )
    assert r.status_code == 201, r.text
    return music["id"]


def _until(fn, ready, tries=100):
    v = None
    for _ in range(tries):
        v = fn()
        if ready(v):
            return v
        time.sleep(0.05)
    return v


def _js(chrome, expr):
    return json.loads(chrome.eval(expr) or "null")


_clock = [0.0]


def _touch(chrome, kind, points):
    step = {"touchStart": 1, "touchEnd": 0.5}.get(kind, 0.02)
    _clock[0] = max(_clock[0] + step, time.time())
    chrome.send(
        "Input.dispatchTouchEvent", {"type": kind, "touchPoints": points, "timestamp": _clock[0]}
    )


@needs_chrome
def test_cover_shows_top_right_and_tap_opens_spotify_screen(chrome, server, now):
    sid = _seed(server)
    chrome.size(1024, 600)
    chrome.touch()
    chrome.send("Page.navigate", {"url": f"{server}/"})
    c = _until(lambda: _js(chrome, COVER), lambda v: v and v["shown"])
    chrome.wait("document.getElementById('splash').hidden")
    assert c["hidden"] is False and c["shown"] is True
    assert c["src"] == now["now"]["cover"]
    assert c["cell"] == "cell-top-right"
    assert c["w"] == pytest.approx(120, abs=1) and c["h"] == pytest.approx(120, abs=1)
    assert c["x"] + c["w"] > 1024 - 60 and c["y"] < 60
    assert _js(chrome, FRAME)["left"] >= 1024
    x, y = c["x"] + c["w"] / 2, c["y"] + c["h"] / 2
    _touch(chrome, "touchStart", [{"x": x, "y": y}])
    _touch(chrome, "touchEnd", [])
    s = _until(lambda: api.get(f"{server}/api/settings").json(), lambda v: v["channel"] == "screen")
    assert (s["channel"], s["screen_id"]) == ("screen", sid)
    sent = chrome.eval("JSON.stringify(window.__sent)")
    assert {
        "method": "PUT",
        "url": "/api/settings",
        "body": {"channel": "screen", "screen_id": sid},
    } in json.loads(sent)
    f = _until(lambda: _js(chrome, FRAME), lambda v: v["left"] == 0 and not v["sliding"])
    assert f["left"] == 0


@needs_chrome
def test_drag_starting_on_cover_still_swipes(chrome, server, now):
    _seed(server)
    chrome.size(1024, 600)
    chrome.touch()
    chrome.send("Page.navigate", {"url": f"{server}/"})
    c = _until(lambda: _js(chrome, COVER), lambda v: v and v["shown"])
    chrome.wait("document.getElementById('splash').hidden")
    x, y = c["x"] + c["w"] / 2, c["y"] + c["h"] / 2
    _touch(chrome, "touchStart", [{"x": x, "y": y}])
    for i in range(1, 11):
        _touch(chrome, "touchMove", [{"x": x - 40 * i, "y": y}])
    _touch(chrome, "touchEnd", [])
    s = _until(lambda: api.get(f"{server}/api/settings").json(), lambda v: v["channel"] == "screen")
    first = api.get(f"{server}/api/screens").json()[0]["id"]
    assert (s["channel"], s["screen_id"]) == ("screen", first)


@needs_chrome
def test_cover_not_in_layout_when_not_playing(chrome, server, now):
    _seed(server)
    now["now"] = _playing(playing=False, cover=_png((200, 40, 40), 200))
    chrome.size(1024, 600)
    chrome.touch()
    chrome.send("Page.navigate", {"url": f"{server}/"})
    chrome.wait("document.readyState === 'complete' && window.__test.heard('hello') > 0")
    chrome.run("window.__test.tick(15000)")
    c = _js(chrome, COVER)
    assert c["hidden"] is True and c["shown"] is False
    assert c["w"] == 0 and c["h"] == 0
    now["now"] = _playing(cover=_png((200, 40, 40), 200))
    chrome.run("window.__test.tick(15000)")
    shown = _until(lambda: _js(chrome, COVER), lambda v: v["shown"])
    assert shown["shown"] is True
    now["now"] = _playing(on_board=False, cover=_png((200, 40, 40), 200))
    chrome.run("window.__test.tick(15000)")
    gone = _until(lambda: _js(chrome, COVER), lambda v: not v["shown"])
    assert gone["hidden"] is True


def test_tv_page_has_now_playing_cover_row(client):
    group = form._group(client.get("/admin/tv").text, "group-overlay")
    assert '<label for="cover-size">Now playing cover</label>' in group
    for part in ('id="cover-size-minus"', 'id="cover-size"', 'id="cover-size-plus"'):
        assert part in group
    assert re.findall(r'name="cover_pos" value="([a-z-]+)"', group) == list(POSITIONS)
    assert re.findall(r'name="cover_on_top" value="(\w+)"', group) == ["true", "false"]
    assert 'id="cover-stack-row"' in group
    assert '<input type="checkbox" data-key="cover_show">' in group


COVER_CHECK = """
var coverCheck = new El("input");
coverCheck.setAttribute("data-key", "cover_show");
var byName = document.querySelectorAll;
document.querySelectorAll = function (sel) {
  return sel === ".checks input[data-key]" ? [coverCheck] : byName(sel);
};
"""


@needs_node
def test_cover_controls_put_cover_keys():
    r = form._node(
        """
        await flush();
        const before = { size: $("cover-size").value, spot: checkedValue("cover_pos"),
          top: checkedValue("cover_on_top"), show: coverCheck.checked, stack: $("cover-stack-row").hidden };
        tap("cover-size-plus");
        await wait(450);
        const spot = radio("cover_pos", "bottom-right");
        spot.checked = true;
        spot.fire("change");
        await flush();
        const stack = $("cover-stack-row").hidden;
        const below = radio("cover_on_top", "false");
        below.checked = true;
        below.fire("change");
        await flush();
        coverCheck.checked = false;
        coverCheck.fire("change");
        await flush();
        console.log(JSON.stringify({ before, stack, puts }));
        """,
        form.FORM_SETUP + COVER_CHECK,
    )
    assert r["before"] == {
        "size": 3,
        "spot": "top-right",
        "top": "true",
        "show": True,
        "stack": True,
    }
    assert r["stack"] is False
    assert r["puts"] == [
        {"cover_size": 4},
        {"cover_pos": "bottom-right"},
        {"cover_on_top": False},
        {"cover_show": False},
    ]
