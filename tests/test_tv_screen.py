import io
import json
import re
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from tests.test_tv_lists import _Chrome, needs_chrome, needs_node, server  # noqa: F401

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


def test_tv_markup_has_ids_the_script_uses(client):
    page = client.get("/tv").text
    js = SCREEN_JS.read_text(encoding="utf-8")
    ids = set(re.findall(r'getElementById\("([\w-]+)"\)', js))
    assert {"screen", "title", "clock", "today"} <= ids
    for name in ids:
        assert f'id="{name}"' in page, name


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
    assert result["needsA"] == ["events"]
    assert result["needsB"] == []
    assert result["needsNull"] == []
    assert result["kinds"] == ["calendar", "clock", "lists", "weather"]
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
    return {s["name"]: s for s in httpx.get(f"{base}/api/screens").json()}


def _seed_events(base):
    today = date.today()
    with httpx.Client(base_url=base) as c:
        c.post("/api/events", json={"title": "Dentist", "date": today.isoformat(), "time": "09:00"})
        later = (today + timedelta(days=2)).isoformat()
        c.post("/api/events", json={"title": "Bins out", "date": later})


CARDS = """JSON.stringify({
  title: document.getElementById('title').textContent,
  nav: performance.getEntriesByType('navigation').length,
  mark: window.__mark || null,
  cards: [...document.querySelectorAll('#screen .tv-card')].map(c => {
    const r = c.getBoundingClientRect();
    return {
      kind: c.getAttribute('data-kind'),
      h2: c.querySelector('h2') ? c.querySelector('h2').textContent : null,
      text: c.innerText,
      col: c.style.gridColumn,
      row: c.style.gridRow,
      grid: !!c.querySelector('.tv-cal-grid'),
      heads: c.querySelectorAll('.tv-cal-grid .h').length,
      top: r.top, left: r.left, bottom: r.bottom, right: r.right,
    };
  }),
  scrollW: document.documentElement.scrollWidth,
  scrollH: document.documentElement.scrollHeight,
})"""


def _wait(chrome, ready):
    out = None
    for _ in range(100):
        out = json.loads(chrome.eval(CARDS) or "null")
        if out and ready(out):
            return out
        time.sleep(0.05)
    return out


@needs_chrome
def test_daily_brief_renders_four_cards_in_grid(server, tmp_path):  # noqa: F811
    _seed_events(server)
    brief = _screens(server)["Daily Brief"]
    httpx.put(f"{server}/api/settings", json={"channel": "screen", "screen_id": brief["id"]})
    chrome = _Chrome(tmp_path / "chrome", 1920, 1080)
    try:
        chrome.send("Page.navigate", {"url": f"{server}/tv"})
        out = _wait(chrome, lambda o: o["title"] == "Daily Brief" and len(o["cards"]) == 4)
    finally:
        chrome.close()
    assert out["title"] == "Daily Brief", out
    cards = out["cards"]
    assert [c["kind"] for c in cards] == ["clock", "weather", "calendar", "lists"]
    assert [(c["col"], c["row"]) for c in cards] == [
        ("1 / span 1", "1 / span 1"),
        ("2 / span 1", "1 / span 1"),
        ("1 / span 1", "2 / span 1"),
        ("2 / span 1", "2 / span 1"),
    ]
    clock, weather, cal, lists = cards
    assert clock["h2"] == "Clock"
    assert date.today().strftime("%A") in clock["text"]
    assert cal["h2"] == "Today" and cal["grid"] is False
    assert "Dentist" in cal["text"] and "9:00 am" in cal["text"]
    assert "Coming up" in cal["text"] and "Bins out" in cal["text"]
    assert "Coming soon" in weather["text"] and "Coming soon" in lists["text"]
    assert clock["top"] == weather["top"] and clock["left"] < weather["left"]
    assert cal["top"] > clock["bottom"] and cal["left"] == clock["left"]
    assert max(c["bottom"] for c in cards) < 1080
    assert out["scrollW"] <= 1920 and out["scrollH"] <= 1080


@needs_chrome
def test_calendar_screen_shows_month_grid_and_switches_without_reload(server, tmp_path):  # noqa: F811
    _seed_events(server)
    screens = _screens(server)
    httpx.put(
        f"{server}/api/settings", json={"channel": "screen", "screen_id": screens["Calendar"]["id"]}
    )
    chrome = _Chrome(tmp_path / "chrome", 1920, 1080)
    try:
        chrome.send("Page.navigate", {"url": f"{server}/tv"})
        cal = _wait(chrome, lambda o: o["title"] == "Calendar" and o["cards"])
        chrome.eval("window.__mark = 'kept'")
        httpx.put(f"{server}/api/settings", json={"screen_id": screens["Daily Brief"]["id"]})
        brief = _wait(chrome, lambda o: o["title"] == "Daily Brief")
    finally:
        chrome.close()
    assert len(cal["cards"]) == 1, cal
    card = cal["cards"][0]
    assert card["kind"] == "calendar"
    assert (card["col"], card["row"]) == ("1 / span 2", "1 / span 2")
    assert card["grid"] is True and card["heads"] == 7
    assert "Coming up" in card["text"] and "Bins out" in card["text"]
    assert card["h2"] == date.today().strftime("%B %Y")
    assert brief["title"] == "Daily Brief"
    assert brief["mark"] == "kept" and brief["nav"] == 1
    assert len(brief["cards"]) == 4


@needs_chrome
def test_empty_screen_shows_hint(server, tmp_path):  # noqa: F811
    screen = httpx.post(f"{server}/api/screens", json={"name": "Spare"}).json()
    httpx.put(f"{server}/api/settings", json={"channel": "screen", "screen_id": screen["id"]})
    chrome = _Chrome(tmp_path / "chrome", 1920, 1080)
    try:
        chrome.send("Page.navigate", {"url": f"{server}/tv"})
        out = _wait(chrome, lambda o: o["title"] == "Spare" and o["cards"])
    finally:
        chrome.close()
    assert len(out["cards"]) == 1
    assert out["cards"][0]["text"] == "Nothing on this screen yet. Add widgets from your phone."


SWIPE = """(() => {
  const fire = (type, x) => {
    const t = new Touch({identifier: 1, target: document.body, clientX: x, clientY: 300});
    document.dispatchEvent(new TouchEvent(type, {changedTouches: [t], touches: type === 'touchstart' ? [t] : [], bubbles: true}));
  };
  fire('touchstart', %d);
  fire('touchend', %d);
  return true;
})()"""


def _settings_until(base, ready):
    s = None
    for _ in range(100):
        s = httpx.get(f"{base}/api/settings").json()
        if ready(s):
            return s
        time.sleep(0.05)
    return s


def _photo(base):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 30, 30)).save(buf, "JPEG")
    files = [("files", ("a.jpg", buf.getvalue(), "image/jpeg"))]
    assert httpx.post(f"{base}/api/photos", files=files).status_code < 300


@needs_chrome
def test_kiosk_swipe_walks_from_photos_to_first_screen_and_back(server, tmp_path):  # noqa: F811
    _photo(server)
    first = min(httpx.get(f"{server}/api/screens").json(), key=lambda s: s["sort_order"])
    chrome = _Chrome(tmp_path / "chrome", 1920, 1080)
    try:
        chrome.send("Page.navigate", {"url": f"{server}/"})
        for _ in range(100):
            if chrome.eval("document.querySelector('#layer-a.front, #layer-b.front') !== null"):
                break
            time.sleep(0.05)
        chrome.eval(SWIPE % (900, 600))
        left = _settings_until(server, lambda s: s["channel"] == "screen")
        frame = None
        for _ in range(100):
            frame = chrome.eval("document.getElementById('channel').getAttribute('src')")
            if frame == "/tv":
                break
            time.sleep(0.05)
        chrome.eval(SWIPE % (600, 900))
        right = _settings_until(server, lambda s: s["channel"] == "photos")
    finally:
        chrome.close()
    assert (left["channel"], left["screen_id"]) == ("screen", first["id"])
    assert frame == "/tv"
    assert right["channel"] == "photos"
