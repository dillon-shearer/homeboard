import base64
import json
import os
import re
import subprocess
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app import routes_sound, spotify, weather
from app.main import create_app
from tests.chrome import api, api_at, needs_chrome, needs_node
from tests.test_tv_spotify import SIZES as SP_SIZES
from tests.test_tv_spotify import _playing, _png
from tests.test_tv_weather import _seed_cache

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
SCREEN_JS = STATIC / "tv_screen.js"
DISPLAY_JS = STATIC / "display.js"


def _node(body):
    script = f"const T = require({json.dumps(str(SCREEN_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_tv_route_loads_assets_through_static_url(client):
    r = client.get("/tv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    for name in ("win95.css", "tv.css", "calendar_util.js", "tv_screen.js"):
        version = int((STATIC / name).stat().st_mtime)
        url = f"/static/{name}?v={version}"
        assert url in r.text, name
        assert client.get(url).status_code == 200
    assert r.text.index("calendar_util.js") < r.text.index("tv_screen.js")


def test_tv_script_listens_for_changes():
    js = SCREEN_JS.read_text(encoding="utf-8")
    for name in ("hello", "settings_changed", "lists_changed", "events_changed", "weather_changed"):
        assert f'"{name}"' in js, name
    assert "location.reload" not in js


@needs_node
def test_placement_maps_col_row_w_h_to_grid_lines():
    result = _node(
        "const ws = [{col: 0, row: 0, w: 2, h: 1}, {col: 0, row: 1, w: 1, h: 1},"
        " {col: 1, row: 1, w: 1, h: 1}, {col: 1, row: 0, w: 1, h: 2}];"
        "console.log(JSON.stringify({same: globalThis.TvScreen === T,"
        " out: ws.map(T.placement)}))"
    )
    assert result["same"] is True
    assert result["out"] == [
        {"gridColumn": "1 / span 2", "gridRow": "1 / span 1"},
        {"gridColumn": "1 / span 1", "gridRow": "2 / span 1"},
        {"gridColumn": "2 / span 1", "gridRow": "2 / span 1"},
        {"gridColumn": "2 / span 1", "gridRow": "1 / span 2"},
    ]


@needs_node
def test_current_screen_and_needs():
    result = _node(
        "const screens = [{id: 7, name: 'B', sort_order: 20, widgets: [{kind: 'clock'}]},"
        " {id: 3, name: 'A', sort_order: 10, widgets: [{kind: 'calendar'}, {kind: 'clock'},"
        " {kind: 'calendar'}, {kind: 'lists'}, {kind: 'odd'}]}];"
        "console.log(JSON.stringify({first: T.currentScreen(screens, null).name,"
        " seven: T.currentScreen(screens, 7).name, gone: T.currentScreen(screens, 99).name,"
        " none: T.currentScreen([], null),"
        " needsA: T.needs(screens[1]), needsB: T.needs(screens[0]), needsNull: T.needs(null),"
        " kinds: Object.keys(T.WIDGETS).sort(),"
        " label: T.screenLabel(screens, screens[0])}))"
    )
    assert result["first"] == "A"
    assert result["seven"] == "B"
    assert result["gone"] == "A"
    assert result["none"] is None
    assert result["needsA"] == ["events", "lists"]
    assert result["needsB"] == []
    assert result["needsNull"] == []
    assert result["kinds"] == ["calendar", "clock", "lists", "spotify", "weather"]
    assert result["label"] == "Screen 2 of 2"


@needs_node
def test_calendar_helpers_split_today_and_coming_up():
    result = _node(
        "const ev = ["
        "{title: 'Late', date: '2026-10-23', time: '18:30'},"
        "{title: 'Bins', date: '2026-10-23', time: null},"
        "{title: 'Early', date: '2026-10-23', time: '08:00'},"
        "{title: 'Dentist', date: '2026-10-24', time: '09:00'},"
        "{title: 'Trip', date: '2026-10-30', time: null},"
        "{title: 'Far', date: '2026-10-31', time: null},"
        "{title: 'Past', date: '2026-10-22', time: null}];"
        "const d = new Date(2026, 9, 23, 7, 5);"
        "console.log(JSON.stringify({today: T.todayEvents(ev, '2026-10-23'),"
        " up: T.comingUp(ev, '2026-10-23'), range: T.eventRange('2026-10-23'),"
        " endRange: T.eventRange('2026-10-31'),"
        " clock: T.formatClock(d), date: T.formatDate(d), full: T.fullDate(d),"
        " full2: [T.isFull({w: 2, h: 2}), T.isFull({w: 2, h: 1})]}))"
    )
    assert result["today"] == [
        {"title": "Bins", "time": "all day"},
        {"title": "Early", "time": "8:00 am"},
        {"title": "Late", "time": "6:30 pm"},
    ]
    assert result["up"] == [
        {"title": "Dentist", "when": "Tomorrow, 9:00 am"},
        {"title": "Trip", "when": "Fri Oct 30"},
    ]
    assert result["range"] == {"start": "2026-09-27", "end": "2026-10-31"}
    assert result["endRange"] == {"start": "2026-09-27", "end": "2026-11-07"}
    assert result["clock"] == "7:05 am"
    assert result["date"] == "Friday, October 23"
    assert result["full"] == "October 23, 2026"
    assert result["full2"] == [True, False]


@needs_node
def test_slideshow_stops_with_two_screens_wrap_through_photos():
    script = (
        f"const S = require({json.dumps(str(DISPLAY_JS))});"
        "const stops = S.buildStops([{id: 1, name: 'Daily Brief'}, {id: 2, name: 'Calendar'}]);"
        "console.log(JSON.stringify({stops,"
        " fromLast: S.nextStop(stops, {channel: 'screen', screen_id: 2}, 1),"
        " fromPhotos: S.nextStop(stops, {channel: 'photos'}, -1)}))"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    result = json.loads(out.stdout)
    assert result["stops"] == [
        {"channel": "photos"},
        {"channel": "screen", "screen_id": 1},
        {"channel": "screen", "screen_id": 2},
    ]
    assert result["fromLast"] == {"channel": "photos"}
    assert result["fromPhotos"] == {"channel": "screen", "screen_id": 2}


def _screens(base):
    return {s["name"]: s for s in api.get(f"{base}/api/screens").json()}


def _lists(base):
    return {lst["name"]: lst for lst in api.get(f"{base}/api/lists").json()}


def _settings(base):
    return api.get(f"{base}/api/settings").json()


def _show_screen(base, screen):
    api.put(f"{base}/api/settings", json={"channel": "screen", "screen_id": screen["id"]})


def _new_screen(base, name, widgets):
    screen = api.post(f"{base}/api/screens", json={"name": name}).json()
    for body in widgets:
        r = api.post(f"{base}/api/screens/{screen['id']}/widgets", json=body)
        assert r.status_code == 201, r.text
    return screen


def _event(base, title, day, time_=None, repeat="never"):
    r = api.post(
        f"{base}/api/events",
        json={"title": title, "date": day.isoformat(), "time": time_, "repeat": repeat},
    )
    assert r.status_code == 201, r.text


def _until(fn, ready, tries=100):
    v = None
    for _ in range(tries):
        v = fn()
        if ready(v):
            return v
        time.sleep(0.05)
    return v


def _json(chrome, expr):
    return json.loads(chrome.eval(expr) or "null")


def _steady(chrome, expr, ready):
    last = None
    for _ in range(100):
        v = _json(chrome, expr)
        if v and ready(v) and v == last:
            return v
        last = v
        time.sleep(0.05)
    return last


_clock = [0.0]


def _touch(chrome, kind, points):
    step = {"touchStart": 1, "touchEnd": 0.5}.get(kind, 0.02)
    _clock[0] = max(_clock[0] + step, time.time())
    chrome.send(
        "Input.dispatchTouchEvent", {"type": kind, "touchPoints": points, "timestamp": _clock[0]}
    )


def _tap(chrome, x, y):
    _touch(chrome, "touchStart", [{"x": x, "y": y}])
    _touch(chrome, "touchEnd", [])


def _swipe(chrome, points):
    _touch(chrome, "touchStart", [points[0]])
    for p in points[1:]:
        _touch(chrome, "touchMove", [p])
    _touch(chrome, "touchEnd", [])


def _move(chrome, kind, x, y=300):
    _touch(chrome, kind, [] if kind == "touchEnd" else [{"x": x, "y": y}])


def _click(chrome, x, y):
    for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
        chrome.send(
            "Input.dispatchMouseEvent",
            {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1},
        )


def _seed_events(base):
    today = date.today()
    _event(base, "Dentist", today, "09:00")
    _event(base, "Bins out", today + timedelta(days=2))


CARDS = """JSON.stringify({
  title: document.getElementById('title').textContent,
  nav: performance.getEntriesByType('navigation').length,
  mark: window.__mark || null,
  cards: [...document.querySelectorAll('#screen .tv-card')].map(c => {
    const r = c.getBoundingClientRect();
    return {
      kind: c.getAttribute('data-kind'),
      spot: c.classList.contains('spot'),
      h2: c.querySelector('h2') ? c.querySelector('h2').textContent : null,
      text: c.innerText,
      grid: !!c.querySelector('.tv-cal-grid'),
      top: r.top, left: r.left, bottom: r.bottom, right: r.right,
    };
  }),
  scrollW: document.documentElement.scrollWidth,
  scrollH: document.documentElement.scrollHeight,
})"""

EMPTY = "Nothing on this screen yet. Add widgets from your phone."
SPOT_TEXT = "Empty spot. Add a widget on your phone: TV, Edit, then the screen name."


@needs_chrome
def test_daily_brief_renders_four_cards_in_grid(chrome, server):
    _seed_events(server)
    _show_screen(server, _screens(server)["Daily Brief"])
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    out = _until(
        lambda: _json(chrome, CARDS), lambda o: o and o["title"] == "Daily Brief" and o["cards"]
    )
    cards = out["cards"]
    assert [c["kind"] for c in cards] == ["clock", "weather", "calendar", "lists"]
    clock, weather_card, cal, lists = cards
    assert clock["h2"] == "Clock"
    today = date.today()
    assert today.strftime("%A") in clock["text"]
    assert cal["h2"] == f"{today:%a}, {today:%b} {today.day}" and cal["grid"] is False
    assert "Dentist" in cal["text"] and "9:00 am" in cal["text"]
    assert "Coming up" not in cal["text"] and "Bins out" not in cal["text"]
    assert weather_card["h2"] == "Weather" and "No forecast yet" in weather_card["text"]
    assert lists["h2"] == "Publix"
    assert clock["top"] == weather_card["top"] and clock["left"] < weather_card["left"]
    assert cal["top"] > clock["bottom"] and cal["left"] == clock["left"]
    assert max(c["bottom"] for c in cards) < 1080
    assert out["scrollW"] <= 1920 and out["scrollH"] <= 1080


@needs_chrome
def test_calendar_screen_shows_month_grid_and_switches_without_reload(chrome, server):
    _seed_events(server)
    screens = _screens(server)
    _show_screen(server, screens["Calendar"])
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    cal = _until(
        lambda: _json(chrome, CARDS), lambda o: o and o["title"] == "Calendar" and o["cards"]
    )
    chrome.eval("window.__mark = 'kept'")
    api.put(f"{server}/api/settings", json={"screen_id": screens["Daily Brief"]["id"]})
    brief = _until(lambda: _json(chrome, CARDS), lambda o: o["title"] == "Daily Brief")
    assert len(cal["cards"]) == 1, cal
    card = cal["cards"][0]
    assert card["kind"] == "calendar" and card["grid"] is True
    assert "Coming up" in card["text"] and "Bins out" in card["text"]
    assert card["h2"] == date.today().strftime("%B %Y")
    assert brief["mark"] == "kept" and brief["nav"] == 1
    assert len(brief["cards"]) == 4


FACE = """JSON.stringify((() => {
  const box = n => { const r = n.getBoundingClientRect(); return {l: r.left, r: r.right, t: r.top, b: r.bottom}; };
  const f = document.querySelector('#screen .tv-card[data-kind="clock"] .tv-face');
  if (!f) return null;
  return {title: document.getElementById('title').textContent,
    time: box(f.querySelector('.time')), day: box(f.querySelector('.day')),
    date: box(f.querySelector('.date')), text: f.innerText,
    size: parseFloat(getComputedStyle(f.querySelector('.time')).fontSize)};
})())"""


@needs_chrome
def test_wide_clock_puts_time_beside_day_and_date(chrome, server):
    chrome.size(1024, 600)
    faces = {}
    for w in (1, 2):
        name = f"Clock {w}x1"
        screen = _new_screen(server, name, [{"kind": "clock", "col": 0, "row": 0, "w": w, "h": 1}])
        _show_screen(server, screen)
        if not faces:
            chrome.send("Page.navigate", {"url": f"{server}/tv"})
        faces[w] = _steady(chrome, FACE, lambda f, n=name: f["title"] == n)
    small, wide = faces[1], faces[2]
    today = date.today()
    for face in (small, wide):
        assert today.strftime("%A") in face["text"]
        assert f"{today:%B} {today.day}, {today.year}" in face["text"]
    assert small["time"]["b"] <= small["day"]["t"] <= small["date"]["t"]
    t, d, dt = wide["time"], wide["day"], wide["date"]
    assert t["r"] <= d["l"] and t["r"] <= dt["l"]
    assert d["b"] <= dt["t"] + 1
    assert t["t"] < d["b"] and dt["t"] < t["b"]
    assert wide["size"] > small["size"]


@needs_chrome
def test_empty_spots_fill_uncovered_cells_and_empty_screen_shows_hint(chrome, server):
    shown = {}
    layouts = {
        "Wide": [{"kind": "clock", "col": 0, "row": 0, "w": 2, "h": 1}],
        "Tall": [
            {"kind": "calendar", "col": 0, "row": 0, "w": 1, "h": 2},
            {"kind": "clock", "col": 1, "row": 0, "w": 1, "h": 1},
        ],
        "Full": [
            {"kind": "clock", "col": 0, "row": 0, "w": 2, "h": 1},
            {"kind": "clock", "col": 0, "row": 1, "w": 2, "h": 1},
        ],
        "Bare": [],
    }
    for name, widgets in layouts.items():
        _show_screen(server, _new_screen(server, name, widgets))
        if not shown:
            chrome.send("Page.navigate", {"url": f"{server}/tv"})
        shown[name] = _steady(chrome, CARDS, lambda o, n=name: o["title"] == n and o["cards"])
    wide = shown["Wide"]["cards"]
    clock = [c for c in wide if c["kind"] == "clock"]
    spots = [c for c in wide if c["spot"]]
    assert len(clock) == 1 and len(spots) == 2, wide
    for s in spots:
        assert s["text"] == SPOT_TEXT
        assert s["top"] > clock[0]["bottom"] and s["bottom"] < 1080
    assert spots[0]["right"] < spots[1]["left"]
    tall = shown["Tall"]["cards"]
    cal = next(c for c in tall if c["kind"] == "calendar")
    small = next(c for c in tall if c["kind"] == "clock")
    spots = [c for c in tall if c["spot"]]
    assert [s["text"] for s in spots] == [SPOT_TEXT], tall
    assert spots[0]["left"] == small["left"] and spots[0]["top"] > small["bottom"]
    assert spots[0]["left"] > cal["right"]
    assert [c["kind"] for c in shown["Full"]["cards"]] == ["clock", "clock"]
    assert not any(c["spot"] for c in shown["Full"]["cards"])
    bare = shown["Bare"]["cards"]
    assert len(bare) == 1 and bare[0]["text"] == EMPTY and not bare[0]["spot"]


HINT = "Tap Edit to add, rename, lay out or delete screens."

SHOWN = """JSON.stringify({
  row: [...document.querySelectorAll('#tv-stops > *')].filter((e) => !e.hidden)
    .map((e) => e.textContent),
  hint: document.getElementById('screens-hint').hidden ? null
    : document.getElementById('screens-hint').textContent
})"""

BOXES = """JSON.stringify({
  row: (() => { const r = document.getElementById('tv-stops').getBoundingClientRect();
    return [r.left, r.width]; })(),
  screens: [...document.querySelectorAll('#tv-stops .stop-edit')].map((e) => {
    const r = e.getBoundingClientRect(); return [r.left, r.top, r.width, r.bottom]; }),
  add: document.getElementById('screens-new').getBoundingClientRect().top,
  done: document.getElementById('screens-edit').getBoundingClientRect().top,
  hint: !document.getElementById('screens-hint').hidden
})"""


@needs_chrome
def test_no_screens_shows_new_screen_without_edit(chrome, server):
    for s in api.get(f"{server}/api/screens").json():
        api.delete(f"{server}/api/screens/{s['id']}")
    chrome.size(390, 900)
    chrome.send("Page.navigate", {"url": f"{server}/admin"})
    want = {"row": ["Photos", "New screen", "Edit"], "hint": HINT}
    assert _until(lambda: _json(chrome, SHOWN), lambda v: v == want) == want


@needs_chrome
def test_edit_mode_puts_each_screen_on_its_own_row_at_390(chrome, server):
    names = [s["name"] for s in api.get(f"{server}/api/screens").json()]
    chrome.size(390, 900)
    chrome.send("Page.navigate", {"url": f"{server}/admin"})
    want = {"row": ["Photos", *names, "Edit"], "hint": HINT}
    assert _until(lambda: _json(chrome, SHOWN), lambda v: v == want) == want
    chrome.eval("document.getElementById('screens-edit').click()")
    boxes = _json(chrome, BOXES)
    left, width = boxes["row"]
    tops = [s[1] for s in boxes["screens"]]
    assert len(tops) == len(names) > 1 and tops == sorted(set(tops))
    for s in boxes["screens"]:
        assert abs(s[0] - left) < 1 and abs(s[2] - width) < 1
    assert boxes["add"] == boxes["done"]
    assert boxes["add"] >= boxes["screens"][-1][3] - 1
    assert boxes["hint"] is False


SLIDE = """(() => {
  const f = document.getElementById('channel');
  const d = f && f.contentDocument;
  if (!d || d.location.pathname !== '/tv' || !d.getElementById('screen')) return null;
  return {
    grids: d.querySelectorAll('.tv-screen').length,
    transform: getComputedStyle(d.getElementById('screen')).transform,
    title: d.getElementById('title').textContent,
    cards: d.querySelectorAll('#screen .tv-card').length,
    sliding: f.classList.contains('slide'),
  };
})()"""


def _tx(transform):
    if transform in (None, "none"):
        return 0.0
    return float(transform[transform.index("(") + 1 : -1].split(",")[4])


@needs_chrome
def test_screen_to_screen_swipe_slides_inside_tv(chrome, server):
    ordered = sorted(api.get(f"{server}/api/screens").json(), key=lambda s: s["sort_order"])
    first, second = ordered[0], ordered[1]
    _show_screen(server, first)
    chrome.size(1024, 600)
    chrome.touch()
    chrome.send("Page.navigate", {"url": f"{server}/"})
    ready = _until(
        lambda: chrome.eval(SLIDE), lambda o: o and o["title"] == first["name"] and o["cards"]
    )
    _move(chrome, "touchStart", 800)
    for i in range(1, 6):
        _move(chrome, "touchMove", 800 - 30 * i)
    mid = _until(lambda: chrome.eval(SLIDE), lambda o: o and o["grids"] == 2, tries=40)
    for i in range(6, 11):
        _move(chrome, "touchMove", 800 - 30 * i)
    _move(chrome, "touchEnd", 500)
    landed = _until(lambda: _settings(server), lambda s: s["screen_id"] == second["id"])
    after = _until(
        lambda: chrome.eval(SLIDE),
        lambda o: o["grids"] == 1 and o["title"] == second["name"] and not o["sliding"],
    )
    _move(chrome, "touchStart", 800)
    for i in range(1, 5):
        _move(chrome, "touchMove", 800 - 10 * i)
    short_mid = chrome.eval(SLIDE)
    chrome.eval("__test.skip(250)")
    _move(chrome, "touchEnd", 760)
    short = _until(
        lambda: chrome.eval(SLIDE),
        lambda o: o["grids"] == 1 and _tx(o["transform"]) == 0 and not o["sliding"],
    )
    kept = _settings(server)
    assert ready["grids"] == 1
    assert mid["grids"] == 2
    assert -1024 < _tx(mid["transform"]) < 0
    assert (landed["channel"], landed["screen_id"]) == ("screen", second["id"])
    assert after["grids"] == 1
    assert after["transform"] in ("none", "matrix(1, 0, 0, 1, 0, 0)")
    assert short_mid["grids"] == 2
    assert kept["screen_id"] == second["id"]
    assert short["grids"] == 1
    assert short["title"] == second["name"]
    assert _tx(short["transform"]) == 0


@needs_chrome
def test_drag_from_photos_shows_the_target_screen_before_the_slide_ends(chrome, server):
    ordered = sorted(api.get(f"{server}/api/screens").json(), key=lambda s: s["sort_order"])
    first, last = ordered[0], ordered[-1]
    api.put(f"{server}/api/settings", json={"channel": "photos", "screen_id": last["id"]})
    chrome.size(1024, 600)
    chrome.touch()
    chrome.send("Page.navigate", {"url": f"{server}/"})
    ready = _until(
        lambda: chrome.eval(SLIDE), lambda o: o and o["title"] == last["name"] and o["cards"]
    )
    _move(chrome, "touchStart", 800)
    for i in range(1, 6):
        _move(chrome, "touchMove", 800 - 30 * i)
    mid = chrome.eval(SLIDE)
    kept = _settings(server)
    for i in range(6, 11):
        _move(chrome, "touchMove", 800 - 30 * i)
    _move(chrome, "touchEnd", 500)
    landed = _until(lambda: _settings(server), lambda s: s["channel"] == "screen")
    after = _until(lambda: chrome.eval(SLIDE), lambda o: o and o["title"] == first["name"])
    assert ready["title"] == last["name"]
    assert kept["channel"] == "photos"
    assert mid["title"] == first["name"]
    assert mid["grids"] == 1
    assert (landed["channel"], landed["screen_id"]) == ("screen", first["id"])
    assert after["title"] == first["name"]


KIOSK_TX = "getComputedStyle(document.getElementById('channel')).transform"

KIOSK_READY = """(() => {
  const f = document.getElementById('channel');
  const d = f.contentDocument;
  return !!(d && d.readyState === 'complete' && d.location.pathname === '/tv'
    && performance.getEntriesByName(location.origin + '/api/screens').length
    && !f.classList.contains('slide'));
})()"""


@needs_chrome
def test_kiosk_tv_window_follows_finger(chrome, server):
    first = min(api.get(f"{server}/api/screens").json(), key=lambda s: s["sort_order"])
    chrome.size(1024, 600)
    chrome.touch()
    chrome.send("Page.navigate", {"url": f"{server}/"})
    _until(lambda: chrome.eval(KIOSK_READY), bool)
    rest = _until(lambda: _tx(chrome.eval(KIOSK_TX)), lambda v: v >= 1024)
    _move(chrome, "touchStart", 800)
    for i in range(1, 6):
        _move(chrome, "touchMove", 800 - 30 * i)
    mid = _until(lambda: _tx(chrome.eval(KIOSK_TX)), lambda v: 0 < v < 1024, tries=40)
    for i in range(6, 11):
        _move(chrome, "touchMove", 800 - 30 * i)
    _move(chrome, "touchEnd", 500)
    landed = _until(lambda: _settings(server), lambda s: s["channel"] == "screen")
    shown = _until(lambda: _tx(chrome.eval(KIOSK_TX)), lambda v: v == 0)
    _until(lambda: chrome.eval(KIOSK_READY), bool)
    _move(chrome, "touchStart", 200)
    for i in range(1, 6):
        _move(chrome, "touchMove", 200 + 30 * i)
    out = _until(lambda: _tx(chrome.eval(KIOSK_TX)), lambda v: v >= 150, tries=40)
    for i in range(6, 11):
        _move(chrome, "touchMove", 200 + 30 * i)
    _move(chrome, "touchEnd", 500)
    back = _until(lambda: _settings(server), lambda s: s["channel"] == "photos")
    gone = _until(lambda: _tx(chrome.eval(KIOSK_TX)), lambda v: v >= 1024)
    assert rest >= 1024
    assert 0 < mid < 1024
    assert (landed["channel"], landed["screen_id"]) == ("screen", first["id"])
    assert shown == 0
    assert out == 150
    assert back["channel"] == "photos"
    assert gone >= 1024


ROW = """JSON.stringify((() => {
  const li = document.querySelector('#screen li[data-item="%d"]');
  if (!li || li.hidden) return null;
  const r = li.getBoundingClientRect();
  return {x: r.left + r.width / 2, y: r.top + r.height / 2, done: li.classList.contains('done')};
})())"""

HEIGHTS = """JSON.stringify((() => {
  const rows = [...document.querySelectorAll('#screen [data-item]')].filter(n => !n.hidden);
  return {condensed: !!document.querySelector('.tv-lists.condensed'),
    notes: document.querySelectorAll('.tv-card.notes li[data-item]').length,
    heights: rows.map(n => n.getBoundingClientRect().height)};
})())"""


def _done(base, item_id):
    for lst in api.get(f"{base}/api/lists").json():
        for item in lst["items"]:
            if item["id"] == item_id:
                return item["done"]
    return None


@needs_chrome
def test_tapping_tv_rows_ticks_items_and_swipes_do_not(chrome, server):
    with api_at(server) as c:
        publix = _lists(server)["Publix"]["id"]
        item, other = (
            c.post(f"/api/lists/{publix}/items", json={"text": t}).json()
            for t in ("Paper towels", "Olive oil")
        )
        c.post(f"/api/lists/{publix}/items", json={"text": "Rice"})
        notes = c.post("/api/lists", json={"name": "Message board", "kind": "notes"}).json()
        c.post(f"/api/lists/{notes['id']}/items", json={"text": "Plumber Tuesday"})
    screen = _screens(server)["Notes"]
    widget = screen["widgets"][0]
    _show_screen(server, screen)
    row = ROW % item["id"]
    chrome.size(1024, 600)
    chrome.touch()
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    first = _until(lambda: _json(chrome, row), bool)
    full = _json(chrome, HEIGHTS)
    _tap(chrome, first["x"], first["y"])
    start = time.monotonic()
    ticked = _until(lambda: _json(chrome, row), lambda r: r and r["done"])
    ticked_s = time.monotonic() - start
    api_on = _until(lambda: _done(server, item["id"]), bool)
    again = _json(chrome, row)
    _tap(chrome, again["x"], again["y"])
    unticked = _until(lambda: _json(chrome, row), lambda r: r and not r["done"])
    api_off = _until(lambda: _done(server, item["id"]), lambda v: v is False)
    here = _json(chrome, row)
    _swipe(chrome, [{"x": here["x"] + 15 * i, "y": here["y"]} for i in range(11)])
    there = _json(chrome, ROW % other["id"])
    _tap(chrome, there["x"], there["y"])
    _until(lambda: _done(server, other["id"]), bool)
    after_swipe = _json(chrome, row)
    api_swipe = _done(server, item["id"])
    assert api.patch(f"{server}/api/widgets/{widget['id']}", json={"h": 1}).status_code == 200
    small = _until(lambda: _json(chrome, HEIGHTS), lambda o: o["condensed"])
    assert ticked and ticked["done"] and ticked_s < 1
    assert api_on is True
    assert unticked and not unticked["done"] and api_off is False
    assert after_swipe["done"] is False and api_swipe is False
    for out in (full, small):
        assert out["heights"] and out["notes"] == 0
        assert all(h >= 48 for h in out["heights"]), out
    assert small["condensed"]


def _seed_three(base):
    with api_at(base) as c:
        lists = _lists(base)
        publix, bjs = lists["Publix"], lists["BJs"]
        for text in ("Paper towels", "Rotisserie chicken", "Olive oil"):
            c.post(f"/api/lists/{publix['id']}/items", json={"text": text})
        pin = c.post(f"/api/lists/{publix['id']}/items", json={"text": "Dog food"}).json()
        c.patch(f"/api/items/{pin['id']}", json={"pinned": True})
        for text in ("Water", "Rice", "Batteries", "Foil"):
            c.post(f"/api/lists/{bjs['id']}/items", json={"text": text})
        done = c.post(f"/api/lists/{bjs['id']}/items", json={"text": "Bread"}).json()
        c.patch(f"/api/items/{done['id']}", json={"done": True})
        notes = c.post("/api/lists", json={"name": "Message board", "kind": "notes"}).json()
        c.post(f"/api/lists/{notes['id']}/items", json={"text": "Plumber comes Tuesday 9am"})
        assert c.patch(f"/api/lists/{bjs['id']}", json={"tv_open": False}).status_code == 200
    return _lists(base)


LISTS = """JSON.stringify((() => {
  const doc = %s;
  if (!doc || !doc.getElementById('screen')) return null;
  const box = n => { const r = n.getBoundingClientRect();
    return {x: r.left + r.width / 2, y: r.top + r.height / 2, top: r.top, bottom: r.bottom,
      left: r.left, right: r.right}; };
  const card = doc.querySelector('#screen .tv-card[data-kind="lists"]');
  return {
    title: doc.getElementById('title').textContent,
    h2: card ? card.querySelector('h2').textContent : null,
    tabs: card ? [...card.querySelectorAll('.tv-tab')].map(t => ({text: t.textContent, ...box(t)})) : [],
    cards: card ? [...card.querySelectorAll('.tv-list')].map(c => ({
      name: c.querySelector('h2').textContent, head: box(c.querySelector('h2')), ...box(c),
      notes: c.classList.contains('notes'),
      pin: !!c.querySelector('li.pin'), fits: c.querySelector('ul').scrollHeight <= c.querySelector('ul').clientHeight,
      text: c.innerText})) : [],
    condensed: card ? !!card.querySelector('.tv-lists.condensed') : false,
    msg: card && card.querySelector('.tv-msg') ? card.querySelector('.tv-msg').textContent : null,
    text: card ? card.innerText : '',
    scrollW: doc.documentElement.scrollWidth,
    scrollH: doc.documentElement.scrollHeight,
  };
})())"""
LISTS_PAGE = LISTS % "document"
LISTS_FRAME = LISTS % "document.getElementById('channel').contentDocument"


def _lists_shown(chrome, expr, ready):
    return _until(lambda: _json(chrome, expr), lambda o: o and ready(o))


@needs_chrome
def test_notes_screen_tabs_open_and_titles_close_lists(chrome, server):
    lists = _seed_three(server)
    _show_screen(server, _screens(server)["Notes"])
    bjs = lists["BJs"]["id"]
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    before = _lists_shown(chrome, LISTS_PAGE, lambda o: o["title"] == "Notes" and o["cards"])
    tab = before["tabs"][0]
    _click(chrome, tab["x"], tab["y"])
    opened = _until(lambda: _lists(server)["BJs"]["tv_open"], bool)
    after = _lists_shown(chrome, LISTS_PAGE, lambda o: len(o["cards"]) == 3)
    head = next(c for c in after["cards"] if c["name"] == "Publix")["head"]
    _click(chrome, head["x"], head["y"])
    closed = _until(lambda: _lists(server)["Publix"]["tv_open"], lambda v: v is False)
    last = _lists_shown(chrome, LISTS_PAGE, lambda o: len(o["cards"]) == 2 and o["tabs"])
    assert before["h2"] == "Lists"
    assert [c["name"] for c in before["cards"]] == ["Publix", "Message board"]
    assert [t["text"] for t in before["tabs"]] == ["BJs 4"]
    a, b = before["cards"]
    assert a["top"] == b["top"] and a["right"] < b["left"]
    assert abs((a["right"] - a["left"]) - (b["right"] - b["left"])) <= 1
    assert tab["bottom"] < a["top"]
    assert a["pin"] and b["notes"] and a["fits"] and b["fits"]
    assert before["scrollW"] <= 1920 and before["scrollH"] <= 1080
    assert opened is True and _lists(server)["BJs"]["id"] == bjs
    assert [c["name"] for c in after["cards"]] == ["Publix", "BJs", "Message board"]
    assert after["tabs"] == []
    assert "Bread" in next(c for c in after["cards"] if c["name"] == "BJs")["text"]
    assert closed is False
    assert [c["name"] for c in last["cards"]] == ["BJs", "Message board"]
    assert [t["text"] for t in last["tabs"]] == ["Publix 4"]


@needs_chrome
def test_smaller_lists_widgets_show_open_lists_or_one_list(chrome, server):
    lists = _seed_three(server)
    screens = _screens(server)
    notes = screens["Notes"]
    widget = notes["widgets"][0]
    assert api.patch(f"{server}/api/widgets/{widget['id']}", json={"h": 1}).status_code == 200
    _show_screen(server, notes)
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    wide = _lists_shown(chrome, LISTS_PAGE, lambda o: o["title"] == "Notes" and o["condensed"])
    for name in ("Publix", "Message board"):
        api.patch(f"{server}/api/lists/{lists[name]['id']}", json={"tv_open": False})
    empty = _lists_shown(chrome, LISTS_PAGE, lambda o: o["msg"])
    brief = screens["Daily Brief"]
    api.put(f"{server}/api/settings", json={"screen_id": brief["id"]})
    one = _lists_shown(
        chrome, LISTS_PAGE, lambda o: o["title"] == "Daily Brief" and o["h2"] == "Publix"
    )
    lists_widget = next(w for w in brief["widgets"] if w["kind"] == "lists")
    api.patch(f"{server}/api/widgets/{lists_widget['id']}", json={"list_id": None})
    pick = _lists_shown(chrome, LISTS_PAGE, lambda o: o["msg"])
    assert [c["name"] for c in wide["cards"]] == ["Publix", "Message board"]
    assert wide["tabs"] == [] and "BJs" not in wide["text"]
    assert wide["cards"][0]["top"] == wide["cards"][1]["top"]
    assert empty["msg"] == "No lists open. Tick Open on the TV in a list's options on your phone."
    assert empty["cards"] == [] and empty["tabs"] == []
    assert one["h2"] == "Publix" and "Dog food" in one["text"] and "Olive oil" in one["text"]
    assert one["tabs"] == []
    assert pick["h2"] == "Lists"
    assert pick["msg"] == "Pick a list on your phone: TV, Edit, then this screen."


TAB = """(() => {
  const t = document.getElementById('channel').contentDocument.querySelector('.tv-tab');
  if (!t) return null;
  const r = t.getBoundingClientRect();
  return {x: r.left + r.width / 2, y: r.top + r.height / 2, text: t.textContent};
})()"""


@needs_chrome
def test_kiosk_swipe_changes_screen_and_tap_reaches_iframe(chrome, server):
    lists = _seed_three(server)
    screens = _screens(server)
    cal, notes = screens["Calendar"], screens["Notes"]
    _show_screen(server, cal)
    chrome.touch()
    chrome.send("Page.navigate", {"url": f"{server}/"})
    _lists_shown(chrome, LISTS_FRAME, lambda o: o["title"] == "Calendar")
    _swipe(chrome, [{"x": 1200 - 20 * i, "y": 540} for i in range(11)])
    swiped = _until(lambda: _settings(server), lambda s: s["screen_id"] == notes["id"])
    _lists_shown(chrome, LISTS_FRAME, lambda o: o["title"] == "Notes" and o["tabs"])
    tab = chrome.eval(TAB)
    _swipe(chrome, [{"x": tab["x"] + 20 * i, "y": tab["y"]} for i in range(11)])
    back = _until(lambda: _settings(server), lambda s: s["screen_id"] == cal["id"])
    _lists_shown(chrome, LISTS_FRAME, lambda o: o["title"] == "Calendar")
    still = _lists(server)["BJs"]["tv_open"]
    _swipe(chrome, [{"x": 1200 - 20 * i, "y": 540} for i in range(11)])
    _lists_shown(chrome, LISTS_FRAME, lambda o: o["title"] == "Notes" and o["tabs"])
    tab = chrome.eval(TAB)
    chrome.eval(
        "__test.skip(1000); document.getElementById('channel').contentWindow.__test.skip(1000)"
    )
    _tap(chrome, tab["x"], tab["y"])
    opened = _until(lambda: _lists(server)["BJs"]["tv_open"], bool)
    frame = _lists_shown(chrome, LISTS_FRAME, lambda o: len(o["cards"]) == 3)
    settings = _settings(server)
    assert lists["BJs"]["tv_open"] is False
    assert (swiped["channel"], swiped["screen_id"]) == ("screen", notes["id"])
    assert back["screen_id"] == cal["id"] and still is False
    assert tab["text"] == "BJs 4"
    assert opened is True
    assert len(frame["cards"]) == 3
    assert (settings["channel"], settings["screen_id"]) == ("screen", notes["id"])


def _seed_month(base, extra=0):
    today = date.today()
    _event(base, "Vet", today, "15:00")
    _event(base, "Trash day", today + timedelta(days=1), None, "weekly")
    _event(base, "Dinner", today + timedelta(days=1), "18:30")
    for i in range(extra):
        _event(base, f"Extra {i}", today)
    _show_screen(base, _screens(base)["Calendar"])
    return today


CELLS = """JSON.stringify([...document.querySelectorAll('.tv-cal-grid .d')].map(d => ({
  date: d.dataset.date, cls: d.className,
  chips: [...d.querySelectorAll('.ev')].map(e => e.textContent),
  fits: d.querySelector('.chips').scrollHeight <= d.querySelector('.chips').clientHeight})))"""


def _cells(chrome, base):
    chrome.send("Page.navigate", {"url": f"{base}/tv"})
    return _until(
        lambda: json.loads(chrome.eval(CELLS) or "[]"), lambda v: any(c["chips"] for c in v)
    )


@needs_chrome
def test_month_widget_marks_today_and_places_repeats(chrome, server):
    today = _seed_month(server)
    cells = _cells(chrome, server)
    by_date = {c["date"]: c for c in cells}
    t = today.isoformat()
    assert [c["date"] for c in cells if "today" in c["cls"]] == [t]
    assert by_date[t]["chips"] == ["Vet 3pm"]
    tomorrow = (today + timedelta(days=1)).isoformat()
    if tomorrow in by_date:
        assert by_date[tomorrow]["chips"] == ["Trash day", "Dinner 6:30"]
    trash_days = [c["date"] for c in cells if "Trash day" in c["chips"]]
    assert trash_days and all(
        (date.fromisoformat(d) - today).days % 7 == 1 and d >= tomorrow for d in trash_days
    )
    assert all(c["fits"] for c in cells)
    for c in cells:
        assert ("other" in c["cls"]) == (c["date"][:7] != t[:7])


@needs_chrome
def test_too_many_chips_are_cut_with_plus_n(chrome, server):
    today = _seed_month(server, extra=12)
    chrome.size(1280, 800)
    cells = _cells(chrome, server)
    cell = next(c for c in cells if c["date"] == today.isoformat())
    assert cell["fits"]
    more = cell["chips"][-1]
    assert re.fullmatch(r"\+\d+", more)
    assert len(cell["chips"]) - 1 + int(more[1:]) == 13


CAL_CARD = """JSON.stringify((() => {
  const c = document.querySelector('#screen .tv-card[data-kind="calendar"]');
  if (!c) return null;
  return {
    title: document.getElementById('title').textContent,
    h2: c.querySelector('h2').textContent,
    text: c.innerText,
    grid: !!c.querySelector('.tv-cal-grid:not(.tv-week)'),
    up: c.innerText.includes('Coming up'),
    week: [...c.querySelectorAll('.tv-week .d')].map(d => ({
      date: d.dataset.date, cls: d.className, head: d.querySelector('.dh').innerText,
      chips: [...d.querySelectorAll('.ev')].map(e => e.textContent)})),
  };
})())"""


@needs_chrome
def test_calendar_content_per_size(chrome, server):
    today = date.today()
    screens = {}
    for w, h in ((1, 1), (2, 1), (1, 2), (2, 2)):
        widget = {"kind": "calendar", "col": 0, "row": 0, "w": w, "h": h}
        screens[f"{w}x{h}"] = _new_screen(server, f"Cal {w}x{h}", [widget])
    label = f"{today:%a}, {today:%b} {today.day}"
    _event(server, "Dentist", today + timedelta(days=2), "09:00")
    _event(server, "Far trip", today + timedelta(days=9))
    chrome.size(1024, 600)
    _show_screen(server, screens["1x1"])
    chrome.send("Page.navigate", {"url": f"{server}/tv"})

    def card(name, ready=lambda c: True):
        _show_screen(server, screens[name])
        return _steady(chrome, CAL_CARD, lambda c: c["title"] == screens[name]["name"] and ready(c))

    empty_one = card("1x1", lambda c: "Dentist" in c["text"])
    _event(server, "Vet", today, "15:00")
    _event(server, "Bins", today)
    one = card("1x1", lambda c: "Vet" in c["text"] and "Bins" in c["text"])
    wide = card("2x1")
    tall = card("1x2")
    full = card("2x2")
    assert empty_one["h2"] == label
    assert "Nothing today" in empty_one["text"]
    assert "Dentist" in empty_one["text"] and "9:00 am" in empty_one["text"]
    assert "Far trip" not in empty_one["text"] and not empty_one["up"]
    assert one["h2"] == label
    assert one["text"].index("Bins") < one["text"].index("Vet")
    assert "3:00 pm" in one["text"] and "all day" in one["text"]
    assert "Nothing today" not in one["text"] and "Dentist" not in one["text"]
    assert wide["h2"] == "This week" and not wide["grid"]
    days = [today + timedelta(days=i) for i in range(7)]
    assert [w["date"] for w in wide["week"]] == [d.isoformat() for d in days]
    assert [w["head"].split() for w in wide["week"]] == [[f"{d:%a}", str(d.day)] for d in days]
    assert ["today" in w["cls"] for w in wide["week"]] == [True] + [False] * 6
    assert wide["week"][0]["chips"] == ["Bins", "Vet 3pm"]
    assert wide["week"][2]["chips"] == ["Dentist 9am"]
    assert tall["h2"] == "Today" and tall["up"]
    assert "Vet" in tall["text"] and "Dentist" in tall["text"]
    assert full["grid"] and full["up"] and full["h2"] == today.strftime("%B %Y")


CELL = """JSON.stringify((() => {
  const d = document.querySelector('%s[data-date="%s"]');
  if (!d) return null;
  const r = d.getBoundingClientRect();
  return {x: r.left + r.width / 2, y: r.top + r.height / 2};
})())"""

DIALOG = """JSON.stringify((() => {
  const box = document.querySelector('#day-dialog .dialog');
  const r = box.getBoundingClientRect();
  if (!r.width) return null;
  const x = document.querySelector('#day-dialog .title-close').getBoundingClientRect();
  return {text: box.textContent, x: x.left + x.width / 2, y: x.top + x.height / 2,
    w: x.width, h: x.height, width: r.width,
    font: parseFloat(getComputedStyle(document.querySelector('#day-list li')).fontSize)};
})())"""


@needs_chrome
def test_tapping_a_month_day_opens_a_dialog(chrome, server):
    today = date.today()
    _event(server, "Dentist visit", today, "15:00")
    _event(server, "Pick up cake", today)
    screens = _screens(server)
    _show_screen(server, screens["Calendar"])
    cell = CELL % (".tv-cal-grid .d", today.isoformat())
    chrome.size(1024, 600)
    chrome.touch()
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    at = _until(lambda: _json(chrome, cell), bool)
    assert _json(chrome, DIALOG) is None
    _tap(chrome, at["x"], at["y"])
    opened = _until(lambda: _json(chrome, DIALOG), bool)
    _tap(chrome, opened["x"], opened["y"])
    after_close = _until(lambda: _json(chrome, DIALOG), lambda v: v is None)
    _tap(chrome, at["x"], at["y"])
    reopened = _until(lambda: _json(chrome, DIALOG), bool)
    _tap(chrome, 40, 300)
    after_backdrop = _until(lambda: _json(chrome, DIALOG), lambda v: v is None)
    _tap(chrome, at["x"], at["y"])
    third = _until(lambda: _json(chrome, DIALOG), bool)
    api.put(f"{server}/api/settings", json={"screen_id": screens["Notes"]["id"]})
    after_screen = _until(lambda: _json(chrome, DIALOG), lambda v: v is None)
    long = f"{today:%A}, {today:%B} {today.day}, {today.year}"
    assert opened, "dialog did not open"
    assert "Dentist visit" in opened["text"] and "Pick up cake" in opened["text"]
    assert long in opened["text"]
    assert opened["text"].index("Pick up cake") < opened["text"].index("Dentist visit")
    assert opened["w"] >= 48 and opened["h"] >= 48
    assert opened["font"] >= 18
    assert 0.5 * 1024 <= opened["width"] <= 0.7 * 1024
    assert after_close is None
    assert reopened and after_backdrop is None
    assert third and after_screen is None


@needs_chrome
def test_tapping_a_week_strip_day_opens_a_dialog(chrome, server):
    day = date.today() + timedelta(days=3)
    _event(server, "Piano lesson", day, "16:00")
    widget = {"kind": "calendar", "col": 0, "row": 0, "w": 2, "h": 1}
    _show_screen(server, _new_screen(server, "Week", [widget]))
    chrome.size(1024, 600)
    chrome.touch()
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    at = _until(lambda: _json(chrome, CELL % (".tv-week .d", day.isoformat())), bool)
    assert at and _json(chrome, DIALOG) is None
    _tap(chrome, at["x"], at["y"])
    opened = _until(lambda: _json(chrome, DIALOG), bool)
    assert opened, "dialog did not open"
    assert f"{day:%A}, {day:%B} {day.day}, {day.year}" in opened["text"]
    assert "Piano lesson" in opened["text"] and "4:00 pm" in opened["text"]


WX_CARD = """JSON.stringify((() => {
  const c = document.querySelector('#screen .tv-card[data-kind="weather"]');
  if (!c) return null;
  const q = s => c.querySelector(s) ? c.querySelector(s).textContent : null;
  const r = c.getBoundingClientRect();
  const inner = c.querySelector('.tv-wx').getBoundingClientRect();
  return {title: document.getElementById('title').textContent, h2: q('h2'), temp: q('.wx-temp'),
    label: q('.wx-label'), hilo: q('.wx-hilo'), rain: q('.wx-rain'), asof: q('.wx-asof'),
    where: q('.wx-where'), hint: q('.wx-hint'), sun: q('.wx-sun'), svg: c.querySelectorAll('svg').length,
    days: [...c.querySelectorAll('.wx-day')].map(d => d.innerText.replace(/\\s+/g, ' ').trim()),
    strip: c.querySelectorAll('.wx-strip .wx-day').length, rows: c.querySelectorAll('.wx-rows .wx-day').length,
    bold: [...c.querySelectorAll('.wx-dname')].map(n => getComputedStyle(n).fontWeight === '700'),
    fits: inner.bottom <= r.bottom && inner.right <= r.right, text: c.innerText};
})())"""


def _weather_card(chrome, base, ready):
    chrome.send("Page.navigate", {"url": f"{base}/tv"})
    return _until(lambda: _json(chrome, WX_CARD), lambda o: o and ready(o))


def _week():
    today = date.today()
    names = [(today + timedelta(days=i)).strftime("%a") for i in range(7)]
    cells = [
        "H 78 L 55 20%",
        "H 70 L 52 80%",
        "H 66 L 50 --%",
        "H 74 L 51 0%",
        "H 69 L 50 10%",
        "H 40 L 30 60%",
        "H 61 L 48 5%",
    ]
    return [f"{n} {c}" for n, c in zip(names, cells, strict=True)]


@needs_chrome
def test_weather_card_shows_cached_forecast_at_each_size(chrome, server, tmp_path):
    weather._last_fail.clear()
    _seed_cache(server, tmp_path / "data", datetime.now(UTC))
    screens = {"Daily Brief": _screens(server)["Daily Brief"]}
    for name, w, h in (("Sky", 2, 2), ("Wide sky", 2, 1), ("Tall sky", 1, 2), ("Small sky", 1, 1)):
        widget = {"kind": "weather", "col": 0, "row": 0, "w": w, "h": h}
        screens[name] = _new_screen(server, name, [widget])
    _show_screen(server, screens["Daily Brief"])
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    out = {}
    for name, screen in screens.items():
        _show_screen(server, screen)
        out[name] = _steady(chrome, WX_CARD, lambda o, n=name: o["title"] == n and o["temp"])
    brief = out["Daily Brief"]
    assert brief["h2"] == "Newnan"
    assert brief["temp"] == "71°" and brief["label"] == "Sunny"
    assert brief["hilo"] == "H 78 L 55" and brief["rain"] == "Rain 20%"
    assert brief["asof"] is None and brief["days"] == [] and brief["sun"] is None
    assert brief["svg"] == 1 and brief["fits"]
    sky = out["Sky"]
    assert sky["temp"] == "71°" and sky["label"] == "Sunny" and sky["hilo"] == "H 78 L 55"
    assert sky["days"] == _week() and sky["strip"] == 7 and sky["rows"] == 0
    assert sky["bold"] == [True] + [False] * 6
    assert sky["sun"] and "Sunrise 7:50 am" in sky["sun"] and "Sunset 6:50 pm" in sky["sun"]
    assert sky["svg"] == 8 and sky["fits"]
    wide = out["Wide sky"]
    assert wide["temp"] == "71°" and wide["label"] == "Sunny"
    assert wide["hilo"] == "H 78 L 55" and wide["rain"] == "Rain 20%"
    assert wide["days"] == _week() and wide["strip"] == 7 and wide["rows"] == 0
    assert wide["bold"] == [True] + [False] * 6
    assert wide["sun"] is None and wide["svg"] == 8 and wide["fits"]
    tall = out["Tall sky"]
    assert tall["temp"] == "71°" and tall["label"] == "Sunny"
    assert tall["hilo"] == "H 78 L 55" and tall["rain"] == "Rain 20%"
    assert tall["days"] == _week() and tall["rows"] == 7 and tall["strip"] == 0
    assert tall["sun"] is None and tall["svg"] == 8 and tall["fits"]
    small = out["Small sky"]
    assert small["temp"] == "71°" and small["hilo"] == "H 78 L 55" and small["rain"] == "Rain 20%"
    assert small["days"] == [] and small["sun"] is None and small["svg"] == 1 and small["fits"]


@needs_chrome
def test_stale_forecast_shows_as_of_line(chrome, server, tmp_path):
    weather._last_fail.clear()
    fetched = datetime.now(UTC) - timedelta(hours=3)
    _seed_cache(server, tmp_path / "data", fetched)
    _show_screen(server, _screens(server)["Daily Brief"])
    out = _weather_card(chrome, server, lambda o: o["temp"])
    local = fetched.astimezone()
    clock = f"{local.hour % 12 or 12}:{local.minute:02d} {'am' if local.hour < 12 else 'pm'}"
    assert out["temp"] == "71°"
    assert out["asof"] == f"as of {clock}"


@needs_chrome
def test_weather_card_without_forecast_says_so(chrome, server, tmp_path):
    weather._last_fail.clear()
    assert not weather.cache_path(tmp_path / "data").exists()
    _show_screen(server, _screens(server)["Daily Brief"])
    out = _weather_card(chrome, server, lambda o: o["label"])
    assert out["h2"] == "Weather"
    assert out["label"] == "No forecast yet"
    assert out["where"] == "30263"
    assert out["hint"] == "Check the weather location on your phone: TV page, Weather."
    assert out["temp"] is None


DIM = """JSON.stringify((() => {
  const d = document.getElementById('dim');
  if (!d || !d.style.opacity) return null;
  return { dim: parseFloat(getComputedStyle(d).opacity) };
})())"""


@needs_chrome
def test_brightness_dims_and_survives_a_swipe(chrome, server):
    chrome.size(1024, 600)
    chrome.touch()
    chrome.send("Page.navigate", {"url": f"{server}/"})
    _until(lambda: _json(chrome, DIM), bool)
    edge = chrome.eval("document.getElementById('edge')")
    api.put(f"{server}/api/settings", json={"brightness": 50})
    live = _until(lambda: _json(chrome, DIM), lambda s: s and abs(s["dim"] - 0.5) < 0.02)
    _swipe(chrome, [{"x": 200 + 30 * i, "y": 300} for i in range(11)])
    swiped = _until(lambda: _settings(server), lambda s: s["channel"] == "screen")
    api.put(f"{server}/api/settings", json={"brightness": 40})
    chrome.send("Page.navigate", {"url": f"{server}/"})
    reloaded = _until(lambda: _json(chrome, DIM), lambda s: s and abs(s["dim"] - 0.6) < 0.02)
    assert edge is None
    assert live["dim"] == pytest.approx(0.5, abs=0.02)
    assert swiped["brightness"] == 50
    assert reloaded["dim"] == pytest.approx(0.6, abs=0.02)


class FakeSpotify:
    def __init__(self):
        self.now = _playing()
        self.calls = []
        self.fail = {}
        self.connected = False

    def now_playing(self, data_dir, fresh=False):
        self.calls.append(("GET", "/api/spotify/now?fresh=1" if fresh else "/api/spotify/now"))
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

    def play_here(self, data_dir):
        self.calls.append(("POST", "/api/spotify/here"))
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
    fake_api = FakeSpotify()
    monkeypatch.setattr(spotify, "now_playing", fake_api.now_playing)
    monkeypatch.setattr(spotify, "control", fake_api.control)
    monkeypatch.setattr(spotify, "play_playlist", fake_api.play_playlist)
    monkeypatch.setattr(spotify, "play_here", fake_api.play_here)
    monkeypatch.setattr(routes_sound, "helper", fake_api.helper)
    return fake_api


SP_SHOWN = [
    {"id": "pl-road", "name": "Road trip", "cover": _png((40, 140, 70))},
    {"id": "pl-dinner", "name": "Dinner jazz", "cover": _png((40, 70, 160))},
    {"id": "pl-kids", "name": "Kids songs", "cover": None},
    {"id": "pl-focus", "name": "Focus", "cover": _png((150, 40, 120))},
    {"id": "pl-80s", "name": "Eighties", "cover": _png((220, 200, 40))},
    {"id": "pl-rain", "name": "Rainy day", "cover": _png((90, 90, 90))},
]

SP_CARD = """JSON.stringify((() => {
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
    out, small};
})())"""

SP_HIDE = """Object.defineProperty(document, 'visibilityState', {get: () => '%s', configurable: true});
document.dispatchEvent(new Event('visibilitychange'))"""


def _sp_setup(base, sizes=SP_SIZES):
    api.put(f"{base}/api/spotify/shown", json=SP_SHOWN).raise_for_status()
    widget = {"kind": "spotify", "col": 0, "row": 0}
    return {
        (w, h): _new_screen(base, f"Music {w}x{h}", [{**widget, "w": w, "h": h}]) for w, h in sizes
    }


def _sp_card(chrome, title, ready):
    out = _until(lambda: _json(chrome, SP_CARD), lambda o: o and o["title"] == title and ready(o))
    assert out and out["title"] == title and ready(out), out
    return out


def _sp_tap(chrome, selector):
    assert chrome.eval(f"!!document.querySelector({json.dumps(selector)})"), selector
    chrome.eval(f"document.querySelector({json.dumps(selector)}).click()")


def _sp_until(check, what):
    assert _until(check, bool), f"timed out waiting for {what}"


@needs_chrome
def test_spotify_card_layout_per_size(chrome, server, fake):
    ids = _sp_setup(server)
    chrome.size(1024, 600)
    _show_screen(server, ids[(2, 2)])
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    full = _sp_card(chrome, "Music 2x2", lambda o: o["rows"] and o["speaker"] and o["coverImg"])
    assert full["h2"] == "Spotify" and "sp-full" in full["layout"] and "playing" in full["layout"]
    assert full["track"] == "Here Comes the Sun" and full["artist"] == "The Beatles"
    assert full["elapsed"] in ("1:05", "1:06", "1:07") and full["total"] == "3:05"
    assert full["transport"] == ["previous", "toggle", "next"] and full["toggle"] == "Pause"
    assert full["vol"] == "40%" and full["speaker"] == "Speaker not connected"
    assert [r[0] for r in full["rows"]] == [p["id"] for p in SP_SHOWN]
    assert full["rows"][0][1] == "Road trip"
    assert full["out"] == [] and full["small"] == []

    for (w, h), title in [((2, 1), "Music 2x1"), ((1, 2), "Music 1x2")]:
        _show_screen(server, ids[(w, h)])
        out = _sp_card(chrome, title, lambda o: o["track"] == "Here Comes the Sun")
        assert out["size"] == f"{w}x{h}" and out["artist"] == "The Beatles"
        assert out["transport"] == ["previous", "toggle", "next"] and out["coverImg"]
        assert out["rows"] == [] and out["vol"] is None and out["speaker"] is None
        assert out["elapsed"] is None
        assert out["out"] == [] and out["small"] == [], title

    _show_screen(server, ids[(1, 1)])
    tile = _sp_card(chrome, "Music 1x1", lambda o: o["track"] == "Here Comes the Sun")
    assert tile["toggleOnCover"] and tile["toggle"] == "Pause" and tile["coverImg"]
    assert tile["transport"] == [] and tile["rows"] == [] and tile["artist"] is None
    assert tile["out"] == [] and tile["small"] == []


@needs_chrome
def test_spotify_nothing_playing_and_not_linked(chrome, server, fake):
    ids = _sp_setup(server)
    fake.now = spotify._empty()
    chrome.size(1024, 600)
    _show_screen(server, ids[(2, 2)])
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    full = _sp_card(chrome, "Music 2x2", lambda o: o["rows"] and o["speaker"])
    assert full["track"] == "Nothing playing" and full["coverNone"] and not full["coverImg"]
    assert "idle" in full["layout"] and full["transport"] == ["here"] and full["vol"] is None
    assert len(full["rows"]) == len(SP_SHOWN)
    _sp_tap(chrome, '[data-playlist="pl-dinner"]')
    _sp_until(lambda: fake.sent("POST", "/api/spotify/play-playlist"), "the playlist call")
    assert fake.sent("POST", "/api/spotify/play-playlist") == [
        ("POST", "/api/spotify/play-playlist", "pl-dinner")
    ]

    fake.now = {"linked": False, "reason": "not linked"}
    for (w, h), screen in ids.items():
        _show_screen(server, screen)
        out = _sp_card(chrome, f"Music {w}x{h}", lambda o: o["text"] == "Spotify not linked")
        assert out["track"] is None and out["rows"] == [] and out["transport"] == []


@needs_chrome
def test_spotify_taps_send_controls(chrome, server, fake, tmp_path):
    ids = _sp_setup(server, [(2, 2)])
    shots = Path(os.environ.get("TV_SHOTS_DIR") or tmp_path)
    shots.mkdir(parents=True, exist_ok=True)
    chrome.size(1024, 600)
    _show_screen(server, ids[(2, 2)])
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    _sp_card(chrome, "Music 2x2", lambda o: o["rows"] and o["speaker"] and o["toggle"] == "Pause")
    chrome.wait(
        "[...document.querySelectorAll('.tv-card img')].every(i => i.complete && i.naturalWidth)"
    )
    png = chrome.send("Page.captureScreenshot", {"format": "png"})["data"]
    (shots / "tv_spotify_playing.png").write_bytes(base64.b64decode(png))

    _sp_tap(chrome, '[data-sp="toggle"]')
    _sp_until(lambda: fake.sent("POST", "/api/spotify/pause"), "the pause call")
    assert not fake.sent("POST", "/api/spotify/play")

    fake.now = _playing(playing=False)
    _sp_card(chrome, "Music 2x2", lambda o: o["toggle"] == "Play")
    _sp_tap(chrome, '[data-sp="toggle"]')
    _sp_until(lambda: fake.sent("POST", "/api/spotify/play"), "the play call")
    fake.now = _playing()

    _sp_tap(chrome, '[data-playlist="pl-kids"]')
    _sp_until(lambda: fake.sent("POST", "/api/spotify/play-playlist"), "the playlist call")
    assert fake.sent("POST", "/api/spotify/play-playlist")[0][2] == "pl-kids"

    _sp_tap(chrome, '[data-sp="vol-up"]')
    _sp_until(lambda: fake.sent("PUT", "/api/spotify/volume"), "the volume call")
    assert fake.sent("PUT", "/api/spotify/volume")[0][2] == {"volume_percent": 45}

    assert not fake.connected
    _sp_tap(chrome, '[data-sp="speaker"]')
    _sp_until(lambda: fake.sent("POST", "/api/sound/connect"), "the connect call")
    _sp_card(chrome, "Music 2x2", lambda o: o["speaker"] == "Speaker connected")
    assert not fake.sent("POST", "/api/sound/disconnect")
    _sp_tap(chrome, '[data-sp="speaker"]')
    _sp_until(lambda: fake.sent("POST", "/api/sound/disconnect"), "the disconnect call")

    fake.fail["next"] = "no active device"
    _sp_tap(chrome, '[data-sp="next"]')
    _sp_card(chrome, "Music 2x2", lambda o: o["msg"] == "no active device")
    chrome.wait("__test.waiting(3000) > 0")
    chrome.eval("__test.fire(3000)")
    _sp_card(chrome, "Music 2x2", lambda o: o["msg"] is None)


@needs_chrome
def test_spotify_polls_only_while_shown_and_visible(chrome, server, fake):
    ids = _sp_setup(server, [(2, 1)])
    brief = _screens(server)["Daily Brief"]
    idle = "__test.waiting(15000) === 0 && window.__inflight === 0"

    def polls():
        return len(fake.sent("GET", "/api/spotify/now"))

    chrome.size(1024, 600)
    _show_screen(server, ids[(2, 1)])
    chrome.send("Page.navigate", {"url": f"{server}/tv"})
    _sp_card(chrome, "Music 2x1", lambda o: o["track"] == "Here Comes the Sun")
    chrome.wait("__test.waiting(15000) > 0 && window.__inflight === 0")
    first = polls()
    chrome.eval("__test.fire(15000)")
    _sp_until(lambda: polls() > first, "the next poll")

    _show_screen(server, brief)
    chrome.wait("document.getElementById('title').textContent === 'Daily Brief'")
    chrome.wait(idle)
    stopped = polls()
    chrome.eval("__test.fire(15000)")
    chrome.wait("window.__inflight === 0")
    assert polls() == stopped

    _show_screen(server, ids[(2, 1)])
    _sp_card(chrome, "Music 2x1", lambda o: True)
    _sp_until(lambda: polls() > stopped, "polling to restart")
    chrome.eval(SP_HIDE % "hidden")
    chrome.wait(idle)
    hidden = polls()
    chrome.eval("__test.fire(15000)")
    chrome.wait("window.__inflight === 0")
    assert polls() == hidden
    chrome.eval(SP_HIDE % "visible")
    _sp_until(lambda: polls() > hidden, "polling to resume")
