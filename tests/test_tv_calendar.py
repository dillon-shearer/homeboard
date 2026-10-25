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


@needs_node
def test_day_label_next_event_and_week_days():
    result = _node(
        "const occ = ["
        "{title: 'Dinner', date: '2026-10-25', time: '18:30'},"
        "{title: 'Trash', date: '2026-10-25', time: null},"
        "{title: 'Trip', date: '2026-10-31', time: null},"
        "{title: 'Far', date: '2026-11-01', time: null}];"
        "console.log(JSON.stringify({label: T.dayLabel('2026-10-24'),"
        " next: T.nextEvent(occ, '2026-10-24'), none: T.nextEvent(occ.slice(3), '2026-10-24'),"
        " week: T.weekDays(occ, '2026-10-24')}))"
    )
    assert result["label"] == "Sat, Oct 24"
    assert result["next"] == {"title": "Trash", "when": "Tomorrow, all day"}
    assert result["none"] is None
    week = result["week"]
    assert [w["date"] for w in week] == [f"2026-10-{d}" for d in range(24, 31)]
    assert [w["name"] for w in week] == ["Sat", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]
    assert [w["day"] for w in week] == list(range(24, 31))
    assert [w["today"] for w in week] == [True] + [False] * 6
    assert week[1]["chips"] == ["Trash", "Dinner 6:30"]
    assert all(not w["chips"] for w in week if w["date"] != "2026-10-25")


CARD = """JSON.stringify((() => {
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


def _calendar_screens(base):
    out = {}
    with httpx.Client(base_url=base) as c:
        for w, h in ((1, 1), (2, 1), (1, 2), (2, 2)):
            screen = c.post("/api/screens", json={"name": f"Cal {w}x{h}"}).json()
            r = c.post(
                f"/api/screens/{screen['id']}/widgets",
                json={"kind": "calendar", "col": 0, "row": 0, "w": w, "h": h},
            )
            assert r.status_code == 201, r.text
            out[f"{w}x{h}"] = screen
    return out


def _card(base, chrome, screen):
    httpx.put(f"{base}/api/settings", json={"channel": "screen", "screen_id": screen["id"]})
    chrome.send("Page.navigate", {"url": f"{base}/tv"})
    card = None
    for _ in range(100):
        card = json.loads(chrome.eval(CARD) or "null")
        if card and card["title"] == screen["name"]:
            break
        time.sleep(0.05)
    time.sleep(0.2)
    return json.loads(chrome.eval(CARD))


def _event(base, title, day, time_=None):
    r = httpx.post(
        f"{base}/api/events",
        json={"title": title, "date": day.isoformat(), "time": time_, "repeat": "never"},
    )
    assert r.status_code == 201, r.text


@needs_chrome
def test_calendar_content_per_size(server, tmp_path):  # noqa: F811
    today = date.today()
    screens = _calendar_screens(server)
    label = f"{today:%a}, {today:%b} {today.day}"
    _event(server, "Dentist", today + timedelta(days=2), "09:00")
    _event(server, "Far trip", today + timedelta(days=9))
    chrome = _Chrome(tmp_path / "chrome", 1024, 600)
    try:
        empty_one = _card(server, chrome, screens["1x1"])
        _event(server, "Vet", today, "15:00")
        _event(server, "Bins", today)
        one = _card(server, chrome, screens["1x1"])
        wide = _card(server, chrome, screens["2x1"])
        tall = _card(server, chrome, screens["1x2"])
        full = _card(server, chrome, screens["2x2"])
    finally:
        chrome.close()
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
