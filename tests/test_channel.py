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
const baseFetch = globalThis.fetch;
globalThis.fetch = (url, opts) => {
  if (opts && opts.method === "PUT" && url === "/api/settings") {
    const body = JSON.parse(opts.body);
    puts.push(body);
    server.settings = Object.assign({}, server.settings, body);
  }
  return baseFetch(url, opts);
};
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
    r = client.put("/api/settings", json={"channel": "lists", "channel_timeout_min": 10})
    assert r.status_code == 200
    assert (r.json()["channel"], r.json()["channel_timeout_min"]) == ("lists", 10)
    assert events.last_event == "settings_changed"
    body = client.get("/api/settings").json()
    assert (body["channel"], body["channel_timeout_min"]) == ("lists", 10)
    assert client.get("/api/status").json()["channel"] == "lists"


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


def test_start_screen_markup(client):
    page = client.get("/admin").text
    assert ">TV channel<" in page
    for name in ("photos", "calendar", "lists"):
        assert re.search(
            rf'<button class="w95-btn"[^>]*data-channel="{name}"[^>]*aria-pressed', page
        )
    select = re.search(r'<select id="channel-timeout".*?</select>', page, re.DOTALL)
    assert select
    assert re.findall(r'<option value="(\d+)"', select.group(0)) == ["0", "2", "5", "10", "30"]
    assert ">Never<" in select.group(0)
    assert "of no touches on the TV and no edits from a phone" in page
    assert 'id="display-state"' in page


def test_start_js_puts_channel_and_timeout():
    js = (ROOT / "static" / "start.js").read_text(encoding="utf-8")
    assert "putSettings({ channel: channel })" in js
    assert "channel_timeout_min: parseInt" in js
    assert 'classList.toggle("on", on)' in js and "aria-pressed" in js


def test_display_has_channel_frame(client):
    page = client.get("/").text
    assert '<iframe id="channel"' in page
    assert re.search(r'<iframe id="channel"[^>]*hidden', page)
    assert '<div id="swipe" hidden>' in page
    assert 'const url = "/tv/" + channel;' in JS
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


@needs_node
def test_next_channel():
    result = _node(
        "console.log(JSON.stringify(['photos', 'calendar', 'lists']"
        ".map(c => [S.nextChannel(c), S.nextChannel(c, -1)])))"
    )
    assert result == [["calendar", "lists"], ["lists", "photos"], ["photos", "calendar"]]


@needs_node
def test_should_return():
    result = _node(
        """
        const m = 60000, now = 100 * m;
        console.log(JSON.stringify([
          S.shouldReturn(now, now - 6 * m, 5, "calendar"),
          S.shouldReturn(now, now - 6 * m, 5, "lists"),
          S.shouldReturn(now, now - 4 * m, 5, "calendar"),
          S.shouldReturn(now, now - 5 * m, 5, "calendar"),
          S.shouldReturn(now, now - 60 * m, 0, "calendar"),
          S.shouldReturn(now, now - 60 * m, 5, "photos"),
        ]));
        """
    )
    assert result == [True, True, False, False, False, False]


@needs_node
def test_apply_settings_keeps_channel_fields():
    result = _node(
        """
        const a = S.applySettings(null, null);
        const b = S.applySettings(a, {channel: "lists", channel_timeout_min: 0});
        const c = S.applySettings(b, {channel: "tv", channel_timeout_min: -3});
        console.log(JSON.stringify([a.channel, a.channel_timeout_min, b.channel,
          b.channel_timeout_min, c.channel, c.channel_timeout_min]));
        """
    )
    assert result == ["photos", 5, "lists", 0, "lists", 0]


@needs_node
def test_kiosk_shows_frame_and_returns_to_photos_after_idle():
    result = _kiosk(
        """
        await advance(100);
        const es = sources[0];
        const frame = () => ({src: els.channel.src, hidden: els.channel.hidden,
          swipe: els.swipe.hidden});
        const start = frame();
        server.settings = Object.assign({}, server.settings,
          {channel: "calendar", channel_timeout_min: 2});
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
        console.log(JSON.stringify({start, picked, early, puts, end: frame(),
          channel: server.settings.channel}));
        """
    )
    assert result["start"] == {"src": "about:blank", "hidden": True, "swipe": True}
    assert result["picked"] == {"src": "/tv/calendar", "hidden": False, "swipe": False}
    assert result["early"]["puts"] == []
    assert result["early"]["frame"]["src"] == "/tv/calendar"
    assert result["puts"] == [{"channel": "photos"}]
    assert result["end"] == {"src": "about:blank", "hidden": True, "swipe": True}
    assert result["channel"] == "photos"
