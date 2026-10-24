import time

import httpx

from tests.chrome import _Chrome, needs_chrome, server  # noqa: F401
from tests.test_photo_focus import _jpeg

ACTIVE = "document.activeElement"
TODAY = "(() => { const d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); })()"


def _wait(chrome, expr):
    for _ in range(100):
        value = chrome.eval(expr)
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(expr)


def _shown(dialog_id):
    return f"!document.getElementById('{dialog_id}').hidden"


def _click(el_id):
    return f"document.getElementById('{el_id}').click()"


def _escape():
    return "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))"


def _is(el_id):
    return f"{ACTIVE} === document.getElementById('{el_id}')"


def _open(chrome, base, path):
    chrome.send("Page.navigate", {"url": f"{base}{path}"})
    _wait(chrome, "document.readyState === 'complete'")


@needs_chrome
def test_screen_dialogs_return_focus(server, tmp_path):  # noqa: F811
    chrome = _Chrome(tmp_path / "chrome", 412, 900)
    try:
        _open(chrome, server, "/admin")
        _wait(chrome, "document.querySelectorAll('[data-screen-id]').length > 0")
        chrome.eval(_click("screens-edit"))
        chrome.eval(_click("screens-new"))
        assert chrome.eval(_shown("name-dialog"))
        httpx.post(f"{server}/api/screens", json={"name": "Behind"})
        chrome.eval(_click("name-close"))
        assert chrome.eval(_is("screens-new"))
        _wait(chrome, "!!document.querySelector('[data-edit-screen]')")

        group = "document.querySelector('[data-edit-screen]')"
        sid = chrome.eval(f"{group}.getAttribute('data-edit-screen')")
        chrome.eval(f"{group}.children[1].click()")
        assert chrome.eval(_shown("screen-dialog"))
        chrome.eval(_click("screen-delete"))
        assert chrome.eval(_shown("screen-delete-dialog"))
        chrome.eval(_escape())
        assert chrome.eval(_is("screen-delete"))
        chrome.eval(_click("screen-backdrop"))
        _wait(chrome, f"{ACTIVE} === document.querySelector('[data-edit-screen=\"{sid}\"]').children[1]")
    finally:
        chrome.close()


@needs_chrome
def test_new_screen_dialog_holds_the_screen_list(server, tmp_path):  # noqa: F811
    chrome = _Chrome(tmp_path / "chrome", 412, 900)
    try:
        _open(chrome, server, "/admin")
        _wait(chrome, "document.querySelectorAll('[data-screen-id]').length > 0")
        before = chrome.eval("document.querySelectorAll('[data-screen-id]').length")
        chrome.eval(_click("screens-new"))
        httpx.post(f"{server}/api/screens", json={"name": "Hidden"})
        time.sleep(10.5)
        assert chrome.eval("document.querySelectorAll('[data-screen-id]').length") == before
        chrome.eval(_click("name-cancel"))
        _wait(chrome, f"document.querySelectorAll('[data-screen-id]').length === {before + 1}")
    finally:
        chrome.close()


@needs_chrome
def test_calendar_dialogs_return_focus_and_hold_reload(server, tmp_path):  # noqa: F811
    chrome = _Chrome(tmp_path / "chrome", 412, 900)
    try:
        _open(chrome, server, "/admin/calendar")
        today = chrome.eval(TODAY)
        httpx.post(f"{server}/api/events", json={"title": "Dentist", "date": today, "time": None, "repeat": "never"})
        _wait(chrome, "document.querySelectorAll('#day-events li').length === 1")

        chrome.eval(_click("add-event"))
        assert chrome.eval(_shown("event-dialog"))
        chrome.eval(_escape())
        assert chrome.eval(_is("add-event"))

        edit = "document.querySelector('#day-events .edit')"
        chrome.eval(f"{edit}.click()")
        chrome.eval(_click("event-delete"))
        assert chrome.eval(_shown("delete-dialog"))
        chrome.eval(_click("confirm-cancel"))
        assert chrome.eval(_is("event-delete"))

        httpx.post(f"{server}/api/events", json={"title": "Vet", "date": today, "time": None, "repeat": "never"})
        time.sleep(0.5)
        assert chrome.eval("document.querySelectorAll('#day-events li').length") == 1
        chrome.eval(_click("event-close"))
        _wait(chrome, "document.querySelectorAll('#day-events li').length === 2")
        assert chrome.eval(f"{ACTIVE}.classList.contains('edit')")
    finally:
        chrome.close()


@needs_chrome
def test_list_dialogs_return_focus_and_hold_reload(server, tmp_path):  # noqa: F811
    chrome = _Chrome(tmp_path / "chrome", 412, 900)
    try:
        _open(chrome, server, "/admin/lists")
        _wait(chrome, "!!document.getElementById('add-tab')")
        tabs = "document.querySelectorAll('#tabs .tab[data-id]').length"
        before = chrome.eval(tabs)

        chrome.eval(_click("add-tab"))
        assert chrome.eval(_shown("name-dialog"))
        httpx.post(f"{server}/api/lists", json={"name": "Hardware"})
        time.sleep(0.5)
        assert chrome.eval(tabs) == before
        chrome.eval(_click("name-backdrop"))
        _wait(chrome, f"{tabs} === {before + 1}")
        assert chrome.eval(_is("add-tab"))

        chrome.eval(_click("list-options"))
        chrome.eval(_click("delete-list"))
        assert chrome.eval(_shown("delete-dialog"))
        httpx.post(f"{server}/api/lists", json={"name": "Garden"})
        time.sleep(0.5)
        assert chrome.eval(tabs) == before + 1
        chrome.eval(_escape())
        _wait(chrome, f"{tabs} === {before + 2}")
        assert chrome.eval(_is("list-options"))
    finally:
        chrome.close()


@needs_chrome
def test_photo_dialog_returns_focus_and_holds_grid(server, tmp_path):  # noqa: F811
    with httpx.Client(base_url=server) as c:
        ids = []
        for color in ((200, 50, 50), (50, 200, 50)):
            files = [("files", ("a.jpg", _jpeg((800, 600), color), "image/jpeg"))]
            ids.append(c.post("/api/photos", files=files).json()["created"][0]["id"])
    chrome = _Chrome(tmp_path / "chrome", 412, 900)
    try:
        _open(chrome, server, "/admin/photos")
        tile = f"document.querySelector('#grid .tile[data-id=\"{ids[0]}\"]')"
        _wait(chrome, f"!!{tile}")
        chrome.eval(f"{tile}.marked = true; {tile}.querySelector('.tile-open').click()")
        assert chrome.eval(_shown("photo-dialog"))
        httpx.patch(f"{server}/api/photos/{ids[1]}", json={"caption": "Beach"})
        time.sleep(0.5)
        assert chrome.eval(f"{tile}.marked") is True
        chrome.eval(_escape())
        _wait(chrome, f"!{tile}.marked")
        assert chrome.eval(f"{ACTIVE} === {tile}.querySelector('.tile-open')")
    finally:
        chrome.close()
