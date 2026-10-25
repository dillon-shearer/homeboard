import io
import json
import re
import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from tests.chrome import api, api_at, needs_chrome
from tests.test_admin_logic import ADMIN_JS, needs_node


def _jpeg(size, color=(200, 50, 50)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def _upload(c, size=(800, 600)):
    files = [("files", ("a.jpg", _jpeg(size), "image/jpeg"))]
    return c.post("/api/photos", files=files).json()["created"][0]


def test_new_upload_has_centre_focus(client):
    p = _upload(client)
    assert (p["focus_x"], p["focus_y"]) == (50, 50)
    listed = client.get("/api/photos").json()[0]
    assert (listed["focus_x"], listed["focus_y"]) == (50, 50)


def test_patch_focus_round_trips_and_validates(client):
    p = _upload(client)
    r = client.patch(f"/api/photos/{p['id']}", json={"focus_x": 10, "focus_y": 90})
    assert r.status_code == 200
    listed = client.get("/api/photos").json()[0]
    assert (listed["focus_x"], listed["focus_y"]) == (10, 90)
    for bad in ({"focus_x": 101}, {"focus_y": -1}, {"focus_x": 5.5}, {"focus_x": "50"}):
        assert client.patch(f"/api/photos/{p['id']}", json=bad).status_code == 422, bad
    client.patch(f"/api/photos/{p['id']}", json={"caption": "hi"})
    listed = client.get("/api/photos").json()[0]
    assert (listed["focus_x"], listed["focus_y"], listed["caption"]) == (10, 90, "hi")


def _node(body):
    script = f"const A = require({json.dumps(str(ADMIN_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@needs_node
def test_drag_maths_four_by_three_in_sixteen_by_nine():
    r = _node(
        """
        const photo = {width: 400, height: 300};
        const frame = {width: 320, height: 180};
        const s = {x: 50, y: 50};
        console.log(JSON.stringify({
          up: A.focusFromDrag(s, 30, -500, photo, frame),
          down: A.focusFromDrag(s, -30, 500, photo, frame),
          small: A.focusFromDrag(s, 0, -15, photo, frame),
          wide: A.focusFromDrag(s, 100, 0, {width: 1600, height: 600}, frame),
        }));
        """
    )
    assert r["up"] == {"x": 50, "y": 100}
    assert r["down"] == {"x": 50, "y": 0}
    assert r["small"] == {"x": 50, "y": 75}
    assert r["wide"]["y"] == 50 and 0 <= r["wide"]["x"] < 50


def _mouse(chrome, kind, x, y):
    chrome.send(
        "Input.dispatchMouseEvent",
        {"type": kind, "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1},
    )


@needs_chrome
def test_admin_drag_frame_saves_focus(chrome, server):
    with api_at(server) as c:
        _upload(c)
    chrome.size(400, 800)
    chrome.send("Page.navigate", {"url": f"{server}/admin/photos"})
    chrome.wait("!!document.querySelector('#grid .tile-open')")
    chrome.eval("document.querySelector('#grid .tile-open').click()")
    chrome.wait("!document.getElementById('photo-dialog').hidden")
    box = json.loads(
        chrome.eval(
            "(() => { const r = document.getElementById('focus-frame')"
            ".getBoundingClientRect(); return JSON.stringify("
            "{x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height}); })()"
        )
    )
    assert abs(box["w"] / box["h"] - 16 / 9) < 0.02, box
    x, y = box["x"], box["y"]
    _mouse(chrome, "mousePressed", x, y)
    for i in range(1, 6):
        _mouse(chrome, "mouseMoved", x, y + i * 8)
    _mouse(chrome, "mouseReleased", x, y + 40)
    pos = chrome.eval("document.getElementById('photo-thumb').style.objectPosition")
    assert pos and pos != "50% 50%", pos
    chrome.eval("document.getElementById('photo-save').click()")
    chrome.wait("document.getElementById('photo-dialog').hidden")
    p = api.get(f"{server}/api/photos").json()[0]
    assert p["focus_x"] == 50 and p["focus_y"] < 50, p
    assert pos == f"{p['focus_x']}% {p['focus_y']}%"


@needs_chrome
def test_kiosk_cover_layer_uses_focus(chrome, server):
    with api_at(server) as c:
        p = _upload(c)
        c.patch(f"/api/photos/{p['id']}", json={"focus_x": 20, "focus_y": 80})
        c.put("/api/settings", json={"fit": "cover"})
    front = (
        "(() => { const l = document.querySelector('.layer.front');"
        " return l && l.classList.contains('cover') ? l.style.objectPosition : ''; })()"
    )
    chrome.send("Page.navigate", {"url": f"{server}/"})
    assert chrome.wait(front) == "20% 80%"
    api.patch(f"{server}/api/photos/{p['id']}", json={"focus_y": 10})
    chrome.wait(f"({front}) === '20% 10%'")


RECTS = """(() => {
  const cap = document.getElementById('caption');
  cap.textContent = 'Beach day';
  const r = (id) => {
    const el = document.getElementById(id);
    const b = el.getBoundingClientRect();
    return {top: b.top, bottom: b.bottom, left: b.left, right: b.right,
            font: parseFloat(getComputedStyle(el).fontSize), text: el.textContent,
            hidden: el.hidden};
  };
  return JSON.stringify({clock: r('clock'), caption: r('caption'), date: r('date')});
})()"""


def _rects(chrome, ready):
    out = None
    for _ in range(200):
        out = json.loads(chrome.eval(RECTS) or "null")
        if out and out["clock"]["text"] and ready(out):
            return out
        time.sleep(0.05)
    raise AssertionError(out)


@needs_chrome
def test_default_overlay_puts_clock_and_date_bottom_right_caption_bottom_left(chrome, server):
    chrome.size(1920, 1080)
    chrome.send("Page.navigate", {"url": f"{server}/"})
    out = _rects(chrome, lambda o: o["date"]["text"] and o["caption"]["bottom"] > 900)
    clock, date, cap = out["clock"], out["date"], out["caption"]
    edge = 0.04 * 1080
    assert abs(clock["right"] - (1920 - edge)) < 2, out
    assert date["top"] >= clock["bottom"], out
    assert abs(date["bottom"] - (1080 - edge)) < 2, out
    assert re.match(r"^[A-Z][a-z]+ \d{1,2}, \d{4}$", date["text"]), out
    assert abs(cap["left"] - edge) < 2 and abs(cap["bottom"] - (1080 - edge)) < 2, out


@needs_chrome
def test_overlay_follows_settings_live(chrome, server):
    api.put(
        f"{server}/api/settings",
        json={"clock_pos": "bottom-right", "caption_pos": "bottom-right", "clock_on_top": False},
    )
    chrome.size(1920, 1080)
    chrome.send("Page.navigate", {"url": f"{server}/"})
    shared = _rects(chrome, lambda o: o["caption"]["left"] > 960)
    assert shared["caption"]["bottom"] <= shared["clock"]["top"] + 1
    assert abs(shared["clock"]["right"] - (1920 - 0.04 * 1080)) < 2
    api.put(f"{server}/api/settings", json={"clock_pos": "top-center", "clock_size": 8})
    moved = _rects(chrome, lambda o: o["clock"]["top"] < 360)
    clock = moved["clock"]
    assert abs((clock["left"] + clock["right"]) / 2 - 960) < 10
    assert clock["font"] > shared["clock"]["font"]
    api.put(f"{server}/api/settings", json={"date_show": False})
    _rects(chrome, lambda o: o["date"]["hidden"])


ACTIVE = "document.activeElement"
TODAY = (
    "(() => { const d = new Date(); return d.getFullYear() + '-'"
    " + String(d.getMonth() + 1).padStart(2, '0') + '-'"
    " + String(d.getDate()).padStart(2, '0'); })()"
)


def _shown(dialog_id):
    return f"!document.getElementById('{dialog_id}').hidden"


def _click(el_id):
    return f"document.getElementById('{el_id}').click()"


def _escape():
    return "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))"


def _is(el_id):
    return f"{ACTIVE} === document.getElementById('{el_id}')"


def _open(chrome, base, path):
    chrome.size(412, 900)
    chrome.send("Page.navigate", {"url": f"{base}{path}"})
    chrome.wait("document.readyState === 'complete'")


def _held(chrome, event, before):
    chrome.wait(f"__test.heard('{event}') > {before}")
    chrome.run("__test.settle()")


@needs_chrome
def test_screen_dialogs_return_focus_and_hold_the_screen_list(chrome, server):
    _open(chrome, server, "/admin")
    chrome.wait("document.querySelectorAll('[data-screen-id]').length > 0")
    chrome.eval(_click("screens-edit"))
    edits = "document.querySelectorAll('[data-edit-screen]').length"
    before = chrome.eval(edits)
    chrome.eval(_click("screens-new"))
    assert chrome.eval(_shown("name-dialog"))
    api.post(f"{server}/api/screens", json={"name": "Behind"})
    chrome.run("__test.tick(10000)")
    assert chrome.eval(edits) == before
    chrome.eval(_click("name-close"))
    assert chrome.eval(_is("screens-new"))
    chrome.wait(f"{edits} === {before + 1}")

    group = "document.querySelector('[data-edit-screen]')"
    sid = chrome.eval(f"{group}.getAttribute('data-edit-screen')")
    chrome.eval(f"{group}.children[1].click()")
    assert chrome.eval(_shown("screen-dialog"))
    chrome.eval(_click("screen-delete"))
    assert chrome.eval(_shown("screen-delete-dialog"))
    chrome.eval(_escape())
    assert chrome.eval(_is("screen-delete"))
    chrome.eval(_click("screen-backdrop"))
    chrome.wait(f"{ACTIVE} === document.querySelector('[data-edit-screen=\"{sid}\"]').children[1]")


@needs_chrome
def test_calendar_dialogs_return_focus_and_hold_reload(chrome, server):
    _open(chrome, server, "/admin/calendar")
    today = chrome.eval(TODAY)
    api.post(
        f"{server}/api/events",
        json={"title": "Dentist", "date": today, "time": None, "repeat": "never"},
    )
    chrome.wait("document.querySelectorAll('#day-events li').length === 1")

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

    heard = chrome.eval("__test.heard('events_changed')")
    api.post(
        f"{server}/api/events",
        json={"title": "Vet", "date": today, "time": None, "repeat": "never"},
    )
    _held(chrome, "events_changed", heard)
    assert chrome.eval("document.querySelectorAll('#day-events li').length") == 1
    chrome.eval(_click("event-close"))
    chrome.wait("document.querySelectorAll('#day-events li').length === 2")
    assert chrome.eval(f"{ACTIVE}.classList.contains('edit')")


@needs_chrome
def test_list_dialogs_return_focus_and_hold_reload(chrome, server):
    _open(chrome, server, "/admin/lists")
    chrome.wait("!!document.getElementById('add-tab')")
    tabs = "document.querySelectorAll('#tabs .tab[data-id]').length"
    before = chrome.eval(tabs)

    chrome.eval(_click("add-tab"))
    assert chrome.eval(_shown("name-dialog"))
    heard = chrome.eval("__test.heard('lists_changed')")
    api.post(f"{server}/api/lists", json={"name": "Hardware"})
    _held(chrome, "lists_changed", heard)
    assert chrome.eval(tabs) == before
    chrome.eval(_click("name-backdrop"))
    chrome.wait(f"{tabs} === {before + 1}")
    assert chrome.eval(_is("add-tab"))

    chrome.eval(_click("list-options"))
    chrome.eval(_click("delete-list"))
    assert chrome.eval(_shown("delete-dialog"))
    heard = chrome.eval("__test.heard('lists_changed')")
    api.post(f"{server}/api/lists", json={"name": "Garden"})
    _held(chrome, "lists_changed", heard)
    assert chrome.eval(tabs) == before + 1
    chrome.eval(_escape())
    assert chrome.eval(_shown("list-dialog"))
    assert chrome.eval(_is("delete-list"))
    assert chrome.eval(tabs) == before + 1
    chrome.eval(_escape())
    chrome.wait(f"{tabs} === {before + 2}")
    assert chrome.eval(_is("list-options"))


@needs_chrome
def test_photo_dialog_returns_focus_and_holds_grid(chrome, server):
    with api_at(server) as c:
        ids = []
        for color in ((200, 50, 50), (50, 200, 50)):
            files = [("files", ("a.jpg", _jpeg((800, 600), color), "image/jpeg"))]
            ids.append(c.post("/api/photos", files=files).json()["created"][0]["id"])
    _open(chrome, server, "/admin/photos")
    tile = f"document.querySelector('#grid .tile[data-id=\"{ids[0]}\"]')"
    chrome.wait(f"!!{tile}")
    chrome.eval(f"{tile}.marked = true; {tile}.querySelector('.tile-open').click()")
    assert chrome.eval(_shown("photo-dialog"))
    heard = chrome.eval("__test.heard('photos_changed')")
    api.patch(f"{server}/api/photos/{ids[1]}", json={"caption": "Beach"})
    _held(chrome, "photos_changed", heard)
    assert chrome.eval(f"{tile}.marked") is True
    chrome.eval(_escape())
    chrome.wait(f"!{tile}.marked")
    assert chrome.eval(f"{ACTIVE} === {tile}.querySelector('.tile-open')")
