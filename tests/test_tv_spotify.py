import base64
import io
import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image

from app import db, routes_sound, spotify
from app.main import create_app
from tests.chrome import _Chrome, needs_chrome, needs_node, server  # noqa: F401
from tests.test_screen_window import _open_window, _wait, _widgets

SCREEN_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "tv_screen.js"
SIZES = [(2, 2), (2, 1), (1, 2), (1, 1)]


def _node(body):
    script = f"const T = require({json.dumps(str(SCREEN_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


def _png(color, size=64):
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _playing(**over):
    now = {
        "linked": True,
        "playing": True,
        "on_board": True,
        "track": "Here Comes the Sun",
        "artist": "The Beatles",
        "album": "Abbey Road",
        "cover": _png((200, 120, 40), 300),
        "progress_ms": 65000,
        "duration_ms": 185000,
        "volume": 40,
        "device": "Home Board",
        "stale": False,
    }
    now.update(over)
    return now


@needs_node
def test_spotify_helpers():
    empty = spotify._empty()
    result = _node(
        f"const empty = {json.dumps(empty)};"
        f"const playing = {json.dumps(_playing(cover='https://i.scdn.co/big'))};"
        "const paused = Object.assign({}, playing, {playing: false});"
        "console.log(JSON.stringify({"
        " ms: [0, 65000, 3600000, 59999, null].map(T.formatMs),"
        " layouts: [[2, 2], [2, 1], [1, 2], [1, 1]].map(s => T.spotifyLayout({w: s[0], h: s[1]})),"
        " states: [empty, playing, paused, {linked: false, reason: 'x'}, null].map(T.spotifyState),"
        " covers: [empty, playing, paused, {linked: false}].map(T.coverUrl),"
        " progress: [T.progressAt(playing, 2000), T.progressAt(paused, 2000),"
        "   T.progressAt(playing, 999999), T.progressAt(empty, 1000)],"
        " volume: [T.volumeStep(40, 5), T.volumeStep(98, 5), T.volumeStep(3, -5), T.volumeStep(null, 5)],"
        " speaker: [T.speakerOn({speaker: {connected: true}}), T.speakerOn({speaker: {connected: false}}),"
        "   T.speakerOn(null)]}))"
    )
    assert empty["playing"] is False
    assert result["ms"] == ["0:00", "1:05", "60:00", "0:59", "0:00"]
    assert result["layouts"] == ["full", "wide", "tall", "tile"]
    assert result["states"] == ["idle", "playing", "paused", "unlinked", "idle"]
    assert result["covers"] == [None, "https://i.scdn.co/big", "https://i.scdn.co/big", None]
    assert result["progress"] == [67000, 65000, 185000, 0]
    assert result["volume"] == [45, 100, 0, 55]
    assert result["speaker"] == [True, False, False]


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_spotify_widget_kind_takes_all_four_sizes(client):
    assert client.get("/api/widget-sizes").json()["spotify"] == [[2, 2], [2, 1], [1, 2], [1, 1]]
    for w, h in SIZES:
        sid = client.post("/api/screens", json={"name": f"Music {w}x{h}"}).json()["id"]
        r = client.post(
            f"/api/screens/{sid}/widgets",
            json={"kind": "spotify", "col": 0, "row": 0, "w": w, "h": h},
        )
        assert r.status_code == 201, r.text
        assert [(x["kind"], x["w"], x["h"]) for x in r.json()["widgets"]] == [("spotify", w, h)]
    sid = client.post("/api/screens", json={"name": "Default"}).json()["id"]
    r = client.post(f"/api/screens/{sid}/widgets", json={"kind": "spotify", "col": 0, "row": 0})
    assert r.status_code == 201
    assert [(x["w"], x["h"]) for x in r.json()["widgets"]] == [(2, 2)]
    page = client.get("/tv")
    assert page.status_code == 200 and "tv_screen.js" in page.text


def test_migration_keeps_widgets_and_allows_spotify(tmp_path):
    path = tmp_path / "homeboard.db"
    conn = db.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        for v in range(1, 17):
            sql = next(db.MIGRATIONS_DIR.glob(f"{v:04d}_*.sql"))
            conn.executescript(sql.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (v,))
        conn.commit()
        before = [tuple(r) for r in conn.execute("SELECT * FROM widgets ORDER BY id")]
        assert before
        assert db.migrate(conn) == 17
        assert [tuple(r) for r in conn.execute("SELECT * FROM widgets ORDER BY id")] == before
        sid = conn.execute(
            "INSERT INTO tv_screens (name, sort_order, created_at) VALUES ('M', 99, 'x')"
        ).lastrowid
        conn.commit()
        assert db.add_widget(conn, sid, "spotify", 0, 0, 1, 1)["kind"] == "spotify"
        names = {r[1] for r in conn.execute("PRAGMA index_list(widgets)")}
        assert "widgets_screen_id" in names
    finally:
        conn.close()


class FakeApi:
    def __init__(self):
        self.now = _playing()
        self.calls = []
        self.fail = {}
        self.connected = False

    def now_playing(self, data_dir):
        self.calls.append(("GET", "/api/spotify/now"))
        return self.now

    def control(self, data_dir, method, path, payload=None, query=None):
        name = {
            "/me/player/play": "play",
            "/me/player/pause": "pause",
            "/me/player/next": "next",
            "/me/player/previous": "previous",
            "/me/player/volume": "volume",
        }[path]
        self.calls.append(("POST" if name != "volume" else "PUT", f"/api/spotify/{name}", query))
        if name in self.fail:
            raise spotify.SpotifyError(409, self.fail[name])
        return {"linked": True, "ok": True}

    def play_playlist(self, data_dir, playlist_id):
        self.calls.append(("POST", "/api/spotify/play-playlist", playlist_id))
        return {"linked": True, "ok": True}

    def helper(self, method, path, timeout=None):
        self.calls.append((method, "/api/sound" + path))
        if path == "/connect":
            self.connected = True
        elif path == "/disconnect":
            self.connected = False
        if path == "/status":
            return JSONResponse(
                {"speaker": {"mac": "AA", "connected": self.connected, "name": "Bar"}}
            )
        return JSONResponse({"connected": self.connected})

    def sent(self, method, url):
        return [c for c in self.calls if c[0] == method and c[1] == url]


@pytest.fixture
def fake(monkeypatch):
    api = FakeApi()
    monkeypatch.setattr(spotify, "now_playing", api.now_playing)
    monkeypatch.setattr(spotify, "control", api.control)
    monkeypatch.setattr(spotify, "play_playlist", api.play_playlist)
    monkeypatch.setattr(routes_sound, "helper", api.helper)
    return api


SHOWN = [
    {"id": "pl-road", "name": "Road trip", "cover": _png((40, 140, 70))},
    {"id": "pl-dinner", "name": "Dinner jazz", "cover": _png((40, 70, 160))},
    {"id": "pl-kids", "name": "Kids songs", "cover": None},
    {"id": "pl-focus", "name": "Focus", "cover": _png((150, 40, 120))},
    {"id": "pl-80s", "name": "Eighties", "cover": _png((220, 200, 40))},
    {"id": "pl-rain", "name": "Rainy day", "cover": _png((90, 90, 90))},
]

CARD = """JSON.stringify((() => {
  const c = document.querySelector('#screen .tv-card[data-kind="spotify"]');
  if (!c) return null;
  const q = s => c.querySelector(s) ? c.querySelector(s).textContent : null;
  const box = c.getBoundingClientRect();
  const out = [...c.querySelectorAll('.sp-cover, .sp-info, .sp-btn, .sp-track')].filter(n => {
    const r = n.getBoundingClientRect();
    return r.left < box.left - 1 || r.right > box.right + 1 || r.top < box.top - 1 || r.bottom > box.bottom + 1;
  }).map(n => n.className);
  const small = [...c.querySelectorAll('.sp-btn, .sp-pl')].filter(b => {
    const r = b.getBoundingClientRect();
    return r.height < 48 || r.width < 48;
  }).map(b => b.className);
  return {title: document.getElementById('title').textContent, size: c.getAttribute('data-size'),
    h2: q('h2'), layout: c.querySelector('.sp') ? c.querySelector('.sp').className : null,
    track: q('.sp-track'), artist: q('.sp-artist'), elapsed: q('.sp-elapsed'), total: q('.sp-total'),
    vol: q('.sp-vol'), speaker: q('.sp-speaker'), msg: q('.sp-msg'), text: q('.tv-msg'),
    toggle: c.querySelector('.sp-toggle') ? c.querySelector('.sp-toggle').getAttribute('aria-label') : null,
    toggleOnCover: !!c.querySelector('.sp-cover .sp-toggle'),
    transport: [...c.querySelectorAll('.sp-transport .sp-btn')].map(b => b.getAttribute('data-sp')),
    rows: [...c.querySelectorAll('[data-playlist]')].map(b => [b.getAttribute('data-playlist'), b.textContent]),
    coverImg: !!c.querySelector('.sp-cover img'), coverNone: !!c.querySelector('.sp-cover.none'),
    scroll: c.querySelector('.sp-pls') ? getComputedStyle(c.querySelector('.sp-pls')).overflowY : null,
    out, small};
})())"""


def _setup(base, sizes=SIZES):
    httpx.put(f"{base}/api/spotify/shown", json=SHOWN).raise_for_status()
    ids = {}
    for w, h in sizes:
        sid = httpx.post(f"{base}/api/screens", json={"name": f"Music {w}x{h}"}).json()["id"]
        r = httpx.post(
            f"{base}/api/screens/{sid}/widgets",
            json={"kind": "spotify", "col": 0, "row": 0, "w": w, "h": h},
        )
        assert r.status_code == 201, r.text
        ids[(w, h)] = sid
    return ids


def _go(base, sid):
    r = httpx.put(f"{base}/api/settings", json={"channel": "screen", "screen_id": sid})
    r.raise_for_status()


def _card(chrome, title, ready, tries=120):
    out = None
    for _ in range(tries):
        out = json.loads(chrome.eval(CARD) or "null")
        if out and out["title"] == title and ready(out):
            return out
        time.sleep(0.05)
    raise AssertionError(f"card never ready: {out}")


def _until(check, what, tries=120):
    for _ in range(tries):
        if check():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _tap(chrome, selector):
    assert chrome.eval(f"!!document.querySelector({json.dumps(selector)})"), selector
    chrome.eval(f"document.querySelector({json.dumps(selector)}).click()")


@needs_chrome
def test_spotify_card_layout_per_size(server, tmp_path, fake):  # noqa: F811
    ids = _setup(server)
    chrome = _Chrome(tmp_path / "chrome", 1024, 600)
    try:
        _go(server, ids[(2, 2)])
        chrome.send("Page.navigate", {"url": f"{server}/tv"})
        full = _card(chrome, "Music 2x2", lambda o: o["rows"] and o["speaker"] and o["coverImg"])
        assert (
            full["h2"] == "Spotify" and "sp-full" in full["layout"] and "playing" in full["layout"]
        )
        assert full["track"] == "Here Comes the Sun" and full["artist"] == "The Beatles"
        assert full["elapsed"] in ("1:05", "1:06", "1:07") and full["total"] == "3:05"
        assert full["transport"] == ["previous", "toggle", "next"] and full["toggle"] == "Pause"
        assert full["vol"] == "40%" and full["speaker"] == "Speaker not connected"
        assert [r[0] for r in full["rows"]] == [p["id"] for p in SHOWN]
        assert full["rows"][0][1] == "Road trip" and full["scroll"] == "auto"
        assert full["out"] == [] and full["small"] == []

        for (w, h), title in [((2, 1), "Music 2x1"), ((1, 2), "Music 1x2")]:
            _go(server, ids[(w, h)])
            out = _card(chrome, title, lambda o: o["track"] == "Here Comes the Sun")
            assert out["size"] == f"{w}x{h}" and out["artist"] == "The Beatles"
            assert out["transport"] == ["previous", "toggle", "next"] and out["coverImg"]
            assert out["rows"] == [] and out["vol"] is None and out["speaker"] is None
            assert out["elapsed"] is None
            assert out["out"] == [] and out["small"] == [], title

        _go(server, ids[(1, 1)])
        tile = _card(chrome, "Music 1x1", lambda o: o["track"] == "Here Comes the Sun")
        assert tile["toggleOnCover"] and tile["toggle"] == "Pause" and tile["coverImg"]
        assert tile["transport"] == [] and tile["rows"] == [] and tile["artist"] is None
        assert tile["out"] == [] and tile["small"] == []
    finally:
        chrome.close()


@needs_chrome
def test_spotify_nothing_playing_and_not_linked(server, tmp_path, fake):  # noqa: F811
    ids = _setup(server)
    fake.now = spotify._empty()
    chrome = _Chrome(tmp_path / "chrome", 1024, 600)
    try:
        _go(server, ids[(2, 2)])
        chrome.send("Page.navigate", {"url": f"{server}/tv"})
        full = _card(chrome, "Music 2x2", lambda o: o["rows"] and o["speaker"])
        assert full["track"] == "Nothing playing" and full["coverNone"] and not full["coverImg"]
        assert "idle" in full["layout"] and full["transport"] == [] and full["vol"] is None
        assert len(full["rows"]) == len(SHOWN)
        _tap(chrome, '[data-playlist="pl-dinner"]')
        _until(lambda: fake.sent("POST", "/api/spotify/play-playlist"), "the playlist call")
        assert fake.sent("POST", "/api/spotify/play-playlist") == [
            ("POST", "/api/spotify/play-playlist", "pl-dinner")
        ]

        fake.now = {"linked": False, "reason": "not linked"}
        for size, sid in ids.items():
            _go(server, sid)
            title = f"Music {size[0]}x{size[1]}"
            out = _card(chrome, title, lambda o: o["text"] == "Spotify not linked")
            assert out["track"] is None and out["rows"] == [] and out["transport"] == []
    finally:
        chrome.close()


@needs_chrome
def test_spotify_taps_send_controls(server, tmp_path, fake):  # noqa: F811
    ids = _setup(server, [(2, 2)])
    shots = Path(os.environ.get("TV_SHOTS_DIR") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    chrome = _Chrome(tmp_path / "chrome", 1024, 600)
    try:
        _go(server, ids[(2, 2)])
        chrome.send("Page.navigate", {"url": f"{server}/tv"})
        _card(chrome, "Music 2x2", lambda o: o["rows"] and o["speaker"] and o["toggle"] == "Pause")
        _until(
            lambda: chrome.eval(
                "[...document.querySelectorAll('.tv-card img')].every(i => i.complete && i.naturalWidth)"
            ),
            "covers to load",
        )
        png = chrome.send("Page.captureScreenshot", {"format": "png"})["data"]
        (shots / "tv_spotify_playing.png").write_bytes(base64.b64decode(png))

        _tap(chrome, '[data-sp="toggle"]')
        _until(lambda: fake.sent("POST", "/api/spotify/pause"), "the pause call")
        assert not fake.sent("POST", "/api/spotify/play")

        fake.now = _playing(playing=False)
        _card(chrome, "Music 2x2", lambda o: o["toggle"] == "Play")
        _tap(chrome, '[data-sp="toggle"]')
        _until(lambda: fake.sent("POST", "/api/spotify/play"), "the play call")
        fake.now = _playing()

        _tap(chrome, '[data-playlist="pl-kids"]')
        _until(lambda: fake.sent("POST", "/api/spotify/play-playlist"), "the playlist call")
        assert fake.sent("POST", "/api/spotify/play-playlist")[0][2] == "pl-kids"

        _tap(chrome, '[data-sp="vol-up"]')
        _until(lambda: fake.sent("PUT", "/api/spotify/volume"), "the volume call")
        assert fake.sent("PUT", "/api/spotify/volume")[0][2] == {"volume_percent": 45}

        assert not fake.connected
        _tap(chrome, '[data-sp="speaker"]')
        _until(lambda: fake.sent("POST", "/api/sound/connect"), "the connect call")
        _card(chrome, "Music 2x2", lambda o: o["speaker"] == "Speaker connected")
        assert not fake.sent("POST", "/api/sound/disconnect")
        _tap(chrome, '[data-sp="speaker"]')
        _until(lambda: fake.sent("POST", "/api/sound/disconnect"), "the disconnect call")

        fake.fail["next"] = "no active device"
        _tap(chrome, '[data-sp="next"]')
        _card(chrome, "Music 2x2", lambda o: o["msg"] == "no active device")
        start = time.monotonic()
        _card(chrome, "Music 2x2", lambda o: o["msg"] is None, tries=200)
        assert 1.5 < time.monotonic() - start < 4.5
    finally:
        chrome.close()


@needs_chrome
def test_spotify_polls_only_while_shown_and_visible(server, tmp_path, fake):  # noqa: F811
    ids = _setup(server, [(2, 1)])
    brief = next(s for s in httpx.get(f"{server}/api/screens").json() if s["name"] == "Daily Brief")
    chrome = _Chrome(tmp_path / "chrome", 1024, 600)
    polls = lambda: len(fake.sent("GET", "/api/spotify/now"))
    try:
        _go(server, ids[(2, 1)])
        chrome.send("Page.navigate", {"url": f"{server}/tv"})
        _card(chrome, "Music 2x1", lambda o: o["track"] == "Here Comes the Sun")
        first = polls()
        time.sleep(3.5)
        assert polls() > first

        _go(server, brief["id"])
        _until(
            lambda: chrome.eval("document.getElementById('title').textContent") == "Daily Brief",
            "switch",
        )
        time.sleep(0.5)
        stopped = polls()
        time.sleep(3.5)
        assert polls() == stopped

        _go(server, ids[(2, 1)])
        _card(chrome, "Music 2x1", lambda o: True)
        _until(lambda: polls() > stopped, "polling to restart")
        chrome.eval(
            "Object.defineProperty(document, 'visibilityState', {get: () => 'hidden', configurable: true});"
            "document.dispatchEvent(new Event('visibilitychange'))"
        )
        time.sleep(0.5)
        hidden = polls()
        time.sleep(3.5)
        assert polls() == hidden
        chrome.eval(
            "Object.defineProperty(document, 'visibilityState', {get: () => 'visible', configurable: true});"
            "document.dispatchEvent(new Event('visibilitychange'))"
        )
        _until(lambda: polls() > hidden, "polling to resume")
    finally:
        chrome.close()


@needs_chrome
def test_phone_screen_window_adds_spotify_with_all_sizes(server, tmp_path):  # noqa: F811
    sid = httpx.post(f"{server}/api/screens", json={"name": "Tunes"}).json()["id"]
    chrome = _Chrome(tmp_path / "chrome", 412, 900)
    q = "document.querySelector"
    sizes = "[...document.getElementById('widget-size').options].map((o) => o.value)"
    try:
        _open_window(chrome, server, sid)
        _wait(lambda: chrome.eval("document.querySelectorAll('#screen-layout .empty').length") == 4)
        chrome.eval(f"{q}('[data-cell=\"0,0\"]').click()")
        assert chrome.eval(f"!{q}('#widget-add [data-kind=spotify]').hidden") is True
        assert chrome.eval(f"{q}('#widget-add [data-kind=spotify]').textContent") == "Spotify"
        chrome.eval(f"{q}('#widget-add [data-kind=spotify]').click()")
        _wait(lambda: _widgets(server, "Tunes") == [("spotify", 0, 0, 2, 2)])
        _wait(lambda: chrome.eval(f"{q}('#screen-layout .cell.on').textContent") == "Spotify")
        assert chrome.eval(sizes) == ["2x2", "2x1", "1x2", "1x1"]
    finally:
        chrome.close()
