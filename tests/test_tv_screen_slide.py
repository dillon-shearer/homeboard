import json
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.chrome import _Chrome, needs_chrome, needs_node, server  # noqa: F401

SCREEN_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "tv_screen.js"


def _node(body):
    script = f"const T = require({json.dumps(str(SCREEN_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@pytest.fixture
def screens(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c.get("/api/screens").json()


@needs_node
def test_needs_for_all_screens_is_the_union_of_their_keys(screens):
    r = _node(
        f"const s = {json.dumps(screens)};\n"
        "console.log(JSON.stringify({all: T.needs(s), each: s.map((x) => T.needs(x))}));"
    )
    union = set()
    for keys in r["each"]:
        union |= set(keys)
    assert len(screens) == 3
    assert sorted(r["all"]) == sorted(union) == ["events", "lists", "weather"]
    assert len(r["all"]) == len(set(r["all"]))


@needs_node
def test_neighbour_side_and_transform_strings():
    r = _node(
        "console.log(JSON.stringify({left: T.neighbourSide(-300), right: T.neighbourSide(300),"
        " none: T.neighbourSide(0), tx: T.slideX(-150), zero: T.slideX(0)}));"
    )
    assert r == {
        "left": "right",
        "right": "left",
        "none": None,
        "tx": "translateX(-150px)",
        "zero": "translateX(0px)",
    }


FRAME = """(() => {
  const f = document.getElementById('channel');
  const d = f && f.contentDocument;
  if (!d || d.location.pathname !== '/tv' || !d.getElementById('screen')) return null;
  return {
    grids: d.querySelectorAll('.tv-screen').length,
    transform: getComputedStyle(d.getElementById('screen')).transform,
    title: d.getElementById('title').textContent,
    cards: d.querySelectorAll('#screen .tv-card').length,
  };
})()"""


def _until(get, ok, tries=100):
    v = None
    for _ in range(tries):
        v = get()
        if ok(v):
            return v
        time.sleep(0.05)
    return v


def _tx(transform):
    if transform in (None, "none"):
        return 0.0
    return float(transform[transform.index("(") + 1 : -1].split(",")[4])


def _move(chrome, kind, x):
    points = [] if kind == "touchEnd" else [{"x": x, "y": 300}]
    chrome.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})


def _settings(server):  # noqa: F811
    return httpx.get(f"{server}/api/settings").json()


@needs_chrome
def test_screen_to_screen_swipe_slides_inside_tv(server, tmp_path):  # noqa: F811
    ordered = sorted(httpx.get(f"{server}/api/screens").json(), key=lambda s: s["sort_order"])
    first, second = ordered[0], ordered[1]
    httpx.put(f"{server}/api/settings", json={"channel": "screen", "screen_id": first["id"]})
    chrome = _Chrome(tmp_path / "chrome", 1024, 600)
    try:
        chrome.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
        chrome.send("Page.navigate", {"url": f"{server}/"})
        ready = _until(
            lambda: chrome.eval(FRAME), lambda o: o and o["title"] == first["name"] and o["cards"]
        )
        time.sleep(0.5)
        _move(chrome, "touchStart", 800)
        for i in range(1, 6):
            _move(chrome, "touchMove", 800 - 30 * i)
        mid = _until(lambda: chrome.eval(FRAME), lambda o: o and o["grids"] == 2, tries=40)
        for i in range(6, 11):
            _move(chrome, "touchMove", 800 - 30 * i)
        _move(chrome, "touchEnd", 500)
        landed = _until(lambda: _settings(server), lambda s: s["screen_id"] == second["id"])
        after = _until(
            lambda: chrome.eval(FRAME), lambda o: o["grids"] == 1 and o["title"] == second["name"]
        )
        time.sleep(1)
        _move(chrome, "touchStart", 800)
        for i in range(1, 5):
            time.sleep(0.05)
            _move(chrome, "touchMove", 800 - 10 * i)
        short_mid = chrome.eval(FRAME)
        time.sleep(0.25)
        _move(chrome, "touchEnd", 760)
        time.sleep(0.6)
        short = chrome.eval(FRAME)
        kept = _settings(server)
    finally:
        chrome.close()
    assert ready["grids"] == 1
    assert mid["grids"] == 2
    assert -1024 < _tx(mid["transform"]) < 0
    assert (landed["channel"], landed["screen_id"]) == ("screen", second["id"])
    assert after["grids"] == 1
    assert after["transform"] in ("none", "matrix(1, 0, 0, 1, 0, 0)")
    assert short_mid["grids"] == 2
    assert kept["screen_id"] == second["id"]
    assert short["grids"] == 1
    assert short["title"] == second["name"]
    assert _tx(short["transform"]) == 0


@needs_chrome
def test_drag_from_photos_shows_the_target_screen_before_the_slide_ends(server, tmp_path):  # noqa: F811
    ordered = sorted(httpx.get(f"{server}/api/screens").json(), key=lambda s: s["sort_order"])
    first, last = ordered[0], ordered[-1]
    httpx.put(f"{server}/api/settings", json={"channel": "photos", "screen_id": last["id"]})
    chrome = _Chrome(tmp_path / "chrome", 1024, 600)
    try:
        chrome.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
        chrome.send("Page.navigate", {"url": f"{server}/"})
        ready = _until(
            lambda: chrome.eval(FRAME), lambda o: o and o["title"] == last["name"] and o["cards"]
        )
        time.sleep(0.5)
        _move(chrome, "touchStart", 800)
        for i in range(1, 6):
            _move(chrome, "touchMove", 800 - 30 * i)
        mid = chrome.eval(FRAME)
        kept = _settings(server)
        for i in range(6, 11):
            _move(chrome, "touchMove", 800 - 30 * i)
        _move(chrome, "touchEnd", 500)
        landed = _until(lambda: _settings(server), lambda s: s["channel"] == "screen")
        after = _until(lambda: chrome.eval(FRAME), lambda o: o and o["title"] == first["name"])
    finally:
        chrome.close()
    assert ready["title"] == last["name"]
    assert kept["channel"] == "photos"
    assert mid["title"] == first["name"]
    assert mid["grids"] == 1
    assert (landed["channel"], landed["screen_id"]) == ("screen", first["id"])
    assert after["title"] == first["name"]
