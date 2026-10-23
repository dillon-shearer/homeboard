import io
import json
import subprocess
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from tests.chrome import _Chrome, needs_chrome, server  # noqa: F401
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


def _wait(chrome, expr, tries=200):
    value = None
    for _ in range(tries):
        value = chrome.eval(expr)
        if value:
            return value
        time.sleep(0.05)
    return value


def _mouse(chrome, kind, x, y):
    chrome.send(
        "Input.dispatchMouseEvent",
        {"type": kind, "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1},
    )


@needs_chrome
def test_admin_drag_frame_saves_focus(server, tmp_path):  # noqa: F811
    with httpx.Client(base_url=server) as c:
        _upload(c)
    chrome = _Chrome(tmp_path / "chrome", 400, 800)
    try:
        chrome.send("Page.navigate", {"url": f"{server}/admin/photos"})
        assert _wait(chrome, "!!document.querySelector('#grid .tile-open')")
        chrome.eval("document.querySelector('#grid .tile-open').click()")
        assert _wait(chrome, "!document.getElementById('photo-dialog').hidden")
        box = json.loads(
            chrome.eval(
                "(() => { const r = document.getElementById('focus-frame')"
                ".getBoundingClientRect(); const s = getComputedStyle("
                "document.getElementById('focus-frame')); return JSON.stringify("
                "{x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height,"
                " touch: s.touchAction}); })()"
            )
        )
        assert abs(box["w"] / box["h"] - 16 / 9) < 0.02, box
        assert box["touch"] == "none"
        x, y = box["x"], box["y"]
        _mouse(chrome, "mousePressed", x, y)
        for i in range(1, 6):
            _mouse(chrome, "mouseMoved", x, y + i * 8)
        _mouse(chrome, "mouseReleased", x, y + 40)
        pos = chrome.eval("document.getElementById('photo-thumb').style.objectPosition")
        assert pos and pos != "50% 50%", pos
        chrome.eval("document.getElementById('photo-save').click()")
        assert _wait(chrome, "document.getElementById('photo-dialog').hidden")
    finally:
        chrome.close()
    p = httpx.get(f"{server}/api/photos").json()[0]
    assert p["focus_x"] == 50 and p["focus_y"] < 50, p
    assert pos == f"{p['focus_x']}% {p['focus_y']}%"


@needs_chrome
def test_kiosk_cover_layer_uses_focus(server, tmp_path):  # noqa: F811
    with httpx.Client(base_url=server) as c:
        p = _upload(c)
        c.patch(f"/api/photos/{p['id']}", json={"focus_x": 20, "focus_y": 80})
        c.put("/api/settings", json={"fit": "cover"})
    chrome = _Chrome(tmp_path / "chrome", 1920, 1080)
    front = (
        "(() => { const l = document.querySelector('.layer.front');"
        " return l && l.classList.contains('cover') ? l.style.objectPosition : ''; })()"
    )
    try:
        chrome.send("Page.navigate", {"url": f"{server}/"})
        first = _wait(chrome, front)
        httpx.patch(f"{server}/api/photos/{p['id']}", json={"focus_y": 10})
        live = _wait(chrome, f"({front}) === '20% 10%' ? 'ok' : ''")
    finally:
        chrome.close()
    assert first == "20% 80%"
    assert live == "ok"
