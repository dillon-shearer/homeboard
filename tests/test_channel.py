import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import events
from app.main import create_app
from tests.test_caption_lifetime import HARNESS

ROOT = Path(__file__).resolve().parent.parent / "app"
DISPLAY_JS = ROOT / "static" / "display.js"
JS = DISPLAY_JS.read_text(encoding="utf-8")

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


KIOSK = r"""
let idle = null;
globalThis.setInterval = (fn) => { idle = fn; return 1; };
const puts = [];
server.screens = [];
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
const swipe = (dx) => {
  touch.touchstart({ changedTouches: [{ clientX: 500, clientY: 300 }] });
  touch.touchend({ changedTouches: [{ clientX: 500 + dx, clientY: 300 }] });
};
let channelSets = 0;
els.channel = new Proxy(els.channel, {
  set(t, k, v) { if (k === "src") channelSets++; t[k] = v; return true; },
});
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


def test_defaults(client):
    body = client.get("/api/settings").json()
    assert (body["channel"], body["channel_timeout_min"]) == ("photos", 5)
    assert client.get("/api/status").json()["channel"] == "photos"


def test_round_trip(client):
    r = client.put("/api/settings", json={"channel": "screen", "channel_timeout_min": 10})
    assert r.status_code == 200
    assert (r.json()["channel"], r.json()["channel_timeout_min"]) == ("screen", 10)
    assert events.last_event == "settings_changed"
    body = client.get("/api/settings").json()
    assert (body["channel"], body["channel_timeout_min"]) == ("screen", 10)
    assert client.get("/api/status").json()["channel"] == "screen"


@pytest.mark.parametrize("minutes", [0, 2, 5, 10, 30])
def test_allowed_timeouts(client, minutes):
    r = client.put("/api/settings", json={"channel_timeout_min": minutes})
    assert r.status_code == 200
    assert r.json()["channel_timeout_min"] == minutes


@pytest.mark.parametrize(
    "body", [{"channel": "tv"}, {"channel_timeout_min": 7}, {"channel_timeout_min": -1}]
)
def test_invalid_is_422(client, body):
    assert client.put("/api/settings", json=body).status_code == 422
    after = client.get("/api/settings").json()
    assert (after["channel"], after["channel_timeout_min"]) == ("photos", 5)


def test_tv_page_markup(client):
    start = client.get("/admin").text
    assert ">Showing on the TV<" in start
    assert re.search(r'<button class="w95-btn"[^>]*data-channel="photos"[^>]*aria-pressed', start)
    assert 'data-channel="calendar"' not in start
    assert 'data-channel="lists"' not in start
    page = client.get("/admin/tv").text
    assert ">Showing on the TV<" not in page
    select = re.search(r'<select id="channel-timeout".*?</select>', page, re.DOTALL)
    assert select
    assert re.findall(r'<option value="(\d+)"', select.group(0)) == ["0", "2", "5", "10", "30"]
    assert ">Never<" in select.group(0)
    assert "of no touches on the TV and no edits from a phone" in page


def test_tv_js_puts_channel_and_timeout():
    js = (ROOT / "static" / "tv_admin.js").read_text(encoding="utf-8")
    assert "var body = { channel: channel };" in js and "putSettings(body)" in js
    assert 'if (channel === "screen") body.screen_id = parseInt(' in js
    assert "channel_timeout_min: parseInt" in js
    assert 'classList.toggle("on", on)' in js and "aria-pressed" in js


def test_display_has_channel_frame(client):
    page = client.get("/").text
    assert "<iframe" in page
    assert not re.search(r"<iframe[^>]*hidden", page)
    assert 'const url = "/tv";' in JS
    assert "channelEl.src = url" in JS


def test_activity_resets():
    connect = JS[JS.index("function connect()") : JS.index("function updateClock()")]
    hello = connect[connect.index('"hello"') : connect.index('"photos_changed"')]
    assert "markActivity" not in hello
    for name in ("settings_changed", "photos_changed", "lists_changed", "events_changed"):
        block = connect[connect.index(f'"{name}"') :].split("es.addEventListener")[0]
        assert "markActivity" in block, name
    touch = JS[JS.index("function onTouchStart") : JS.index("function onTouchEnd")]
    assert "markActivity()" in touch
    assert 'addEventListener("touchstart", onTouchStart' in JS
    assert 'addEventListener("touchend", onTouchEnd' in JS


PHOTOS = {"channel": "photos"}


@needs_node
def test_build_stops():
    result = _node(
        """
        console.log(JSON.stringify([S.buildStops([]), S.buildStops(null),
          S.buildStops([{id: 4, name: "Daily Brief"}, {id: 2, name: "Calendar"}])]));
        """
    )
    assert result[0] == [PHOTOS]
    assert result[1] == [PHOTOS]
    assert result[2] == [
        PHOTOS,
        {"channel": "screen", "screen_id": 4},
        {"channel": "screen", "screen_id": 2},
    ]


@needs_node
def test_next_stop_walks_and_wraps():
    result = _node(
        """
        const stops = S.buildStops([{id: 4}, {id: 2}]);
        const at = (channel, id) => ({channel: channel, screen_id: id === undefined ? null : id});
        console.log(JSON.stringify([
          S.nextStop(stops, at("photos"), 1),
          S.nextStop(stops, at("screen", 4), 1),
          S.nextStop(stops, at("screen", 2), 1),
          S.nextStop(stops, at("photos"), -1),
          S.nextStop(stops, at("screen", 2), -1),
          S.nextStop(stops, at("screen", 4), -1),
          S.nextStop(stops, at("screen", null), 1),
          S.nextStop(stops, at("screen", 99), -1),
          S.nextStop(stops, at("photos")),
        ]));
        """
    )
    assert result == [
        {"channel": "screen", "screen_id": 4},
        {"channel": "screen", "screen_id": 2},
        PHOTOS,
        {"channel": "screen", "screen_id": 2},
        {"channel": "screen", "screen_id": 4},
        PHOTOS,
        {"channel": "screen", "screen_id": 2},
        PHOTOS,
        {"channel": "screen", "screen_id": 4},
    ]


@needs_node
def test_next_stop_without_screens_stays_on_photos():
    result = _node(
        """
        const stops = S.buildStops([]);
        console.log(JSON.stringify([
          S.nextStop(stops, {channel: "photos"}, 1),
          S.nextStop(stops, {channel: "photos"}, -1),
          S.nextStop(stops, {channel: "screen", screen_id: null}, 1),
          S.nextStop(stops, {channel: "screen", screen_id: 3}, -1),
        ]));
        """
    )
    assert result == [PHOTOS, PHOTOS, PHOTOS, PHOTOS]


@needs_node
def test_should_return():
    result = _node(
        """
        const m = 60000, now = 100 * m;
        console.log(JSON.stringify([
          S.shouldReturn(now, now - 6 * m, 5, "screen"),
          S.shouldReturn(now, now - 4 * m, 5, "screen"),
          S.shouldReturn(now, now - 5 * m, 5, "screen"),
          S.shouldReturn(now, now - 60 * m, 0, "screen"),
          S.shouldReturn(now, now - 60 * m, 5, "photos"),
        ]));
        """
    )
    assert result == [True, False, False, False, False]


@needs_node
def test_apply_settings_keeps_channel_fields():
    result = _node(
        """
        const a = S.applySettings(null, null);
        const b = S.applySettings(a, {channel: "screen", channel_timeout_min: 0});
        const c = S.applySettings(b, {channel: "lists", channel_timeout_min: -3});
        const d = S.applySettings(c, {screen_id: 7});
        const e = S.applySettings(d, {screen_id: "x"});
        const f = S.applySettings(e, {screen_id: null});
        console.log(JSON.stringify([a.channel, a.channel_timeout_min, b.channel,
          b.channel_timeout_min, c.channel, c.channel_timeout_min, a.screen_id,
          d.screen_id, e.screen_id, f.screen_id, "lists_screen_id" in a]));
        """
    )
    assert result == ["photos", 5, "screen", 0, "screen", 0, None, 7, 7, None, False]


@needs_node
def test_kiosk_shows_frame_and_returns_to_photos_after_idle():
    result = _kiosk(
        """
        await advance(100);
        const es = sources[0];
        const frame = () => ({src: els.channel.src, hidden: els.channel.hidden,
          transform: els.channel.style.transform});
        const start = frame();
        server.settings = Object.assign({}, server.settings,
          {channel: "screen", screen_id: 4, channel_timeout_min: 2});
        es.emit("settings_changed");
        await advance(10);
        const picked = frame();
        await advance(60000);
        es.emit("hello");
        await advance(40000);
        idle();
        await advance(10);
        const early = {puts: puts.slice(), frame: frame()};
        await advance(25000);
        idle();
        await advance(10);
        const sliding = {puts: puts.slice(), slide: els.channel.classList.contains("slide")};
        await advance(300);
        console.log(JSON.stringify({start, picked, early, sliding, puts, end: frame(),
          channel: server.settings.channel}));
        """
    )
    off = {"src": "/tv", "hidden": False, "transform": "translateX(100vw)"}
    assert result["start"] == off
    assert result["picked"] == {"src": "/tv", "hidden": False, "transform": "translateX(0px)"}
    assert result["early"]["puts"] == []
    assert result["early"]["frame"]["src"] == "/tv"
    assert result["sliding"] == {"puts": [], "slide": True}
    assert result["puts"] == [{"channel": "photos"}]
    assert result["end"] == off
    assert result["channel"] == "photos"


@needs_node
def test_kiosk_swipe_walks_screens_and_puts_screen_id():
    result = _kiosk(
        """
        await advance(100);
        server.screens = [{id: 4, name: "Daily Brief"}, {id: 2, name: "Calendar"}];
        sources[0].emit("lists_changed");
        await advance(10);
        swipe(-200);
        await advance(300);
        const first = {src: els.channel.src, sets: channelSets};
        swipe(-200);
        await advance(300);
        const second = {src: els.channel.src, sets: channelSets};
        swipe(-200);
        await advance(300);
        const back = els.channel.src;
        swipe(200);
        await advance(300);
        swipe(200);
        await advance(300);
        swipe(200);
        await advance(300);
        swipe(30);
        await advance(300);
        console.log(JSON.stringify({first, second, back, puts, src: els.channel.src}));
        """
    )
    assert result["puts"] == [
        {"channel": "screen", "screen_id": 4},
        {"channel": "screen", "screen_id": 2},
        {"channel": "photos"},
        {"channel": "screen", "screen_id": 2},
        {"channel": "screen", "screen_id": 4},
        {"channel": "photos"},
    ]
    assert result["first"]["src"] == "/tv"
    assert result["second"] == result["first"]
    assert result["back"] == "/tv"
    assert result["src"] == "/tv"


@needs_node
def test_kiosk_reloads_screens_on_lists_changed():
    result = _kiosk(
        """
        await advance(100);
        swipe(-200);
        await advance(300);
        server.screens = [{id: 9, name: "Kitchen"}];
        sources[0].emit("lists_changed");
        await advance(10);
        swipe(-200);
        await advance(300);
        swipe(-200);
        await advance(300);
        console.log(JSON.stringify(puts));
        """
    )
    assert result == [
        {"channel": "screen", "screen_id": 9},
        {"channel": "photos"},
    ]


def test_screen_id_round_trip(client):
    screen = client.get("/api/screens").json()[0]["id"]
    r = client.put("/api/settings", json={"channel": "screen", "screen_id": screen})
    assert r.status_code == 200
    assert (r.json()["channel"], r.json()["screen_id"]) == ("screen", screen)
    status = client.get("/api/status").json()
    assert (status["channel"], status["screen_id"]) == ("screen", screen)
