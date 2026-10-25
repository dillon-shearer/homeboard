import json
import re
import subprocess
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.chrome import _Chrome, needs_chrome, server  # noqa: F401
from tests.test_caption_lifetime import DISPLAY_JS, HARNESS, needs_node

DEFAULTS = {
    "clock_size": 5,
    "caption_size": 5,
    "clock_pos": "bottom-right",
    "caption_pos": "bottom-left",
    "clock_on_top": True,
}
DATE_DEFAULTS = {
    "date_size": 3,
    "date_pos": "bottom-right",
    "date_on_top": False,
    "date_show": True,
}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_defaults_returned(client):
    s = client.get("/api/settings").json()
    assert {k: s[k] for k in DEFAULTS} == DEFAULTS
    assert {k: s[k] for k in DATE_DEFAULTS} == DATE_DEFAULTS


@pytest.mark.parametrize(
    "field,value",
    [
        ("clock_size", 2),
        ("clock_size", 12),
        ("caption_size", 8),
        ("clock_pos", "top-center"),
        ("caption_pos", "middle-right"),
        ("clock_on_top", False),
        ("date_size", 2),
        ("date_size", 12),
        ("date_pos", "top-left"),
        ("date_on_top", True),
        ("date_show", False),
    ],
)
def test_put_each_field_round_trips(client, field, value):
    r = client.put("/api/settings", json={field: value})
    assert r.status_code == 200
    assert r.json()[field] == value
    assert client.get("/api/settings").json()[field] == value


@pytest.mark.parametrize(
    "field,value",
    [
        ("clock_size", 1),
        ("clock_size", 13),
        ("caption_size", 1),
        ("caption_size", 13),
        ("caption_size", "big"),
        ("clock_size", 5.5),
        ("clock_pos", "nowhere"),
        ("caption_pos", "left"),
        ("date_size", 1),
        ("date_size", 13),
        ("date_size", "big"),
        ("date_pos", "nowhere"),
    ],
)
def test_bad_values_rejected(client, field, value):
    r = client.put("/api/settings", json={field: value})
    assert r.status_code == 422
    assert client.get("/api/settings").json()[field] == {**DEFAULTS, **DATE_DEFAULTS}[field]


def _node(body):
    script = f"{HARNESS}\nconst S = require({json.dumps(str(DISPLAY_JS))});\n(async () => {{\n{body}\n}})();"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@needs_node
def test_apply_settings_fills_defaults_and_ignores_bad_values():
    r = _node(
        """
        const d = S.applySettings(null, null);
        const good = S.applySettings(d, {
          clock_size: 8, caption_size: 12, clock_pos: "top-center",
          caption_pos: "middle-left", clock_on_top: false,
        });
        const bad = S.applySettings(good, {
          clock_size: 1, caption_size: "big", clock_pos: "nowhere", caption_pos: 3,
        });
        const edge = S.applySettings(d, { clock_size: 13, caption_size: 2.5 });
        console.log(JSON.stringify({ d, good, bad, edge }));
        """
    )
    pick = lambda s: {k: s[k] for k in DEFAULTS}
    assert pick(r["d"]) == DEFAULTS
    want = {
        "clock_size": 8,
        "caption_size": 12,
        "clock_pos": "top-center",
        "caption_pos": "middle-left",
        "clock_on_top": False,
    }
    assert pick(r["good"]) == want
    assert pick(r["bad"]) == want
    assert pick(r["edge"]) == DEFAULTS


@needs_node
def test_overlay_layout_cells_and_order():
    r = _node(
        """
        console.log(JSON.stringify({
          d: S.overlayLayout({}),
          shared: S.overlayLayout({ clock_pos: "top-left", caption_pos: "top-left", clock_on_top: true }),
          below: S.overlayLayout({ clock_pos: "bottom-right", caption_pos: "bottom-right", clock_on_top: false }),
        }));
        """
    )
    assert r["d"] == {
        "clockCell": "bottom-right",
        "captionCell": "bottom-left",
        "dateCell": "bottom-right",
        "order": ["clock", "date", "caption"],
    }
    assert r["shared"] == {
        "clockCell": "top-left",
        "captionCell": "top-left",
        "dateCell": "bottom-right",
        "order": ["clock", "date", "caption"],
    }
    assert r["below"]["order"] == ["caption", "clock", "date"]
    assert r["below"]["clockCell"] == r["below"]["captionCell"] == "bottom-right"


@needs_node
def test_settings_change_moves_elements_and_sets_sizes():
    r = _node(
        """
        await advance(100);
        const before = { vars: { ...rootVars }, clock: els.clock.parentNode.id, caption: els.caption.parentNode.id };
        server.settings = { ...server.settings, clock_size: 8, caption_size: 3, clock_pos: "top-center", caption_pos: "middle-right" };
        sources[0].emit("settings_changed");
        await advance(100);
        const after = { vars: { ...rootVars }, clock: els.clock.parentNode.id, caption: els.caption.parentNode.id };
        console.log(JSON.stringify({ before, after }));
        """
    )
    assert r["before"] == {
        "vars": {"--clock-size": "5vh", "--caption-size": "5vh", "--date-size": "3vh"},
        "clock": "cell-bottom-right",
        "caption": "cell-bottom-left",
    }
    assert r["after"] == {
        "vars": {"--clock-size": "8vh", "--caption-size": "3vh", "--date-size": "3vh"},
        "clock": "cell-top-center",
        "caption": "cell-middle-right",
    }


RECTS = """(() => {
  const cap = document.getElementById('caption');
  cap.textContent = 'Beach day';
  const r = (id) => {
    const el = document.getElementById(id);
    const b = el.getBoundingClientRect();
    return {top: b.top, bottom: b.bottom, left: b.left, right: b.right,
            font: parseFloat(getComputedStyle(el).fontSize), cell: el.parentNode.id,
            text: el.textContent};
  };
  return JSON.stringify({clock: r('clock'), caption: r('caption'), date: r('date'),
                         w: innerWidth, h: innerHeight});
})()"""


def _rects(chrome, ready):
    out = None
    for _ in range(100):
        out = json.loads(chrome.eval(RECTS) or "null")
        if out and out["clock"]["text"] and ready(out):
            return out
        time.sleep(0.05)
    return out


@needs_chrome
def test_default_overlay_matches_old_look(server, tmp_path):  # noqa: F811
    chrome = _Chrome(tmp_path / "chrome", 1920, 1080)
    try:
        chrome.send("Page.navigate", {"url": f"{server}/"})
        out = _rects(chrome, lambda o: o["caption"]["text"] and o["caption"]["bottom"] > 900)
    finally:
        chrome.close()
    clock, cap = out["clock"], out["caption"]
    assert (clock["cell"], cap["cell"]) == ("cell-bottom-right", "cell-bottom-left"), out
    edge = 0.04 * 1080
    assert abs(clock["font"] - 54) < 1 and abs(cap["font"] - 54) < 1, out
    assert abs(clock["right"] - (1920 - edge)) < 2, out
    assert abs(out["date"]["bottom"] - (1080 - edge)) < 2, out
    assert abs(cap["left"] - edge) < 2 and abs(cap["bottom"] - (1080 - edge)) < 2, out


@needs_chrome
def test_clock_top_center_size_8(server, tmp_path):  # noqa: F811
    httpx.put(f"{server}/api/settings", json={"clock_pos": "top-center", "clock_size": 8})
    chrome = _Chrome(tmp_path / "chrome", 1920, 1080)
    try:
        chrome.send("Page.navigate", {"url": f"{server}/"})
        out = _rects(chrome, lambda o: o["clock"]["cell"] == "cell-top-center")
    finally:
        chrome.close()
    clock = out["clock"]
    assert abs((clock["left"] + clock["right"]) / 2 - 960) < 10
    assert clock["top"] < 360
    assert abs(clock["font"] - 86.4) < 2


@needs_chrome
def test_shared_cell_caption_above_clock_and_live_move(server, tmp_path):  # noqa: F811
    httpx.put(
        f"{server}/api/settings",
        json={"clock_pos": "bottom-right", "caption_pos": "bottom-right", "clock_on_top": False},
    )
    chrome = _Chrome(tmp_path / "chrome", 1920, 1080)
    try:
        chrome.send("Page.navigate", {"url": f"{server}/"})
        shared = _rects(chrome, lambda o: o["caption"]["cell"] == "cell-bottom-right")
        httpx.put(f"{server}/api/settings", json={"clock_pos": "top-left"})
        moved = _rects(chrome, lambda o: o["clock"]["cell"] == "cell-top-left")
    finally:
        chrome.close()
    assert shared["caption"]["top"] < shared["clock"]["top"]
    assert shared["caption"]["bottom"] <= shared["clock"]["top"] + 1
    assert abs(shared["clock"]["right"] - (1920 - 0.04 * 1080)) < 2
    assert moved["clock"]["cell"] == "cell-top-left"
    assert moved["clock"]["top"] < 360 and moved["clock"]["left"] < 100


@needs_node
def test_apply_settings_date_defaults_and_bad_values():
    r = _node(
        """
        const d = S.applySettings(null, null);
        const good = S.applySettings(d, {
          date_size: 7, date_pos: "top-left", date_on_top: true, date_show: false,
        });
        const bad = S.applySettings(good, { date_size: 13, date_pos: "nowhere" });
        const low = S.applySettings(d, { date_size: 1, date_pos: 3 });
        console.log(JSON.stringify({ d, good, bad, low }));
        """
    )
    pick = lambda s: {k: s[k] for k in DATE_DEFAULTS}
    assert pick(r["d"]) == DATE_DEFAULTS
    want = {"date_size": 7, "date_pos": "top-left", "date_on_top": True, "date_show": False}
    assert pick(r["good"]) == want
    assert pick(r["bad"]) == want
    assert pick(r["low"]) == DATE_DEFAULTS


@needs_node
def test_format_date():
    r = _node(
        """
        console.log(JSON.stringify({
          a: S.formatDate(new Date(2026, 9, 24)),
          b: S.formatDate(new Date(2027, 0, 1)),
        }));
        """
    )
    assert r == {"a": "October 24, 2026", "b": "January 1, 2027"}


@needs_node
def test_overlay_layout_date_order():
    r = _node(
        """
        const all = { clock_pos: "bottom-right", caption_pos: "bottom-right" };
        const cap = { clock_pos: "top-left", caption_pos: "middle-center", date_pos: "middle-center" };
        console.log(JSON.stringify({
          d: S.overlayLayout(all),
          under: S.overlayLayout({ ...all, clock_on_top: false }),
          above: S.overlayLayout({ ...all, date_on_top: true }),
          capBelow: S.overlayLayout(cap),
          capAbove: S.overlayLayout({ ...cap, date_on_top: true }),
          alone: S.overlayLayout({ date_pos: "top-center" }),
        }));
        """
    )
    assert r["d"]["order"] == ["clock", "date", "caption"]
    assert r["under"]["order"] == ["caption", "clock", "date"]
    assert r["above"]["order"] == ["date", "clock", "caption"]
    assert r["capBelow"]["order"] == ["clock", "caption", "date"]
    assert r["capBelow"]["dateCell"] == "middle-center"
    assert r["capAbove"]["order"] == ["clock", "date", "caption"]
    assert r["alone"]["dateCell"] == "top-center"
    assert sorted(r["alone"]["order"]) == ["caption", "clock", "date"]


@needs_node
def test_date_show_hides_and_shows_date():
    r = _node(
        """
        await advance(100);
        const d = els.date;
        const before = { hidden: d.hidden, cell: d.parentNode.id, text: d.textContent };
        server.settings = { ...server.settings, date_show: false };
        sources[0].emit("settings_changed");
        await advance(100);
        const off = d.hidden;
        server.settings = { ...server.settings, date_show: true, date_size: 6 };
        sources[0].emit("settings_changed");
        await advance(100);
        console.log(JSON.stringify({ before, off, on: d.hidden, size: rootVars["--date-size"] }));
        """
    )
    assert r["before"]["hidden"] is False
    assert r["before"]["cell"] == "cell-bottom-right"
    assert re.match(r"^[A-Z][a-z]+ \d{1,2}, \d{4}$", r["before"]["text"])
    assert r["off"] is True
    assert r["on"] is False
    assert r["size"] == "6vh"


@needs_chrome
def test_default_date_under_clock(server, tmp_path):  # noqa: F811
    chrome = _Chrome(tmp_path / "chrome", 1920, 1080)
    kids = "[...document.getElementById('cell-bottom-right').children].map(e => e.id)"
    try:
        chrome.send("Page.navigate", {"url": f"{server}/"})
        out = _rects(chrome, lambda o: o["date"]["text"])
        order = json.loads(chrome.eval(f"JSON.stringify({kids})"))
    finally:
        chrome.close()
    clock, date = out["clock"], out["date"]
    assert date["cell"] == "cell-bottom-right", out
    assert order == ["clock", "date"]
    assert date["top"] >= clock["bottom"], out
    assert abs(date["font"] - 32.4) < 2, out
    assert re.match(r"^[A-Z][a-z]+ \d{1,2}, \d{4}$", date["text"]), out


@needs_chrome
def test_date_show_false_hides_date_live(server, tmp_path):  # noqa: F811
    chrome = _Chrome(tmp_path / "chrome", 1920, 1080)
    hidden = "document.getElementById('date').hidden"
    try:
        chrome.send("Page.navigate", {"url": f"{server}/"})
        _rects(chrome, lambda o: o["date"]["text"])
        httpx.put(f"{server}/api/settings", json={"date_show": False})
        off = None
        for _ in range(100):
            off = chrome.eval(hidden)
            if off:
                break
            time.sleep(0.05)
    finally:
        chrome.close()
    assert off is True
