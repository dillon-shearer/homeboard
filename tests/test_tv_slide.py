import json
import subprocess
import time
from pathlib import Path

import httpx

from tests.chrome import _Chrome, needs_chrome, needs_node, server  # noqa: F401
from tests.test_caption_lifetime import HARNESS

DISPLAY_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "display.js"


def _node(body):
    script = f"const S = require({json.dumps(str(DISPLAY_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


KIOSK = r"""
let idle = null;
globalThis.setInterval = (fn) => { idle = fn; return 1; };
globalThis.innerWidth = 1000;
const puts = [];
server.screens = [{id: 4, name: "Daily Brief"}, {id: 2, name: "Calendar"}];
const baseFetch = globalThis.fetch;
globalThis.fetch = (url, opts) => {
  if (opts && opts.method === "PUT" && url === "/api/settings") {
    const body = JSON.parse(opts.body);
    puts.push(body);
    server.settings = Object.assign({}, server.settings, body);
  }
  if (url === "/api/screens") {
    return Promise.resolve({ ok: true, json: () => Promise.resolve(server.screens) });
  }
  return baseFetch(url, opts);
};
const touch = {};
document.addEventListener = (k, fn) => { touch[k] = fn; };
const at = (x) => ({ changedTouches: [{ clientX: x, clientY: 300 }] });
const srcs = [];
els.channel = new Proxy(els.channel, {
  set(t, k, v) { if (k === "src") srcs.push(v); t[k] = v; return true; },
});
const tf = () => els.channel.style.transform;
"""


def _kiosk(body):
    script = (
        f"{HARNESS}\n{KIOSK}\nrequire({json.dumps(str(DISPLAY_JS))});\n"
        f"(async () => {{\n{body}\n}})();"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@needs_node
def test_commit_or_snap_and_target_stop():
    r = _node(
        """
        const stops = S.buildStops([{id: 4}, {id: 2}]);
        const photos = {channel: "photos", screen_id: null};
        console.log(JSON.stringify({
          slow40: S.swipeCommits(-40, 0, -0.05),
          fast40: S.swipeCommits(-40, 0, -1),
          back40: S.swipeCommits(-40, 0, 1),
          left120: S.swipeCommits(-120, 0, 0),
          right120: S.swipeCommits(120, 0, 0),
          steep: S.swipeCommits(-120, 200, 0),
          next: S.dragStop(stops, photos, -120),
          last: S.dragStop(stops, photos, 120),
        }));
        """
    )
    assert r["slow40"] is False
    assert r["fast40"] is True
    assert r["back40"] is False
    assert r["left120"] is True and r["right120"] is True
    assert r["steep"] is False
    assert r["next"] == {"channel": "screen", "screen_id": 4}
    assert r["last"] == {"channel": "screen", "screen_id": 2}


@needs_node
def test_drag_offset_for_each_edge():
    r = _node(
        """
        const stops = S.buildStops([{id: 4}, {id: 2}]);
        const w = 1000;
        const photos = {channel: "photos", screen_id: null};
        const first = {channel: "screen", screen_id: 4};
        const last = {channel: "screen", screen_id: 2};
        console.log(JSON.stringify([
          S.dragOffset(stops, photos, 0, w),
          S.dragOffset(stops, photos, -300, w),
          S.dragOffset(stops, photos, 300, w),
          S.dragOffset(stops, photos, -5000, w),
          S.dragOffset(stops, last, -300, w),
          S.dragOffset(stops, first, 300, w),
          S.dragOffset(stops, first, -300, w),
          S.dragOffset(stops, last, 300, w),
          S.dragOffset(stops, first, 0, w),
          S.dragOffset(S.buildStops([]), photos, -300, w),
        ]));
        """
    )
    assert r == [1000, 700, -700, 0, -300, 300, None, None, 0, None]


@needs_node
def test_photos_channel_keeps_tv_loaded_and_drag_follows_finger():
    r = _kiosk(
        """
        await advance(100);
        const start = {src: els.channel.getAttribute("src"), tf: tf()};
        touch.touchstart(at(800));
        touch.touchmove(at(760));
        const dragged = tf();
        touch.touchend(at(760));
        await advance(10);
        const snapping = els.channel.classList.contains("slide");
        await advance(300);
        const snapped = {tf: tf(), puts: puts.slice()};
        touch.touchstart(at(800));
        touch.touchmove(at(700));
        touch.touchend(at(680));
        await advance(10);
        const mid = {tf: tf(), puts: puts.slice()};
        touch.touchstart(at(800));
        touch.touchend(at(600));
        await advance(300);
        const onScreen = {tf: tf(), puts: puts.slice()};
        touch.touchstart(at(500));
        touch.touchmove(at(700));
        const out = tf();
        touch.touchend(at(700));
        await advance(300);
        console.log(JSON.stringify({start, dragged, snapping, snapped, mid, onScreen, out,
          end: tf(), puts, srcs}));
        """
    )
    assert r["start"] == {"src": "/tv", "tf": "translateX(100vw)"}
    assert r["dragged"] == "translateX(960px)"
    assert r["snapping"] is True
    assert r["snapped"] == {"tf": "translateX(100vw)", "puts": []}
    assert r["mid"] == {"tf": "translateX(0px)", "puts": []}
    assert r["onScreen"] == {
        "tf": "translateX(0px)",
        "puts": [{"channel": "screen", "screen_id": 4}],
    }
    assert r["out"] == "translateX(200px)"
    assert r["end"] == "translateX(100vw)"
    assert r["puts"][-1] == {"channel": "photos"}
    assert r["srcs"] == ["/tv"]
    assert "about:blank" not in r["srcs"]


@needs_node
def test_swipe_right_on_photos_drags_last_screen_in_from_the_left():
    r = _kiosk(
        """
        await advance(100);
        touch.touchstart(at(200));
        touch.touchmove(at(500));
        const dragged = tf();
        touch.touchend(at(500));
        await advance(300);
        console.log(JSON.stringify({dragged, puts, tf: tf()}));
        """
    )
    assert r["dragged"] == "translateX(-700px)"
    assert r["puts"] == [{"channel": "screen", "screen_id": 2}]
    assert r["tf"] == "translateX(0px)"


def _tx(chrome):
    m = chrome.eval("getComputedStyle(document.getElementById('channel')).transform")
    if m in (None, "none"):
        return 0.0
    return float(m[m.index("(") + 1 : -1].split(",")[4])


def _until(get, ok, tries=100):
    v = None
    for _ in range(tries):
        v = get()
        if ok(v):
            return v
        time.sleep(0.05)
    return v


def _move(chrome, kind, x):
    points = [] if kind == "touchEnd" else [{"x": x, "y": 300}]
    chrome.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})


READY = """(() => {
  const f = document.getElementById('channel');
  const d = f.contentDocument;
  return !!(d && d.readyState === 'complete' && d.location.pathname === '/tv'
    && performance.getEntriesByName(location.origin + '/api/screens').length);
})()"""


@needs_chrome
def test_kiosk_tv_window_follows_finger_in_headless_chrome(server, tmp_path):  # noqa: F811
    first = min(httpx.get(f"{server}/api/screens").json(), key=lambda s: s["sort_order"])
    chrome = _Chrome(tmp_path / "chrome", 1024, 600)
    try:
        chrome.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
        chrome.send("Page.navigate", {"url": f"{server}/"})
        _until(lambda: chrome.eval(READY), bool)
        time.sleep(0.3)
        rest = _tx(chrome)
        _move(chrome, "touchStart", 800)
        for i in range(1, 6):
            _move(chrome, "touchMove", 800 - 30 * i)
        mid = _until(lambda: _tx(chrome), lambda v: 0 < v < 1024, tries=40)
        for i in range(6, 11):
            _move(chrome, "touchMove", 800 - 30 * i)
        _move(chrome, "touchEnd", 500)
        landed = _until(
            lambda: httpx.get(f"{server}/api/settings").json(), lambda s: s["channel"] == "screen"
        )
        shown = _until(lambda: _tx(chrome), lambda v: v == 0)
        time.sleep(1)
        _move(chrome, "touchStart", 200)
        for i in range(1, 6):
            _move(chrome, "touchMove", 200 + 30 * i)
        out = _until(lambda: _tx(chrome), lambda v: v >= 150, tries=40)
        for i in range(6, 11):
            _move(chrome, "touchMove", 200 + 30 * i)
        _move(chrome, "touchEnd", 500)
        back = _until(
            lambda: httpx.get(f"{server}/api/settings").json(), lambda s: s["channel"] == "photos"
        )
        gone = _until(lambda: _tx(chrome), lambda v: v >= 1024)
    finally:
        chrome.close()
    assert rest >= 1024
    assert 0 < mid < 1024
    assert (landed["channel"], landed["screen_id"]) == ("screen", first["id"])
    assert shown == 0
    assert out == 150
    assert back["channel"] == "photos"
    assert gone >= 1024
