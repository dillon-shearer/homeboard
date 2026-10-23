import json
import re
import subprocess
import time
from datetime import date, timedelta
from pathlib import Path

import httpx

from tests.chrome import _Chrome, needs_chrome, needs_node, server  # noqa: F401

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
SCREEN_JS = STATIC / "tv_screen.js"


def _node(body):
    script = f"const T = require({json.dumps(str(SCREEN_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


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
        "console.log(JSON.stringify({up: T.upcoming(occ, today).map(u => [u.title, u.when]),"
        " none: T.upcoming([], today)}))"
    )
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


def _seed(base, extra=0):
    today = date.today()
    with httpx.Client(base_url=base) as c:
        events = [
            ("Vet", today, "15:00", "never"),
            ("Trash day", today + timedelta(days=1), None, "weekly"),
            ("Dinner", today + timedelta(days=1), "18:30", "never"),
        ]
        events += [(f"Extra {i}", today, None, "never") for i in range(extra)]
        for title, day, time_, repeat in events:
            r = c.post(
                "/api/events",
                json={"title": title, "date": day.isoformat(), "time": time_, "repeat": repeat},
            )
            assert r.status_code == 201, r.text
        cal = next(s for s in c.get("/api/screens").json() if s["name"] == "Calendar")
        c.put("/api/settings", json={"channel": "screen", "screen_id": cal["id"]})
    return today


CELLS = """JSON.stringify([...document.querySelectorAll('.tv-cal-grid .d')].map(d => ({
  date: d.dataset.date, cls: d.className,
  chips: [...d.querySelectorAll('.ev')].map(e => e.textContent),
  fits: d.querySelector('.chips').scrollHeight <= d.querySelector('.chips').clientHeight})))"""


def _cells(server, tmp_path, width, height):  # noqa: F811
    chrome = _Chrome(tmp_path / "chrome", width, height)
    try:
        chrome.send("Page.navigate", {"url": f"{server}/tv"})
        cells = []
        for _ in range(100):
            cells = json.loads(chrome.eval(CELLS) or "[]")
            if any(c["chips"] for c in cells):
                break
            time.sleep(0.05)
    finally:
        chrome.close()
    return cells


@needs_chrome
def test_month_widget_marks_today_and_places_repeats(server, tmp_path):  # noqa: F811
    today = _seed(server)
    cells = _cells(server, tmp_path, 1920, 1080)
    assert len(cells) % 7 == 0 and 28 <= len(cells) <= 42
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
def test_too_many_chips_are_cut_with_plus_n(server, tmp_path):  # noqa: F811
    today = _seed(server, extra=12)
    cells = _cells(server, tmp_path, 1280, 800)
    cell = next(c for c in cells if c["date"] == today.isoformat())
    assert cell["fits"]
    more = cell["chips"][-1]
    assert re.fullmatch(r"\+\d+", more)
    assert len(cell["chips"]) - 1 + int(more[1:]) == 13
