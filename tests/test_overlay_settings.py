import json
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


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_defaults_returned(client):
    s = client.get("/api/settings").json()
    assert {k: s[k] for k in DEFAULTS} == DEFAULTS


@pytest.mark.parametrize(
    "field,value",
    [
        ("clock_size", 2),
        ("clock_size", 12),
        ("caption_size", 8),
        ("clock_pos", "top-center"),
        ("caption_pos", "middle-right"),
        ("clock_on_top", False),
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
    ],
)
def test_bad_values_rejected(client, field, value):
    r = client.put("/api/settings", json={field: value})
    assert r.status_code == 422
    assert client.get("/api/settings").json()[field] == DEFAULTS[field]


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
        "order": ["clock", "caption"],
    }
    assert r["shared"] == {
        "clockCell": "top-left",
        "captionCell": "top-left",
        "order": ["clock", "caption"],
    }
    assert r["below"]["order"] == ["caption", "clock"]
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
        "vars": {"--clock-size": "5vh", "--caption-size": "5vh"},
        "clock": "cell-bottom-right",
        "caption": "cell-bottom-left",
    }
    assert r["after"] == {
        "vars": {"--clock-size": "8vh", "--caption-size": "3vh"},
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
  return JSON.stringify({clock: r('clock'), caption: r('caption'), w: innerWidth, h: innerHeight});
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
    assert abs(clock["bottom"] - (1080 - edge)) < 2, out
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
