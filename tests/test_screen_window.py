import json
import time

from tests.chrome import api, needs_chrome


def _wait(fn, tries=200):
    out = None
    for _ in range(tries):
        out = fn()
        if out:
            return out
        time.sleep(0.05)
    raise AssertionError("condition never held")


def _screen(server, name):
    for s in api.get(f"{server}/api/screens").json():
        if s["name"] == name:
            return s
    return None


def _widgets(server, name):
    s = _screen(server, name)
    return [(w["kind"], w["col"], w["row"], w["w"], w["h"]) for w in s["widgets"]] if s else None


def _js(expr):
    return json.dumps(expr)


@needs_chrome
def test_screen_window_in_headless_chrome(chrome, server):
    extra = api.post(f"{server}/api/lists", json={"name": "Hall jobs"}).json()
    chrome.size(412, 900)
    q = "document.querySelector"
    shown = "!document.getElementById({}).hidden"
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
    _wait(lambda: _widgets(server, "Test") == [("clock", 0, 0, 2, 1)])
    _wait(lambda: chrome.eval(f"{q}('#screen-layout .cell.on').textContent") == "Clock")
    sizes = "[...document.getElementById('widget-size').options].map((o) => o.value)"
    assert chrome.eval(sizes) == ["2x1", "1x1"]

    chrome.eval(
        "const s = document.getElementById('widget-size');"
        "s.value = '1x1'; s.dispatchEvent(new Event('change'))"
    )
    _wait(lambda: _widgets(server, "Test") == [("clock", 0, 0, 1, 1)])
    _wait(lambda: chrome.eval("document.querySelectorAll('#screen-layout .empty').length") == 3)
    assert chrome.eval(f"{q}('#screen-layout .empty').textContent") == "Move here"
    chrome.eval(f"{q}('#screen-layout .cell.on').click()")
    assert chrome.eval(f"{q}('#screen-layout .empty').textContent") == "+"
    chrome.eval(f"{q}('#screen-layout .empty').click(); {q}('[data-kind=weather]').click()")
    _wait(lambda: len(_widgets(server, "Test")) == 2)
    assert _widgets(server, "Test") == [("clock", 0, 0, 1, 1), ("weather", 1, 0, 1, 2)]
    _wait(lambda: chrome.eval(f"{q}('#screen-layout .cell.on').textContent") == "Weather")
    assert chrome.eval(shown.format(_js("widget-list-field"))) is False

    chrome.eval(f"{q}('#screen-layout .cell.on').click()")
    chrome.eval(f"{q}('#screen-layout .empty').click(); {q}('[data-kind=lists]').click()")
    _wait(lambda: len(_widgets(server, "Test")) == 3)
    lists_widget = _screen(server, "Test")["widgets"][2]
    assert (lists_widget["kind"], lists_widget["w"], lists_widget["h"]) == ("lists", 1, 1)
    first_list = api.get(f"{server}/api/lists").json()[0]
    assert lists_widget["list_id"] == first_list["id"]
    _wait(lambda: chrome.eval(shown.format(_js("widget-list-field"))))
    chrome.eval(
        "const l = document.getElementById('widget-list');"
        f"l.value = '{extra['id']}'; l.dispatchEvent(new Event('change'))"
    )
    _wait(lambda: _screen(server, "Test")["widgets"][2]["list_id"] == extra["id"])
    _wait(lambda: chrome.eval(f"{q}('#screen-layout .cell.on').textContent") == "Hall jobs")

    chrome.eval(f"{q}('#screen-layout .cell.on').click()")
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

    api.put(f"{server}/api/settings", json={"channel": "screen", "screen_id": sid})
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
    assert api.get(f"{server}/api/settings").json()["channel"] == "photos"
    chrome.eval("document.getElementById('screens-edit').click()")
    _wait(
        lambda: chrome.eval(f"{q}('[data-channel=photos]').getAttribute('aria-pressed')") == "true"
    )


def _open_window(chrome, server, sid):
    q = "document.querySelector"
    name_button = f"{q}('[data-edit-screen=\"{sid}\"] .w95-btn:nth-child(2)')"
    chrome.send("Page.navigate", {"url": f"{server}/admin"})
    _wait(lambda: chrome.eval("document.querySelectorAll('[data-screen-id]').length") > 0)
    chrome.eval("document.getElementById('screens-edit').click()")
    chrome.eval(f"{name_button}.click()")
    _wait(lambda: chrome.eval("!document.getElementById('screen-dialog').hidden"))


@needs_chrome
def test_screen_window_adds_at_a_cell_moves_and_swaps(chrome, server):
    sid = api.post(f"{server}/api/screens", json={"name": "Grid"}).json()["id"]
    api.post(
        f"{server}/api/screens/{sid}/widgets",
        json={"kind": "clock", "col": 0, "row": 0, "w": 1, "h": 1},
    )
    chrome.size(412, 900)
    q = "document.querySelector"
    sizes = "[...document.getElementById('widget-size').options].map((o) => o.value)"
    kinds = (
        "[...document.querySelectorAll('#widget-add [data-kind]')]"
        ".filter((b) => !b.hidden).map((b) => b.getAttribute('data-kind'))"
    )
    _open_window(chrome, server, sid)
    _wait(lambda: chrome.eval("document.querySelectorAll('#screen-layout .empty').length") == 3)
    assert chrome.eval("!document.getElementById('widget-add').hidden") is False
    chrome.eval(f"{q}('[data-cell=\"0,1\"]').click()")
    assert chrome.eval("!document.getElementById('widget-add').hidden") is True
    assert chrome.eval(kinds) == ["clock", "weather", "calendar", "lists"]
    chrome.eval(f"{q}('[data-kind=weather]').click()")
    _wait(lambda: _widgets(server, "Grid") == [("clock", 0, 0, 1, 1), ("weather", 0, 1, 2, 1)])
    _wait(lambda: chrome.eval(f"{q}('#screen-layout .cell.on').textContent") == "Weather")
    assert chrome.eval(sizes) == ["2x1", "1x1"]

    chrome.eval(
        "const s = document.getElementById('widget-size');"
        "s.value = '1x1'; s.dispatchEvent(new Event('change'))"
    )
    _wait(lambda: ("weather", 0, 1, 1, 1) in _widgets(server, "Grid"))
    chrome.eval(f"{q}('#screen-layout .cell.on').click()")
    chrome.eval(f"{q}('#screen-layout [data-widget-id]').click()")
    _wait(lambda: chrome.eval(f"{q}('#screen-layout .cell.on').textContent") == "Clock")
    assert chrome.eval(sizes) == ["2x1", "1x1"]
    targets = "[...document.querySelectorAll('#screen-layout .empty')].map((e) => e.textContent)"
    assert chrome.eval(targets) == ["Move here", "Move here"]

    chrome.eval(f"{q}('[data-cell=\"1,0\"]').click()")
    _wait(lambda: _widgets(server, "Grid") == [("clock", 1, 0, 1, 1), ("weather", 0, 1, 1, 1)])
    _wait(lambda: chrome.eval(f"{q}('#screen-layout .cell.on').textContent") == "Clock")
    assert chrome.eval(sizes) == ["1x1"]

    weather = _screen(server, "Grid")["widgets"][1]["id"]
    chrome.eval(f"{q}('[data-widget-id=\"{weather}\"]').click()")
    _wait(lambda: _widgets(server, "Grid") == [("clock", 0, 1, 1, 1), ("weather", 1, 0, 1, 1)])
    assert chrome.eval("document.getElementById('screen-error').hidden") is True
