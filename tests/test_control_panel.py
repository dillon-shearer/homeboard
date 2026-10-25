import json
import subprocess
import time

import pytest
from fastapi.responses import JSONResponse

from app import routes_sound
from tests.chrome import api, needs_chrome, needs_node
from tests.test_caption_lifetime import DISPLAY_JS


def _node(body):
    script = f"const S = require({json.dumps(str(DISPLAY_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@needs_node
def test_panel_drag_helpers():
    r = _node(
        """
        const w = 1024, h = 600;
        console.log(JSON.stringify({
          down: S.panelCommits(500, 0, S.SWIPE_PX + 1, 0, w),
          short: S.panelCommits(500, 0, S.SWIPE_PX, 0, w),
          fling: S.panelCommits(500, 0, 40, 1, w),
          edge: S.panelCommits(30, 0, S.SWIPE_PX + 40, 0, w),
          edgeBorder: S.panelCommits(62, 0, S.SWIPE_PX + 40, 0, w),
          sideways: S.panelCommits(500, 200, S.SWIPE_PX + 40, 0, w),
          up: S.panelCommits(500, 0, -200, 0, w),
          inEdge: [S.inEdge(0, w), S.inEdge(61, w), S.inEdge(62, w)],
          offsets: [
            S.panelOffset(0, h, false), S.panelOffset(200, h, false), S.panelOffset(900, h, false),
            S.panelOffset(-50, h, false), S.panelOffset(0, h, true), S.panelOffset(-200, h, true),
            S.panelOffset(150, h, true), S.panelOffset(-900, h, true),
          ],
        }));
        """
    )
    assert r["down"] is True
    assert r["short"] is False
    assert r["fling"] is True
    assert r["edge"] is False
    assert r["edgeBorder"] is True
    assert r["sideways"] is False
    assert r["up"] is False
    assert r["inEdge"] == [True, True, False]
    assert r["offsets"] == [-600, -400, 0, -600, 0, -200, 0, -600]


class FakeHelper:
    def __init__(self):
        self.connected = False
        self.calls = []

    def __call__(self, method, path, timeout=5):
        self.calls.append((method, path))
        if path == "/connect":
            self.connected = True
        elif path == "/disconnect":
            self.connected = False
        if path == "/status":
            return JSONResponse({"speaker": {"connected": self.connected, "name": "Bar"}})
        return JSONResponse({"ok": True})


@pytest.fixture
def helper(monkeypatch):
    fake = FakeHelper()
    monkeypatch.setattr(routes_sound, "helper", fake)
    return fake


_clock = [0.0]


def _touch(chrome, kind, points):
    step = {"touchStart": 1, "touchEnd": 0.1}.get(kind, 0.02)
    _clock[0] = max(_clock[0] + step, time.time())
    chrome.send(
        "Input.dispatchTouchEvent", {"type": kind, "touchPoints": points, "timestamp": _clock[0]}
    )


def _swipe(chrome, x, ys):
    _touch(chrome, "touchStart", [{"x": x, "y": ys[0]}])
    for y in ys[1:]:
        _touch(chrome, "touchMove", [{"x": x, "y": y}])
        time.sleep(0.02)
    _touch(chrome, "touchEnd", [])


def _sent(chrome):
    return json.loads(chrome.eval("JSON.stringify(window.__sent)"))


OPEN = (
    "!document.getElementById('control-panel').hidden && "
    "document.getElementById('control-panel').style.transform === 'translateY(0px)' && "
    "!document.getElementById('control-panel').classList.contains('slide')"
)
HIDDEN = "document.getElementById('control-panel').hidden"
DOWN = list(range(80, 460, 40))


def _load(chrome, server):
    chrome.size(1024, 600)
    chrome.touch(True)
    chrome.send("Page.navigate", {"url": f"{server}/"})
    chrome.wait("window.__test && __test.heard('hello') > 0 && window.__inflight === 0")


@needs_chrome
def test_control_panel_screens_speaker_and_brightness(chrome, server, helper):
    extra = api.post(f"{server}/api/screens", json={"name": "Kitchen"}).json()
    names = [s["name"] for s in api.get(f"{server}/api/screens").json()]
    _load(chrome, server)
    _swipe(chrome, 30, DOWN)
    time.sleep(0.4)
    edge_hidden = chrome.eval(HIDDEN)
    _swipe(chrome, 500, [300, 310, 330, 340])
    time.sleep(0.4)
    sideways_hidden = chrome.eval(HIDDEN)

    _swipe(chrome, 500, DOWN)
    chrome.wait(OPEN)
    labels = chrome.eval(
        "[...document.querySelectorAll('#cp-screens .cp-icon')].map(b => b.textContent)"
    )
    title = chrome.eval("document.getElementById('cp-title').textContent")

    chrome.wait("document.getElementById('cp-speaker-state').textContent === 'Off'")
    chrome.eval("document.getElementById('cp-speaker').click()")
    chrome.wait("document.getElementById('cp-speaker-state').textContent === 'Connected'")
    checked = chrome.eval("document.getElementById('cp-speaker').getAttribute('aria-checked')")
    chrome.eval("document.getElementById('cp-speaker').click()")
    chrome.wait("document.getElementById('cp-speaker-state').textContent === 'Off'")
    speaker_posts = [c for c in helper.calls if c[0] == "POST"]

    chrome.eval(
        "(s => { s.value = '40'; s.dispatchEvent(new Event('input')); })"
        "(document.getElementById('cp-brightness'))"
    )
    dim_live = chrome.eval("document.getElementById('dim').style.opacity")
    readout = chrome.eval("document.getElementById('cp-brightness-value').textContent")
    chrome.eval("document.getElementById('cp-brightness').dispatchEvent(new Event('change'))")
    chrome.wait("window.__inflight === 0 && window.__sent.length > 0")
    brightness = api.get(f"{server}/api/settings").json()["brightness"]

    box = chrome.eval(
        "(() => { const b = [...document.querySelectorAll('#cp-screens .cp-icon')]"
        ".find(n => n.textContent === 'Kitchen').getBoundingClientRect();"
        " return {x: b.left + b.width / 2, y: b.top + b.height / 2}; })()"
    )
    _touch(chrome, "touchStart", [box])
    _touch(chrome, "touchEnd", [])
    chrome.wait(HIDDEN)
    chrome.wait("window.__inflight === 0")
    sent = _sent(chrome)
    settings = api.get(f"{server}/api/settings").json()
    errors = chrome.eval("window.__errors")

    assert edge_hidden is True
    assert sideways_hidden is True
    assert title == "Control Panel"
    assert labels == ["Photos"] + names
    assert "Kitchen" in names
    assert checked == "true"
    assert speaker_posts == [("POST", "/connect"), ("POST", "/disconnect")]
    assert float(dim_live) == pytest.approx(0.6)
    assert readout == "40%"
    assert brightness == 40
    assert {"method": "PUT", "url": "/api/settings", "body": {"brightness": 40}} in sent
    assert sent[-1] == {
        "method": "PUT",
        "url": "/api/settings",
        "body": {"channel": "screen", "screen_id": extra["id"]},
    }
    assert settings["channel"] == "screen" and settings["screen_id"] == extra["id"]
    assert chrome.eval("document.getElementById('channel').style.transform") == "translateX(0px)"
    assert errors == []


@needs_chrome
def test_control_panel_restart_and_idle_close(chrome, server, helper):
    _load(chrome, server)
    _swipe(chrome, 500, DOWN)
    chrome.wait(OPEN)
    edge_hidden = chrome.eval("document.getElementById('edge').hidden")
    chrome.eval("document.getElementById('cp-restart').click()")
    asked = chrome.eval("!document.getElementById('cp-confirm').hidden")
    text = chrome.eval("document.getElementById('cp-confirm-text').textContent")
    buttons = chrome.eval(
        "[...document.querySelectorAll('#cp-confirm button')].map(b => b.textContent)"
    )
    chrome.eval("document.getElementById('cp-restart-no').click()")
    cancelled = chrome.eval("document.getElementById('cp-confirm').hidden")
    time.sleep(0.2)
    after_cancel = list(helper.calls)

    chrome.eval("__test.skip(6 * 60000)")
    chrome.run("__test.tick(15000)")
    chrome.wait(HIDDEN)
    edge_back = chrome.eval("!document.getElementById('edge').hidden")

    _swipe(chrome, 500, DOWN)
    chrome.wait(OPEN)
    chrome.eval("document.getElementById('cp-restart').click()")
    chrome.eval(
        "document.getElementById('cp-restart-yes').click();"
        "document.getElementById('cp-restart-yes').click()"
    )
    chrome.wait("!document.getElementById('splash').hidden")
    chrome.wait("window.__inflight === 0")
    label = chrome.eval("document.querySelector('#splash #label').textContent")
    reboots = [s for s in _sent(chrome) if s["url"] == "/api/sound/reboot"]
    errors = chrome.eval("window.__errors")

    assert edge_hidden is True
    assert asked is True
    assert text == "Restart the board? It takes about a minute."
    assert buttons == ["Restart", "Cancel"]
    assert cancelled is True
    assert ("POST", "/reboot") not in after_cancel
    assert edge_back is True
    assert len(reboots) == 1 and reboots[0]["method"] == "POST"
    assert helper.calls.count(("POST", "/reboot")) == 1
    assert label == "Home Board is restarting..."
    assert chrome.eval("document.getElementById('splash').classList.contains('fade')") is False
    assert errors == []
