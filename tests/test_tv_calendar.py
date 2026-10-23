import base64
import json
import re
import shutil
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_tv_lists import _Chrome, needs_chrome, server  # noqa: F401

from app.main import create_app

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
TV_JS = STATIC / "tv_calendar.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _node(body):
    script = f"const T = require({json.dumps(str(TV_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_tv_calendar_route_links_styles_through_static_url(client):
    r = client.get("/tv/calendar")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    for name in ("win95.css", "tv.css", "calendar_util.js", "tv_calendar.js"):
        version = int((STATIC / name).stat().st_mtime)
        url = f"/static/{name}?v={version}"
        assert url in r.text, name
        assert client.get(url).status_code == 200
    assert r.text.index("calendar_util.js") < r.text.index("tv_calendar.js")
    assert "/static/calendar.js" not in r.text


def test_tv_calendar_markup_has_ids_the_script_uses(client):
    page = client.get("/tv/calendar").text
    for name in ("cal-grid", "upcoming", "clock"):
        assert f'id="{name}"' in page, name
    assert "<h2>Coming up</h2>" in page
    js = TV_JS.read_text(encoding="utf-8")
    ids = set(re.findall(r'getElementById\("([\w-]+)"\)', js))
    assert {"cal-grid", "upcoming", "clock", "month-count", "today"} <= ids
    for name in ids:
        assert f'id="{name}"' in page, name


def test_tv_calendar_js_listens_for_changes():
    js = TV_JS.read_text(encoding="utf-8")
    assert 'addEventListener("events_changed"' in js
    assert '"/api/events' in js


def test_grid_days_defined_once_across_static():
    hits = [
        p.name
        for p in STATIC.glob("*.js")
        if re.search(r"function gridDays\b", p.read_text(encoding="utf-8"))
    ]
    assert hits == ["calendar_util.js"]


@needs_node
def test_upcoming_labels_and_order():
    result = _node(
        "const today = '2026-10-23';"
        "const occ = ["
        "{id: 1, title: 'Plumber', date: '2026-10-26', time: '09:00'},"
        "{id: 2, title: 'Dinner', date: '2026-10-24', time: '18:30'},"
        "{id: 3, title: 'Trash day', date: '2026-10-24', time: null},"
        "{id: 4, title: 'Vet', date: '2026-10-23', time: '15:00'},"
        "{id: 5, title: 'Bday', date: '2026-10-26', time: null},"
        "{id: 6, title: 'Past', date: '2026-10-22', time: null},"
        "{id: 7, title: 'Later', date: '2026-10-30', time: null}];"
        "console.log(JSON.stringify({same: globalThis.TvCalendar === T,"
        " up: T.upcoming(occ, today).map(u => [u.title, u.when]),"
        " none: T.upcoming([], today)}))"
    )
    assert result["same"] is True
    assert result["up"] == [
        ["Vet", "Today, 3:00 pm"],
        ["Trash day", "Tomorrow, all day"],
        ["Dinner", "Tomorrow, 6:30 pm"],
        ["Bday", "Mon Oct 26"],
        ["Plumber", "Mon Oct 26, 9:00 am"],
    ]
    assert result["none"] == []


@needs_node
def test_upcoming_caps_at_ten_and_chip_text():
    result = _node(
        "const occ = [];"
        "for (let i = 0; i < 14; i++) occ.push({id: i, title: 'E' + i, date: '2026-10-25', time: null});"
        "console.log(JSON.stringify({n: T.upcoming(occ, '2026-10-23').length,"
        " chips: [T.chipText({title: 'Vet', time: '15:00'}), T.chipText({title: 'Dinner', time: '18:30'}),"
        " T.chipText({title: 'Plumber', time: '09:00'}), T.chipText({title: 'Trash', time: null})]}))"
    )
    assert result["n"] == 10
    assert result["chips"] == ["Vet 3pm", "Dinner 6:30", "Plumber 9am", "Trash"]


@needs_node
def test_fetch_range_covers_grid_and_next_week():
    result = _node(
        "const C = require(" + json.dumps(str(STATIC / "calendar_util.js")) + ");"
        "const a = T.fetchRange(C.gridDays(2026, 10), '2026-10-23');"
        "const b = T.fetchRange(C.gridDays(2026, 10), '2026-10-30');"
        "const c = T.fetchRange(C.gridDays(2026, 8), '2026-08-31');"
        "const ms = T.msToMidnight(new Date(2026, 9, 23, 23, 59, 0));"
        "console.log(JSON.stringify([a, b, c, ms]))"
    )
    assert result[0] == {"start": "2026-09-27", "end": "2026-10-31"}
    assert result[1] == {"start": "2026-09-27", "end": "2026-11-05"}
    assert result[2] == {"start": "2026-07-26", "end": "2026-09-06"}
    assert result[3] == 65000


def _seed(base, extra=0):
    today = date.today()
    with httpx.Client(base_url=base) as c:
        events = [
            ("Vet", today, "15:00", "never"),
            ("Trash day", today + timedelta(days=1), None, "weekly"),
            ("Dinner", today + timedelta(days=1), "18:30", "never"),
            ("Plumber", today + timedelta(days=4), "09:00", "never"),
        ]
        events += [(f"Extra {i}", today, None, "never") for i in range(extra)]
        for title, day, time_, repeat in events:
            r = c.post(
                "/api/events",
                json={"title": title, "date": day.isoformat(), "time": time_, "repeat": repeat},
            )
            assert r.status_code == 201, r.text
    return today


LAYOUT = """JSON.stringify({
  grid: (() => { const r = document.getElementById('cal-grid').getBoundingClientRect();
    return {left: r.left, right: r.right, top: r.top, bottom: r.bottom}; })(),
  up: (() => { const r = document.querySelector('.tv-up').getBoundingClientRect();
    return {left: r.left, right: r.right}; })(),
  heads: [...document.querySelectorAll('#cal-grid .h')].map(h => h.textContent),
  cells: [...document.querySelectorAll('#cal-grid .d')].map(d => {
    const r = d.getBoundingClientRect();
    return {date: d.dataset.date, cls: d.className, h: r.height, bottom: r.bottom,
      left: r.left, top: r.top,
      chips: [...d.querySelectorAll('.ev')].map(e => e.textContent),
      fits: d.querySelector('.chips').scrollHeight <= d.querySelector('.chips').clientHeight};
  }),
  upcoming: [...document.querySelectorAll('#upcoming li')].map(li => li.textContent),
  count: document.getElementById('month-count').textContent,
  title: document.getElementById('month-title').textContent,
  clock: document.getElementById('clock').textContent,
  scrollW: document.documentElement.scrollWidth,
  scrollH: document.documentElement.scrollHeight,
  w: innerWidth,
  h: innerHeight,
})"""


def _shoot(server, tmp_path, width, height):  # noqa: F811
    chrome = _Chrome(tmp_path / "chrome", width, height)
    try:
        chrome.send("Page.navigate", {"url": f"{server}/tv/calendar"})
        layout = None
        for _ in range(100):
            layout = json.loads(chrome.eval(LAYOUT) or "null")
            if layout and layout["upcoming"] and "Nothing" not in layout["upcoming"][0]:
                break
            time.sleep(0.05)
        png = base64.b64decode(chrome.send("Page.captureScreenshot", {"format": "png"})["data"])
    finally:
        chrome.close()
    shot = tmp_path / f"tv-calendar-{width}x{height}.png"
    shot.write_bytes(png)
    return layout, Image.open(shot).convert("RGB")


@needs_chrome
@pytest.mark.parametrize("width,height", [(1920, 1080), (1280, 800)])
def test_headless_screenshot_shows_month_and_coming_up(server, tmp_path, width, height):  # noqa: F811
    today = _seed(server)
    layout, img = _shoot(server, tmp_path, width, height)

    assert layout["heads"] == ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
    assert layout["title"] == today.strftime("%B %Y")
    assert re.fullmatch(r"\d{1,2}:\d\d [ap]m", layout["clock"])
    assert layout["scrollW"] <= layout["w"] and layout["scrollH"] <= layout["h"]

    grid, up = layout["grid"], layout["up"]
    assert grid["right"] < up["left"]
    assert 0.55 < (grid["right"] - grid["left"]) / width < 0.7
    assert up["right"] < width
    cells = layout["cells"]
    assert len(cells) % 7 == 0 and 28 <= len(cells) <= 42
    assert len({round(c["h"]) for c in cells}) == 1
    assert cells[-1]["bottom"] > height * 0.85

    by_date = {c["date"]: c for c in cells}
    t = today.isoformat()
    assert "today" in by_date[t]["cls"]
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

    up_list = layout["upcoming"]
    assert up_list[:3] == [
        "VetToday, 3:00 pm",
        "Trash dayTomorrow, all day",
        "DinnerTomorrow, 6:30 pm",
    ]
    assert up_list[3].startswith("Plumber") and up_list[3].endswith(", 9:00 am")
    in_month = sum(len(c["chips"]) for c in cells if "other" not in c["cls"])
    assert layout["count"] == f"{in_month} events this month"

    teal = (0, 128, 128)
    for y in range(0, height, max(1, height // 50)):
        assert img.getpixel((width - 2, y)) == teal, ("right edge", y)
    navy = (0, 0, 128)
    cell = by_date[t]
    assert img.getpixel((int(cell["left"]) + 1, int(cell["top"] + cell["h"] / 2))) == navy
    other = next(c for c in cells if c["date"] != t and not c["chips"])
    assert img.getpixel((int(other["left"]) + 1, int(other["top"] + other["h"] / 2))) != navy


@needs_chrome
def test_too_many_chips_are_cut_with_plus_n(server, tmp_path):  # noqa: F811
    today = _seed(server, extra=12)
    layout, _ = _shoot(server, tmp_path, 1280, 800)
    cell = next(c for c in layout["cells"] if c["date"] == today.isoformat())
    assert cell["fits"]
    more = cell["chips"][-1]
    assert re.fullmatch(r"\+\d+", more)
    assert len(cell["chips"]) - 1 + int(more[1:]) == 13
