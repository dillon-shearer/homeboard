import time

from tests.chrome import api, needs_chrome
from tests.test_control_panel import DOWN, HIDDEN, OPEN, _load, _swipe

FRAME = "document.getElementById('channel').contentDocument"
READY = (
    f"{FRAME} && {FRAME}.readyState === 'complete' && {FRAME}.body && "
    f"{FRAME}.body.children.length > 0 && window.__inflight === 0 && "
    "document.getElementById('channel').style.transform === 'translateX(0px)' && "
    "document.elementFromPoint(500, 80) === document.getElementById('channel')"
)
SCROLLER = f"""(d => {{
  const box = d.createElement('div');
  box.id = 'test-scroller';
  box.style.cssText = 'position:fixed;left:300px;top:40px;width:400px;height:300px;'
    + 'overflow-y:auto;z-index:99;background:#fff';
  const inner = d.createElement('div');
  inner.style.height = '1200px';
  box.appendChild(inner);
  d.body.appendChild(box);
  return box.scrollHeight > box.clientHeight && getComputedStyle(box).overflowY === 'auto';
}})({FRAME})"""


def _screen_channel(chrome, server):
    screen = api.get(f"{server}/api/screens").json()[0]
    api.put(f"{server}/api/settings", json={"channel": "screen", "screen_id": screen["id"]})
    _load(chrome, server)
    chrome.wait(READY)
    return screen


def _settings(server):
    s = api.get(f"{server}/api/settings").json()
    return s["channel"], s["screen_id"]


@needs_chrome
def test_swipe_down_on_a_screen_opens_panel_and_keeps_channel(chrome, server):
    screen = _screen_channel(chrome, server)
    _swipe(chrome, 500, DOWN)
    chrome.wait(OPEN)
    opened = _settings(server)
    _swipe(chrome, 500, list(reversed(DOWN)))
    chrome.wait(HIDDEN)
    time.sleep(0.4)
    closed = _settings(server)
    sent = chrome.eval("JSON.stringify(window.__sent)")
    transform = chrome.eval("document.getElementById('channel').style.transform")
    errors = chrome.eval("window.__errors")

    assert opened == ("screen", screen["id"])
    assert closed == ("screen", screen["id"])
    assert '"channel"' not in sent
    assert transform == "translateX(0px)"
    assert errors == []


@needs_chrome
def test_drag_down_on_a_scrolling_list_does_not_open_panel(chrome, server):
    _screen_channel(chrome, server)
    assert chrome.eval(SCROLLER) is True
    _swipe(chrome, 500, DOWN[:6])
    time.sleep(0.4)
    on_list = chrome.eval(HIDDEN)
    _swipe(chrome, 900, DOWN)
    chrome.wait(OPEN)

    assert on_list is True
    assert chrome.eval("window.__errors") == []
