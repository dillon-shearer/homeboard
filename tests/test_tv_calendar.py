import json
import re
import subprocess
from pathlib import Path

from tests.chrome import needs_node

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
