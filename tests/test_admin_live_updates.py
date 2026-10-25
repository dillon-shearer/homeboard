import threading

from fastapi.testclient import TestClient

from app import events
from app.main import create_app
from tests.test_upload_batches import _node, needs_node

LIVE_SETUP = """
var calls = [];
var status = { display_connected: true, photo_count: 2, current_photo_id: null, disk_free_bytes: 5e10 };
var list = [
  { id: 1, thumb_url: "/thumbs/1.jpg", caption: null, enabled: true },
  { id: 2, thumb_url: "/thumbs/2.jpg", caption: "Beach", enabled: true },
];
var settings = { interval_s: 20, transition: true, fit: "contain", shuffle: false, captions: false };
globalThis.fetch = function (url) {
  calls.push(url);
  var body = {};
  if (url.indexOf("/api/photos") === 0) body = list;
  if (url === "/api/status") body = status;
  if (url === "/api/settings") body = settings;
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
};
var sources = [];
globalThis.EventSource = function (url) {
  this.url = url;
  this.listeners = {};
  this.closed = false;
  sources.push(this);
};
EventSource.prototype.addEventListener = function (t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); };
EventSource.prototype.close = function () { this.closed = true; };
EventSource.prototype.emit = function (t) { (this.listeners[t] || []).forEach(function (fn) { fn({ data: "{}" }); }); };
function count(url) { return calls.filter(function (u) { return u === url; }).length; }
"""


@needs_node
def test_events_reload_grid_and_not_settings():
    r = _node(
        """
        await flush();
        const es = sources[0];
        const before = { photos: count("/api/photos?all=1"), settings: count("/api/settings") };
        es.emit("hello");
        await flush();
        const afterHello = { photos: count("/api/photos?all=1"), settings: count("/api/settings") };
        list = list.concat([{ id: 3, thumb_url: "/thumbs/3.jpg", caption: null, enabled: false }]);
        es.emit("photos_changed");
        await flush();
        const tiles = $("grid").children.length;
        es.emit("settings_changed");
        await flush();
        console.log(JSON.stringify({
          url: es.url, n: sources.length, before: before, afterHello: afterHello,
          photos: count("/api/photos?all=1"), settings: count("/api/settings"),
          tiles: tiles,
        }));
        """,
        LIVE_SETUP,
    )
    assert r["url"] == "/api/events" and r["n"] == 1
    assert r["afterHello"] == r["before"] == {"photos": 1, "settings": 0}
    assert r["photos"] == 2 and r["tiles"] == 3
    assert r["settings"] == 0


@needs_node
def test_events_reconnect_with_backoff_and_reload_after_reconnect():
    r = _node(
        """
        await flush();
        var delays = [];
        var realTimeout = globalThis.setTimeout;
        globalThis.setTimeout = function (fn, ms) {
          if (ms >= 1000) { delays.push(ms); return realTimeout(fn, 0); }
          return realTimeout(fn, ms);
        };
        sources[0].emit("hello");
        sources[0].onerror();
        await flush();
        sources[1].onerror();
        await flush();
        sources[2].onopen();
        const photos = count("/api/photos?all=1");
        sources[2].emit("hello");
        await flush();
        console.log(JSON.stringify({
          closed: sources[0].closed && sources[1].closed, n: sources.length, delays: delays,
          reloaded: count("/api/photos?all=1") - photos,
        }));
        """,
        LIVE_SETUP,
    )
    assert r["closed"] and r["n"] == 3
    assert r["delays"] == [1000, 2000]
    assert r["reloaded"] == 1


def _thumb(current_id):
    return _node(
        f"""
        status.current_photo_id = {current_id};
        await flush();
        await flush();
        const img = $("now-thumb");
        console.log(JSON.stringify({{ hidden: img.hidden, src: img.src || null, alt: img.alt || null }}));
        """,
        LIVE_SETUP,
    )


@needs_node
def test_status_line_shows_photo_on_tv_now():
    assert _thumb(2) == {"hidden": False, "src": "/thumbs/2.jpg", "alt": "On the TV now: Beach"}
    assert _thumb("null")["hidden"] is True
    assert _thumb(99)["hidden"] is True


@needs_node
def test_status_line_warns_when_card_is_almost_full():
    r = _node(
        """
        status.disk_free_bytes = 8e8;
        await flush();
        const low = { hidden: $("disk-warning").hidden, text: $("disk-warning").textContent };
        console.log(JSON.stringify({ low: low, ok: A.diskWarning(2e9), edge: A.diskWarning(1e9) }));
        """,
        LIVE_SETUP,
    )
    assert r["low"] == {"hidden": False, "text": "Card almost full: 0.8 GB left"}
    assert r["ok"] == "" and r["edge"] == ""


def test_status_reports_current_photo_id(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/status").json()["current_photo_id"] is None
        client.put("/api/display/current", json={"id": 7})
        assert client.get("/api/status").json()["current_photo_id"] == 7


def _connected_while_streaming(tmp_path, host):
    seen = {}
    with TestClient(create_app(tmp_path), client=(host, 50000)) as client:
        client.app.state.sse_limit = 1
        with client.stream("GET", "/api/events", headers={"Accept": "text/event-stream"}) as r:
            chunks = r.iter_text()
            assert next(chunks).startswith("event: hello")
            seen["subs"] = len(events.subscribers)
            seen["connected"] = events.display_connected()
            threading.Timer(0, events.publish, ["photos_changed"]).start()
            for _ in chunks:
                pass
    return seen


def test_lan_admin_page_does_not_count_as_display(tmp_path):
    assert _connected_while_streaming(tmp_path, "192.168.1.20") == {"subs": 1, "connected": False}


def test_loopback_events_count_as_display(tmp_path):
    assert _connected_while_streaming(tmp_path, "127.0.0.1") == {"subs": 1, "connected": True}
