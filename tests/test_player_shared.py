import json
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.chrome import needs_node

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
PLAYER_JS = STATIC / "player.js"
SURFACES = ["sound.js", "settings.js", "settings_sound.js", "display.js", "tv_screen.js"]

HARNESS = """
const P = require(%s);
const calls = [];
let replies = {};
let gate = null;
globalThis.fetch = function (url, opts) {
  calls.push({ url: url, method: (opts && opts.method) || "GET", body: opts && opts.body });
  const reply = replies[url];
  const wait = gate && url.indexOf("/api/spotify/volume") === 0 ? gate : Promise.resolve();
  return wait.then(function () {
    if (reply === "down") throw new TypeError("network");
    const r = reply || { status: 200, body: {} };
    return { ok: r.status < 400, status: r.status, json: function () { return Promise.resolve(r.body); } };
  });
};
function flush() { return new Promise(function (r) { setTimeout(r, 0); }); }
"""


def _node(body):
    script = (
        HARNESS % json.dumps(str(PLAYER_JS))
        + "(async function () {\n"
        + body
        + "\n})().catch(function (e) { console.error(e); process.exit(1); });"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


@pytest.mark.parametrize(
    "path,script",
    [
        ("/admin/sound", "sound.js"),
        ("/admin/settings", "settings.js"),
        ("/admin/settings", "settings_sound.js"),
        ("/", "display.js"),
        ("/tv", "tv_screen.js"),
    ],
)
def test_every_surface_loads_the_shared_player_first(client, path, script):
    page = client.get(path).text
    shared = page.index("/static/player.js?v=")
    assert shared < page.index(f"/static/{script}?v=")


def test_surfaces_keep_no_private_copy_of_the_shared_code():
    for name in SURFACES:
        src = (STATIC / name).read_text(encoding="utf-8")
        assert "new EventSource" not in src, name
        assert not re.search(
            r"function (getJSON|fetchJson|send|holdStep|volumeStep|freshVolume)\(", src
        ), name
        assert 'require("./player.js")' in src, name


@needs_node
def test_get_json_rejects_with_the_status():
    out = _node("""
replies["/a"] = { status: 503, body: { detail: "x" } };
replies["/b"] = { status: 200, body: { v: 1 } };
const err = await P.getJSON("/a").catch(function (e) { return e; });
const ok = await P.getJSON("/b");
console.log(JSON.stringify([err.status, ok.v, calls[0].method]));
""")
    assert out == [503, 1, "GET"]


@needs_node
def test_send_rejects_with_the_server_detail_or_a_fallback():
    out = _node("""
replies["/a"] = { status: 409, body: { detail: "Nope" } };
replies["/b"] = { status: 500, body: {} };
const a = await P.send("PUT", "/a", { x: 1 }).catch(function (e) { return e; });
const b = await P.send("POST", "/b").catch(function (e) { return e; });
console.log(JSON.stringify([a.message, a.status, b.message, calls[0].body, calls[1].body]));
""")
    assert out == ["Nope", 409, "Could not save", '{"x":1}', "{}"]


@needs_node
def test_commands_run_in_order_and_report_errors():
    out = _node("""
const notes = [];
let afters = 0;
const call = P.commands(function (t) { notes.push(t); }, function () { afters++; });
replies["/api/spotify/next"] = { status: 409, body: { detail: "No active device" } };
replies["/api/spotify/pause"] = { status: 500, body: { detail: "boom" } };
replies["/api/spotify/play"] = "down";
let release;
gate = new Promise(function (r) { release = r; });
call("PUT", "/api/spotify/volume", { percent: 40 });
call("POST", "/api/spotify/next");
await flush();
const before = calls.map(function (c) { return c.url; });
release();
await call("POST", "/api/spotify/pause");
await call("POST", "/api/spotify/play");
console.log(JSON.stringify([before, calls.map(function (c) { return c.url; }), notes, afters]));
""")
    before, urls, notes, afters = out
    assert before == ["/api/spotify/volume"]
    assert urls == [
        "/api/spotify/volume",
        "/api/spotify/next",
        "/api/spotify/pause",
        "/api/spotify/play",
    ]
    assert notes == ["No active device", "Could not reach Spotify", "Could not reach Spotify"]
    assert afters == 4


CHAIN = """
const notes = [];
const shown = { volume: %s };
let skips = 0;
const call = P.commands(function (t) { notes.push(t); }, function () {});
const tap = P.volumeChain({
  shown: function () { return shown.volume; },
  fresh: function () { return P.freshVolume(); },
  skip: function () { skips++; },
  set: function (v) { shown.volume = v; },
  put: function (v) { return call("PUT", "/api/spotify/volume", { percent: v }); },
});
let release;
function close() { gate = new Promise(function (r) { release = r; }); }
function open() { gate = null; release(); }
function puts() {
  return calls.filter(function (c) { return c.url === "/api/spotify/volume"; })
    .map(function (c) { return JSON.parse(c.body).percent; });
}
function urls() { return calls.map(function (c) { return c.url; }); }
const realNow = Date.now;
function ago(ms) { Date.now = function () { return realNow() - ms; }; P.volumeSeen(); Date.now = realNow; }
"""


def _chain(volume, body):
    return _node(CHAIN % json.dumps(volume) + body)


@needs_node
def test_taps_step_a_local_target_with_one_put_in_flight():
    out = _chain(
        50,
        """
ago(0);
close();
const done = tap(5); tap(5); tap(5);
await flush();
const during = [shown.volume, puts()];
open();
await done;
console.log(JSON.stringify([during, puts(), shown.volume, urls().indexOf("/api/spotify/now?fresh=1")]));
""",
    )
    during, sent, final, fresh = out
    assert during == [65, [55]]
    assert sent == [55, 65]
    assert final == 65
    assert fresh == -1


@needs_node
def test_a_burst_lands_on_the_sum_of_every_tap():
    out = _chain(
        50,
        """
ago(0);
close();
const steps = [5, 5, 5, -5, 5, -5, 5];
let i = 0;
let last = tap(steps[i++]);
await flush();
const running = [];
while (i < steps.length) {
  last = tap(steps[i++]);
  running.push(shown.volume);
  if (i === 4) { open(); await flush(); close(); }
}
open();
const end = await last;
console.log(JSON.stringify([puts(), shown.volume, end, running]));
""",
    )
    sent, final, last, running = out
    assert sent[-1] == 65 and final == 65 and last == 65
    assert len(sent) <= 3
    assert running == [60, 65, 60, 65, 60, 65]


@needs_node
def test_puts_never_overshoot_the_running_target():
    out = _chain(
        50,
        """
ago(0);
close();
const seen = [];
const done = tap(5);
seen.push(shown.volume);
tap(5); seen.push(shown.volume);
open(); await flush(); close();
tap(-5); seen.push(shown.volume);
open();
await done;
console.log(JSON.stringify([puts(), seen, shown.volume]));
""",
    )
    sent, seen, final = out
    assert sent == [55, 60, 55]
    assert seen == [55, 60, 55]
    assert final == 55


@needs_node
def test_recent_volume_skips_the_fresh_read():
    out = _chain(
        50,
        """
ago(2000);
await tap(5);
console.log(JSON.stringify([urls(), puts()]));
""",
    )
    assert out == [["/api/spotify/volume"], [55]]


@needs_node
@pytest.mark.parametrize("age,volume", [(11000, 50), (None, None)])
def test_old_or_unknown_volume_reads_fresh_first(age, volume):
    out = _chain(
        volume,
        """
replies["/api/spotify/now?fresh=1"] = { status: 200, body: { volume: 70 } };
%s
const v = await tap(5);
console.log(JSON.stringify([urls(), puts(), v]));
"""
        % (f"ago({age});" if age else ""),
    )
    assert out == [["/api/spotify/now?fresh=1", "/api/spotify/volume"], [75], 75]


@needs_node
def test_unreadable_volume_skips_the_tap():
    out = _chain(
        None,
        """
replies["/api/spotify/now?fresh=1"] = "down";
const v = await tap(5);
console.log(JSON.stringify([v, skips, puts()]));
""",
    )
    assert out == [None, 1, []]


@needs_node
def test_renders_hold_the_target_until_the_last_put_settles():
    out = _chain(
        50,
        """
ago(0);
close();
const done = tap(5); tap(5); tap(5);
await flush();
const held = tap.hold({ volume: 50, track: "t" });
open();
await done;
const after = tap.hold({ volume: 50, track: "t" });
console.log(JSON.stringify([held, after]));
""",
    )
    held, after = out
    assert held == {"volume": 65, "track": "t"}
    assert after == {"volume": 50, "track": "t"}


@needs_node
def test_failed_put_notes_and_shows_the_server_volume_next():
    out = _chain(
        50,
        """
ago(0);
replies["/api/spotify/volume"] = "down";
const v = await tap(5);
replies["/api/spotify/volume"] = undefined;
replies["/api/spotify/now?fresh=1"] = { status: 200, body: { volume: 50 } };
await tap(5);
const next = tap.hold({ volume: 50 });
console.log(JSON.stringify([v, notes, next, urls()]));
""",
    )
    v, notes, nxt, urls = out
    assert v is None
    assert notes == ["Could not reach Spotify"]
    assert nxt == {"volume": 50}
    assert urls == ["/api/spotify/volume", "/api/spotify/now?fresh=1", "/api/spotify/volume"]


@needs_node
def test_slider_value_joins_a_pending_burst():
    out = _chain(
        50,
        """
ago(0);
close();
const done = tap(5);
await flush();
tap.to(20);
tap(5);
open();
await done;
console.log(JSON.stringify([puts(), shown.volume]));
""",
    )
    assert out == [[55, 25], 25]


@needs_node
def test_fresh_volume_notes_when_spotify_is_down():
    out = _node("""
replies["/api/spotify/now?fresh=1"] = "down";
const notes = [];
const v = await P.freshVolume(function (t) { notes.push(t); });
const quiet = await P.freshVolume();
console.log(JSON.stringify([v, quiet, notes]));
""")
    assert out == [None, None, ["Could not reach Spotify"]]


@needs_node
def test_events_reconnect_with_backoff_and_reset_on_open():
    out = _node("""
const made = [];
const delays = [];
globalThis.setTimeout = function (fn, ms) { delays.push(ms); made.pending = fn; return 0; };
globalThis.EventSource = function (url) {
  this.url = url;
  this.listeners = {};
  this.closed = false;
  made.push(this);
};
EventSource.prototype.addEventListener = function (name, fn) {
  (this.listeners[name] = this.listeners[name] || []).push(fn);
};
EventSource.prototype.close = function () { this.closed = true; };
const seen = [];
P.events({ hello: [function () { seen.push("a"); }, function () { seen.push("b"); }], x: function () { seen.push("x"); } });
made[0].listeners.hello.forEach(function (fn) { fn(); });
made[0].listeners.x[0]();
for (let i = 0; i < 7; i++) {
  made[made.length - 1].onerror();
  made.pending();
}
made[made.length - 1].onopen();
made[made.length - 1].onerror();
console.log(JSON.stringify([made[0].url, seen, delays, made[0].closed, made.length]));
""")
    url, seen, delays, closed, count = out
    assert url == "/api/events"
    assert seen == ["a", "b", "x"]
    assert delays == [1000, 2000, 4000, 8000, 16000, 30000, 30000, 1000]
    assert closed is True and count == 8


@needs_node
def test_events_do_nothing_without_event_source():
    out = _node("""
delete globalThis.EventSource;
P.events({ hello: function () {} });
console.log(JSON.stringify(true));
""")
    assert out is True


@needs_node
def test_playback_shaping():
    out = _node("""
const playing = { playing: true, progress_ms: 10000, duration_ms: 13000 };
console.log(JSON.stringify([
  P.nextPollMs(playing, 15000, 1000),
  P.nextPollMs({ playing: false }, 15000, 1000),
  P.nextPollMs({ playing: true, progress_ms: 0, duration_ms: 600000 }, 5000, 1000),
  [0, 1, 2, 3, 9].map(function (n) { return P.backoffMs(null, n, 15000, 120000, 1000); }),
  P.progressAt(playing, 1000),
  P.progressAt(Object.assign({ stale: true }, playing), 1000),
  P.progressAt(playing, 99999),
  P.formatMs(65000),
  P.staleText({ stale: true, reason: "rate limited", track: "t" }),
  P.staleText({ stale: false }),
  P.deviceText({ device: "Phone", on_board: false }),
  P.deviceText({ device: "Pi", on_board: true }),
  [P.volumeStep(98, 5), P.volumeStep(2, -5), P.volumeStep(null, 5)],
  [1, 2, 16, 30].map(P.nextBackoff),
]));
""")
    assert out == [
        4000,
        15000,
        5000,
        [15000, 15000, 30000, 60000, 120000],
        11000,
        10000,
        13000,
        "1:05",
        "Could not reach Spotify (too many requests). Showing the last known song.",
        None,
        "Playing on Phone",
        "Playing on Home Board",
        [100, 0, None],
        [2, 4, 30, 30],
    ]


@needs_node
def test_hold_step_repeats_while_held_and_steps_on_keyboard_click():
    out = _node("""
const timers = [];
globalThis.setTimeout = function (fn, ms) { timers.push(["t", ms, fn]); return timers.length; };
globalThis.setInterval = function (fn, ms) { timers.push(["i", ms, fn]); return timers.length; };
globalThis.clearTimeout = globalThis.clearInterval = function () {};
const on = {};
const btn = { addEventListener: function (name, fn) { on[name] = fn; } };
let steps = 0;
P.holdStep(btn, function () { steps++; });
on.pointerdown({ button: 2 });
on.pointerdown({ button: 0 });
timers[0][2]();
on.click({ detail: 0 });
on.click({ detail: 1 });
console.log(JSON.stringify([steps, timers.map(function (t) { return [t[0], t[1]]; }), Object.keys(on).sort()]));
""")
    steps, timers, names = out
    assert steps == 2
    assert timers == [["t", 400], ["i", 80]]
    assert names == ["click", "pointercancel", "pointerdown", "pointerleave", "pointerup"]
