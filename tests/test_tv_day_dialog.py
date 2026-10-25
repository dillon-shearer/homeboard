import json
import subprocess
from pathlib import Path

from tests.chrome import needs_node

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
