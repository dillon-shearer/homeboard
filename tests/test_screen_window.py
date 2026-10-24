import json
import re
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.chrome import _Chrome, needs_chrome, needs_node, server  # noqa: F401

TV_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "tv_admin.js"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def _node(body):
    script = f"const T = require({json.dumps(str(TV_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@needs_node
def test_js_packer_matches_the_server_rule():
    r = _node(
        """
        const s = (list) => list.map((x, i) => ({ id: i + 1, w: +x[0], h: +x[2] }));
        console.log(JSON.stringify({
          ok: T.pack(s(["2x1", "1x1", "1x1"])),
          bad: T.pack(s(["2x1", "1x2"])),
          quarters: T.sizeOptions(s(["1x1", "1x1", "1x1", "1x1"]), 0),
          alone: T.sizeOptions(s(["1x1"]), 0),
          beside: T.sizeOptions(s(["1x1", "1x2"]), 0),
          empty: T.addSize([]),
          afterQuarter: T.addSize(s(["1x1"])),
          full: T.addSize(s(["2x2"])),
        }));
        """
    )
    assert r["ok"] == [
        {"id": 1, "col": 0, "row": 0, "w": 2, "h": 1},
        {"id": 2, "col": 0, "row": 1, "w": 1, "h": 1},
        {"id": 3, "col": 1, "row": 1, "w": 1, "h": 1},
    ]
    assert r["bad"] is None
    assert r["quarters"] == ["1x1"]
    assert r["alone"] == ["2x2", "2x1", "1x2", "1x1"]
    assert r["beside"] == ["1x2", "1x1"]
    assert (r["empty"], r["afterQuarter"], r["full"]) == ("2x2", "1x2", None)


def test_screen_window_markup(client):
    page = client.get("/admin").text
    start = page.index('<div id="screen-dialog" class="modal" hidden>')
    html = page[start : page.index('<div id="screen-delete-dialog"', start)]
    assert 'class="w95-window dialog"' in html
    assert re.search(r'<input id="screen-name"[^>]*maxlength="60"', html)
    assert 'id="screen-layout" class="layout"' in html
    assert re.findall(r'data-kind="(\w+)">(\w+)<', html) == [
        ("clock", "Clock"),
        ("weather", "Weather"),
        ("calendar", "Calendar"),
        ("lists", "Lists"),
    ]
    buttons = re.search(r'<div class="buttons">(.*?)</div>', html, re.DOTALL).group(1)
    assert re.findall(r">([^<]+)</button>", buttons) == ["Delete screen", "Done"]
    assert 'id="screen-delete" class="w95-btn del"' in buttons
    confirm = page[page.index('<div id="screen-delete-dialog" class="modal" hidden>') :]
    assert ">Delete</button>" in confirm and ">Cancel</button>" in confirm


def _wait(fn, tries=200):
    out = None
    for _ in range(tries):
        out = fn()
        if out:
            return out
        time.sleep(0.05)
    raise AssertionError("condition never held")


def _screen(server, name):  # noqa: F811
    for s in httpx.get(f"{server}/api/screens").json():
        if s["name"] == name:
            return s
    return None


def _widgets(server, name):  # noqa: F811
    s = _screen(server, name)
    return [(w["kind"], w["col"], w["row"], w["w"], w["h"]) for w in s["widgets"]] if s else None


def _js(expr):
    return json.dumps(expr)


@needs_chrome
def test_screen_window_in_headless_chrome(server, tmp_path):  # noqa: F811
    extra = httpx.post(f"{server}/api/lists", json={"name": "Hall jobs"}).json()
    chrome = _Chrome(tmp_path / "chrome", 412, 900)
    q = "document.querySelector"
    shown = "!document.getElementById({}).hidden"
    try:
        chrome.send("Page.navigate", {"url": f"{server}/admin"})
        _wait(lambda: chrome.eval("document.querySelectorAll('[data-screen-id]').length") > 0)
        chrome.eval("document.getElementById('screens-edit').click()")
        chrome.eval("document.getElementById('screens-new').click()")
        chrome.eval(
            "document.getElementById('name-input').value = 'Test';"
            "document.getElementById('name-save').click()"
        )
        _wait(lambda: _screen(server, "Test"))
        _wait(lambda: chrome.eval(shown.format(_js("screen-dialog"))))
        assert chrome.eval("document.getElementById('screen-name').value") == "Test"
        assert chrome.eval(shown.format(_js("name-dialog"))) is False
        _wait(lambda: chrome.eval("document.querySelectorAll('#screen-layout .empty').length") == 4)

        chrome.eval(f"{q}('#screen-layout .empty').click(); {q}('[data-kind=clock]').click()")
        _wait(lambda: _widgets(server, "Test") == [("clock", 0, 0, 2, 2)])
        _wait(lambda: chrome.eval(f"{q}('#screen-layout .cell.on').textContent") == "Clock")
        sizes = "[...document.getElementById('widget-size').options].map((o) => o.value)"
        assert chrome.eval(sizes) == ["2x2", "2x1", "1x2", "1x1"]

        chrome.eval(
            "const s = document.getElementById('widget-size');"
            "s.value = '1x1'; s.dispatchEvent(new Event('change'))"
        )
        _wait(lambda: _widgets(server, "Test") == [("clock", 0, 0, 1, 1)])
        _wait(lambda: chrome.eval("document.querySelectorAll('#screen-layout .empty').length") == 3)
        chrome.eval(f"{q}('#screen-layout .empty').click(); {q}('[data-kind=weather]').click()")
        _wait(lambda: len(_widgets(server, "Test")) == 2)
        assert _widgets(server, "Test") == [("clock", 0, 0, 1, 1), ("weather", 1, 0, 1, 2)]
        _wait(lambda: chrome.eval(f"{q}('#screen-layout .cell.on').textContent") == "Weather")
        assert chrome.eval(shown.format(_js("widget-list-field"))) is False

        chrome.eval(f"{q}('#screen-layout .empty').click(); {q}('[data-kind=lists]').click()")
        _wait(lambda: len(_widgets(server, "Test")) == 3)
        lists_widget = _screen(server, "Test")["widgets"][2]
        assert (lists_widget["kind"], lists_widget["w"], lists_widget["h"]) == ("lists", 1, 1)
        first_list = httpx.get(f"{server}/api/lists").json()[0]
        assert lists_widget["list_id"] == first_list["id"]
        _wait(lambda: chrome.eval(shown.format(_js("widget-list-field"))))
        chrome.eval(
            "const l = document.getElementById('widget-list');"
            f"l.value = '{extra['id']}'; l.dispatchEvent(new Event('change'))"
        )
        _wait(lambda: _screen(server, "Test")["widgets"][2]["list_id"] == extra["id"])
        _wait(lambda: chrome.eval(f"{q}('#screen-layout .cell.on').textContent") == "Hall jobs")

        chrome.eval(f"{q}('#screen-layout [data-widget-id]').click()")
        _wait(lambda: chrome.eval(f"{q}('#screen-layout .cell.on').textContent") == "Clock")
        chrome.eval("document.getElementById('widget-remove').click()")
        _wait(lambda: [w[0] for w in _widgets(server, "Test")] == ["weather", "lists"])

        chrome.eval(
            "document.getElementById('screen-name').value = 'Test 2';"
            "document.getElementById('screen-save').click()"
        )
        _wait(lambda: _screen(server, "Test 2"))
        assert chrome.eval(shown.format(_js("screen-dialog"))) is False
        sid = _screen(server, "Test 2")["id"]
        name_button = f"{q}('[data-edit-screen=\"{sid}\"] .w95-btn:nth-child(2)')"
        _wait(lambda: chrome.eval(f"{name_button} && {name_button}.textContent") == "Test 2")

        chrome.eval(f"{name_button}.click()")
        assert chrome.eval(shown.format(_js("screen-dialog")))
        chrome.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
        assert chrome.eval(shown.format(_js("screen-dialog"))) is False

        httpx.put(f"{server}/api/settings", json={"channel": "screen", "screen_id": sid})
        chrome.eval(f"{name_button}.click()")
        chrome.eval("document.getElementById('screen-delete').click()")
        assert chrome.eval(shown.format(_js("screen-delete-dialog")))
        assert chrome.eval("document.getElementById('screen-delete-text').textContent") == (
            'Delete "Test 2" and its widgets? The TV goes back to Photos if it is showing it.'
        )
        chrome.eval("document.getElementById('screen-confirm-delete').click()")
        _wait(lambda: _screen(server, "Test 2") is None)
        _wait(lambda: chrome.eval(f"{q}('[data-edit-screen=\"{sid}\"]') === null"))
        assert chrome.eval(shown.format(_js("screen-dialog"))) is False
        assert httpx.get(f"{server}/api/settings").json()["channel"] == "photos"
        chrome.eval("document.getElementById('screens-edit').click()")
        _wait(
            lambda: (
                chrome.eval(f"{q}('[data-channel=photos]').getAttribute('aria-pressed')") == "true"
            )
        )
    finally:
        chrome.close()
