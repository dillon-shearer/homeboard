import json
import subprocess
import time
from pathlib import Path

import httpx

from tests.chrome import _Chrome, needs_chrome, needs_node, server  # noqa: F401

SCREEN_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "tv_screen.js"
SPOT_TEXT = "Empty spot. Add a widget on your phone: TV, Edit, then the screen name."

CARDS = """JSON.stringify({
  title: document.getElementById('title').textContent,
  cards: [...document.querySelectorAll('#screen .tv-card')].map(c => {
    const r = c.getBoundingClientRect();
    return {
      kind: c.getAttribute('data-kind'),
      spot: c.classList.contains('spot'),
      text: c.innerText,
      col: c.style.gridColumn,
      row: c.style.gridRow,
      top: r.top, left: r.left, bottom: r.bottom, right: r.right,
    };
  }),
})"""


def _node(body):
    script = f"const T = require({json.dumps(str(SCREEN_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


def _show(base, tmp_path, name, widgets, ready):
    screen = httpx.post(f"{base}/api/screens", json={"name": name}).json()
    for body in widgets:
        r = httpx.post(f"{base}/api/screens/{screen['id']}/widgets", json=body)
        assert r.status_code == 201, r.text
    httpx.put(f"{base}/api/settings", json={"channel": "screen", "screen_id": screen["id"]})
    chrome = _Chrome(tmp_path / "chrome", 1920, 1080)
    try:
        chrome.send("Page.navigate", {"url": f"{base}/tv"})
        out = None
        for _ in range(100):
            out = json.loads(chrome.eval(CARDS) or "null")
            if out and out["title"] == name and ready(out["cards"]):
                break
            time.sleep(0.05)
    finally:
        chrome.close()
    return out


@needs_node
def test_free_cells_lists_only_uncovered_cells():
    result = _node(
        "console.log(JSON.stringify({"
        " wide: T.freeCells([{col: 0, row: 0, w: 2, h: 1}]),"
        " tall: T.freeCells([{col: 0, row: 0, w: 1, h: 2}, {col: 1, row: 0, w: 1, h: 1}]),"
        " full: T.freeCells([{col: 0, row: 0, w: 2, h: 2}]),"
        " four: T.freeCells([{col: 0, row: 0, w: 1, h: 1}, {col: 1, row: 0, w: 1, h: 1},"
        " {col: 0, row: 1, w: 1, h: 1}, {col: 1, row: 1, w: 1, h: 1}])}))"
    )
    assert result["wide"] == [
        {"col": 0, "row": 1, "w": 1, "h": 1},
        {"col": 1, "row": 1, "w": 1, "h": 1},
    ]
    assert result["tall"] == [{"col": 1, "row": 1, "w": 1, "h": 1}]
    assert result["full"] == []
    assert result["four"] == []


@needs_chrome
def test_wide_clock_screen_fills_bottom_row_with_spots(server, tmp_path):  # noqa: F811
    out = _show(
        server, tmp_path, "Wide", [{"kind": "clock", "w": 2, "h": 1}], lambda c: len(c) == 3
    )
    cards = out["cards"]
    clock = [c for c in cards if c["kind"] == "clock"]
    spots = [c for c in cards if c["spot"]]
    assert len(clock) == 1 and len(spots) == 2, cards
    assert [(s["col"], s["row"]) for s in spots] == [
        ("1 / span 1", "2 / span 1"),
        ("2 / span 1", "2 / span 1"),
    ]
    for s in spots:
        assert s["text"] == SPOT_TEXT
        assert s["top"] > clock[0]["bottom"]
        assert s["bottom"] < 1080
    assert spots[0]["right"] < spots[1]["left"]


@needs_chrome
def test_tall_calendar_and_one_small_widget_fill_last_quarter(server, tmp_path):  # noqa: F811
    out = _show(
        server,
        tmp_path,
        "Tall",
        [{"kind": "calendar", "w": 1, "h": 2}, {"kind": "clock", "w": 1, "h": 1}],
        lambda c: len(c) == 3,
    )
    spots = [c for c in out["cards"] if c["spot"]]
    assert [(s["col"], s["row"], s["text"]) for s in spots] == [
        ("2 / span 1", "2 / span 1", SPOT_TEXT)
    ], out


@needs_chrome
def test_full_screen_has_no_spots(server, tmp_path):  # noqa: F811
    out = _show(
        server,
        tmp_path,
        "Full",
        [{"kind": "clock", "w": 2, "h": 1}, {"kind": "clock", "w": 2, "h": 1}],
        lambda c: len(c) >= 2,
    )
    assert [c["kind"] for c in out["cards"]] == ["clock", "clock"]
    assert not any(c["spot"] for c in out["cards"])


@needs_chrome
def test_empty_screen_keeps_single_hint(server, tmp_path):  # noqa: F811
    out = _show(server, tmp_path, "Bare", [], lambda c: len(c) >= 1)
    assert len(out["cards"]) == 1
    assert out["cards"][0]["text"] == "Nothing on this screen yet. Add widgets from your phone."
    assert not out["cards"][0]["spot"]
