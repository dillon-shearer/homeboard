import json
import subprocess
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import events
from app.main import create_app
from tests.chrome import _Chrome, needs_chrome, needs_node, server  # noqa: F401
from tests.test_caption_lifetime import DISPLAY_JS


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_brightness_defaults_to_100_and_saves(client):
    assert client.get("/api/settings").json()["brightness"] == 100
    events.last_event = None
    r = client.put("/api/settings", json={"brightness": 65})
    assert r.status_code == 200 and r.json()["brightness"] == 65
    assert events.last_event == "settings_changed"
    assert client.get("/api/settings").json()["brightness"] == 65


@pytest.mark.parametrize("value", [5, 9, 101, 65.5, True, "bright"])
def test_brightness_out_of_range_is_422(client, value):
    assert client.put("/api/settings", json={"brightness": value}).status_code == 422
    assert client.get("/api/settings").json()["brightness"] == 100


def test_brightness_limits_are_accepted(client):
    assert client.put("/api/settings", json={"brightness": 10}).json()["brightness"] == 10
    assert client.put("/api/settings", json={"brightness": 100}).json()["brightness"] == 100


@needs_node
def test_brightness_helpers():
    script = f"""
    const S = require({json.dumps(str(DISPLAY_JS))});
    console.log(JSON.stringify({{
      half: S.dragBrightness(100, 300, 600),
      up: S.dragBrightness(50, -150, 600),
      low: S.dragBrightness(30, 600, 600),
      high: S.dragBrightness(90, -600, 600),
      none: S.dragBrightness(70, 0, 600),
      clampLow: S.clampBrightness(3),
      clampHigh: S.clampBrightness(140),
      dim65: S.dimOpacity(65),
      dim100: S.dimOpacity(100),
      dimFloor: S.dimOpacity(0),
      setLow: S.applySettings(null, {{brightness: 2}}).brightness,
      setDefault: S.applySettings(null, {{}}).brightness,
    }}));
    """
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    r = json.loads(out.stdout)
    assert r["half"] == 50
    assert r["up"] == 75
    assert r["low"] == 10 and r["high"] == 100 and r["none"] == 70
    assert r["clampLow"] == 10 and r["clampHigh"] == 100
    assert r["dim65"] == pytest.approx(0.35)
    assert r["dim100"] == 0 and r["dimFloor"] == pytest.approx(0.9)
    assert r["setLow"] == 10 and r["setDefault"] == 100


STATE = """JSON.stringify((() => {
  const b = document.getElementById('brightness');
  const d = document.getElementById('dim');
  if (!b || !d || !d.style.opacity) return null;
  return {
    text: b.textContent,
    readout: parseFloat(getComputedStyle(b).opacity),
    dim: parseFloat(getComputedStyle(d).opacity),
  };
})())"""


def _state(chrome):
    return json.loads(chrome.eval(STATE) or "null")


def _until(fn, ready):
    v = None
    for _ in range(100):
        v = fn()
        if ready(v):
            return v
        time.sleep(0.05)
    return v


def _settings(base):
    return httpx.get(f"{base}/api/settings").json()


def _touch(chrome, kind, x=None, y=None):
    points = [] if kind == "touchEnd" else [{"x": x, "y": y}]
    chrome.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})


@needs_chrome
def test_left_edge_drag_dims_and_saves(server, tmp_path):  # noqa: F811
    chrome = _Chrome(tmp_path / "chrome", 1024, 600)
    try:
        chrome.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
        chrome.send("Page.navigate", {"url": f"{server}/"})
        _until(lambda: chrome.eval("document.readyState"), lambda s: s == "complete")
        time.sleep(0.5)
        _touch(chrome, "touchStart", 30, 100)
        for y in range(130, 280, 30):
            _touch(chrome, "touchMove", 30, y)
        mid = _until(lambda: _state(chrome), lambda s: s and s["readout"] > 0.9)
        for y in range(280, 401, 30):
            _touch(chrome, "touchMove", 30, y)
        _touch(chrome, "touchMove", 30, 400)
        _touch(chrome, "touchEnd")
        released = time.monotonic()
        saved = _until(lambda: _settings(server), lambda s: s["brightness"] != 100)
        after = _state(chrome)
        time.sleep(max(0, 1.5 - (time.monotonic() - released)))
        faded = _state(chrome)

        time.sleep(1)
        _touch(chrome, "touchStart", 200, 300)
        for i in range(1, 11):
            _touch(chrome, "touchMove", 200 + 30 * i, 300)
        _touch(chrome, "touchEnd")
        time.sleep(0.6)
        swiped = _settings(server)

        httpx.put(f"{server}/api/settings", json={"brightness": 40})
        chrome.send("Page.navigate", {"url": f"{server}/"})
        reloaded = _until(lambda: _state(chrome), lambda s: s and abs(s["dim"] - 0.6) < 0.02)
    finally:
        chrome.close()
    assert mid["readout"] > 0.9 and mid["text"].endswith("%")
    assert abs(saved["brightness"] - 50) <= 2
    assert after["dim"] == pytest.approx(1 - saved["brightness"] / 100, abs=0.02)
    assert faded["readout"] == 0
    assert swiped["brightness"] == saved["brightness"]
    assert reloaded["dim"] == pytest.approx(0.6, abs=0.02)
