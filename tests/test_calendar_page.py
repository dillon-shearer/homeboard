import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
CALENDAR_JS = STATIC / "calendar.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _node(body):
    script = f"const C = require({json.dumps(str(CALENDAR_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_calendar_page_returns_200_with_styles(client):
    r = client.get("/admin/calendar")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "/static/win95.css?v=" in r.text
    assert "/static/calendar.css?v=" in r.text
    assert "/static/calendar.js?v=" in r.text
    assert '<a class="w95-back" href="/admin">Start</a>' in r.text
    assert '<span class="w95-title-text">Calendar</span>' in r.text


def test_calendar_page_has_month_controls_and_dialog(client):
    page = client.get("/admin/calendar").text
    for name in ("month-select", "year-select", "cal-grid", "event-dialog"):
        assert f'id="{name}"' in page, name
    assert re.search(r'<input id="event-date"[^>]*type="date"', page)
    assert re.search(r'<input id="event-time"[^>]*type="time"', page)
    assert re.search(r'<input id="event-title"[^>]*maxlength="120"', page)
    assert '<input id="event-allday" type="checkbox">' in page
    for value, label in (
        ("never", "Never"),
        ("weekly", "Every week"),
        ("monthly", "Every month"),
        ("yearly", "Every year"),
    ):
        assert f'<option value="{value}">{label}</option>' in page
    for label in ("Delete", "Save", "Cancel", "Add Event", "Today"):
        assert f">{label}</button>" in page
    assert ">Nothing on this day.<" in page


def test_calendar_page_has_ids_calendar_js_uses(client):
    page = client.get("/admin/calendar").text
    ids = set(re.findall(r'\$\("([\w-]+)"\)', CALENDAR_JS.read_text()))
    assert {"cal-grid", "event-dialog", "toast", "toast-text"} <= ids
    for name in ids:
        assert f'id="{name}"' in page, name


def test_start_calendar_icon_links(client):
    page = client.get("/admin").text
    icon = re.search(r'<a class="w95-icon" href="/admin/calendar">.*?</a>', page, re.DOTALL)
    assert icon
    assert '<span class="w95-icon-label">Calendar</span>' in icon.group(0)
    assert "Coming soon" not in page


def test_calendar_js_listens_for_changes_and_uses_no_browser_dialogs():
    js = CALENDAR_JS.read_text()
    assert 'addEventListener("events_changed"' in js
    assert '"/api/events"' in js
    for banned in ("prompt(", "confirm(", "alert("):
        assert banned not in js


@needs_node
def test_loads_under_node_and_sets_window_calendar():
    result = _node(
        "console.log(JSON.stringify({same: globalThis.Calendar === C,"
        " fns: Object.keys(C).filter(k => typeof C[k] === 'function')}))"
    )
    assert result["same"] is True
    assert {"gridDays", "formatTime", "dayTitle", "shiftMonth"} <= set(result["fns"])


@needs_node
def test_grid_for_october_2026():
    result = _node(
        "const g = C.gridDays(2026, 10);"
        "console.log(JSON.stringify({n: g.length, first: g[0], last: g[g.length - 1],"
        " other: g.filter(c => c.other).map(c => c.date)}))"
    )
    assert result["n"] == 35
    assert result["first"] == {"date": "2026-09-27", "day": 27, "other": True}
    assert result["last"]["date"] == "2026-10-31"
    assert result["other"] == ["2026-09-27", "2026-09-28", "2026-09-29", "2026-09-30"]


@needs_node
def test_grid_is_whole_weeks_and_at_most_42_days():
    result = _node(
        "const out = [];"
        "for (let y = 2025; y <= 2029; y++) for (let m = 1; m <= 12; m++) {"
        " const g = C.gridDays(y, m);"
        " out.push([g.length, new Date(g[0].date + 'T00:00:00Z').getUTCDay(),"
        " g.filter(c => !c.other).length, new Date(Date.UTC(y, m, 0)).getUTCDate()]); }"
        "console.log(JSON.stringify(out))"
    )
    for n, weekday, inside, days in result:
        assert n % 7 == 0 and 28 <= n <= 42
        assert weekday == 0
        assert inside == days


@needs_node
def test_format_time():
    result = _node(
        "console.log(JSON.stringify(['18:30', '00:05', '12:00', '09:07', null]"
        ".map(C.formatTime)))"
    )
    assert result == ["6:30 pm", "12:05 am", "12:00 pm", "9:07 am", "All day"]


@needs_node
def test_day_title_and_month_shift():
    result = _node(
        "console.log(JSON.stringify([C.dayTitle('2026-10-24'), C.shiftMonth(2026, 1, -1),"
        " C.shiftMonth(2026, 12, 1), C.clampDay(2026, 2, 31)]))"
    )
    assert result == [
        "Saturday, October 24",
        {"year": 2025, "month": 12},
        {"year": 2027, "month": 1},
        "2026-02-28",
    ]
