import json
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

from tests.chrome import _Chrome, needs_chrome, needs_node, server  # noqa: F401

SCREEN_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "tv_screen.js"
WIDTH, HEIGHT = 1024, 600


@needs_node
def test_day_events_sorted_all_day_first_then_time():
    script = (
        f"const T = require({json.dumps(str(SCREEN_JS))});\n"
        "const occ = ["
        " {title: 'Dinner', date: '2026-10-24', time: '18:30'},"
        " {title: 'Vet', date: '2026-10-24', time: '09:00'},"
        " {title: 'Other day', date: '2026-10-25', time: '08:00'},"
        " {title: 'Trash day', date: '2026-10-24', time: null}];\n"
        "console.log(JSON.stringify({day: T.dayEvents(occ, '2026-10-24'),"
        " none: T.dayEvents(occ, '2026-10-26'), long: T.longDate('2026-10-24')}))"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    result = json.loads(out.stdout)
    assert result["day"] == [
        {"title": "Trash day", "time": "all day"},
        {"title": "Vet", "time": "9:00 am"},
        {"title": "Dinner", "time": "6:30 pm"},
    ]
    assert result["none"] == []
    assert result["long"] == "Saturday, October 24, 2026"


CELL = """JSON.stringify((() => {
  const d = document.querySelector('.tv-cal-grid .d[data-date="%s"]');
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


def _until(fn, ready, tries=100):
    v = None
    for _ in range(tries):
        v = fn()
        if ready(v):
            return v
        time.sleep(0.05)
    return v


def _tap(chrome, x, y):
    chrome.send(
        "Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]}
    )
    chrome.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})


@needs_chrome
def test_tapping_a_month_day_opens_a_dialog(server, tmp_path):  # noqa: F811
    today = date.today()
    with httpx.Client(base_url=server) as c:
        for title, time_ in (("Dentist visit", "15:00"), ("Pick up cake", None)):
            r = c.post(
                "/api/events",
                json={"title": title, "date": today.isoformat(), "time": time_, "repeat": "never"},
            )
            assert r.status_code == 201, r.text
        screens = {s["name"]: s for s in c.get("/api/screens").json()}
        cal = screens["Calendar"]
        assert any(w["kind"] == "calendar" and w["w"] == 2 and w["h"] == 2 for w in cal["widgets"])
        c.put("/api/settings", json={"channel": "screen", "screen_id": cal["id"]})
    cell = CELL % today.isoformat()
    dialog = lambda: json.loads(chrome.eval(DIALOG) or "null")
    chrome = _Chrome(tmp_path / "chrome", WIDTH, HEIGHT)
    try:
        chrome.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
        chrome.send("Page.navigate", {"url": f"{server}/tv"})
        at = _until(lambda: json.loads(chrome.eval(cell) or "null"), bool)
        assert dialog() is None
        _tap(chrome, at["x"], at["y"])
        opened = _until(dialog, bool)
        time.sleep(0.3)
        _tap(chrome, opened["x"], opened["y"])
        after_close = _until(dialog, lambda v: v is None)
        time.sleep(0.3)
        _tap(chrome, at["x"], at["y"])
        reopened = _until(dialog, bool)
        time.sleep(0.3)
        _tap(chrome, 40, HEIGHT / 2)
        after_backdrop = _until(dialog, lambda v: v is None)
        time.sleep(0.3)
        _tap(chrome, at["x"], at["y"])
        third = _until(dialog, bool)
        httpx.put(f"{server}/api/settings", json={"screen_id": screens["Notes"]["id"]})
        after_screen = _until(dialog, lambda v: v is None)
    finally:
        chrome.close()
    long = f"{today:%A}, {today:%B} {today.day}, {today.year}"
    assert opened, "dialog did not open"
    assert "Dentist visit" in opened["text"] and "Pick up cake" in opened["text"]
    assert long in opened["text"]
    assert opened["text"].index("Pick up cake") < opened["text"].index("Dentist visit")
    assert opened["w"] >= 48 and opened["h"] >= 48
    assert opened["font"] >= 18
    assert 0.5 * WIDTH <= opened["width"] <= 0.7 * WIDTH
    assert after_close is None
    assert reopened and after_backdrop is None
    assert third and after_screen is None


WEEK_CELL = """JSON.stringify((() => {
  const d = document.querySelector('.tv-week .d[data-date="%s"]');
  if (!d) return null;
  const r = d.getBoundingClientRect();
  return {x: r.left + r.width / 2, y: r.top + r.height / 2};
})())"""


@needs_chrome
def test_tapping_a_week_strip_day_opens_a_dialog(server, tmp_path):  # noqa: F811
    day = date.today() + timedelta(days=3)
    with httpx.Client(base_url=server) as c:
        r = c.post(
            "/api/events",
            json={"title": "Piano lesson", "date": day.isoformat(), "time": "16:00"},
        )
        assert r.status_code == 201, r.text
        screen = c.post("/api/screens", json={"name": "Week"}).json()
        r = c.post(
            f"/api/screens/{screen['id']}/widgets",
            json={"kind": "calendar", "col": 0, "row": 0, "w": 2, "h": 1},
        )
        assert r.status_code == 201, r.text
        c.put("/api/settings", json={"channel": "screen", "screen_id": screen["id"]})
    dialog = lambda: json.loads(chrome.eval(DIALOG) or "null")
    chrome = _Chrome(tmp_path / "chrome", WIDTH, HEIGHT)
    try:
        chrome.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
        chrome.send("Page.navigate", {"url": f"{server}/tv"})
        at = _until(lambda: json.loads(chrome.eval(WEEK_CELL % day.isoformat()) or "null"), bool)
        assert at and dialog() is None
        _tap(chrome, at["x"], at["y"])
        opened = _until(dialog, bool)
    finally:
        chrome.close()
    assert opened, "dialog did not open"
    assert f"{day:%A}, {day:%B} {day.day}, {day.year}" in opened["text"]
    assert "Piano lesson" in opened["text"] and "4:00 pm" in opened["text"]
