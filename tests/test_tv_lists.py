import base64
import json
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from PIL import Image
from websockets.sync.client import connect

from app.main import create_app

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
TV_JS = STATIC / "tv_lists.js"
TV_CSS = STATIC / "tv.css"

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def _chrome():
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    for path in CHROME_PATHS:
        if Path(path).exists():
            return path
    return None


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
needs_chrome = pytest.mark.skipif(_chrome() is None, reason="chrome not installed")


def _node(body):
    script = f"const T = require({json.dumps(str(TV_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_tv_lists_route_links_styles_through_static_url(client):
    r = client.get("/tv/lists")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    for name in ("win95.css", "tv.css", "tv_lists.js"):
        version = int((STATIC / name).stat().st_mtime)
        url = f"/static/{name}?v={version}"
        assert url in r.text, name
        assert client.get(url).status_code == 200
    assert "display.js" not in r.text


def test_tv_lists_markup_has_ids_the_script_uses(client):
    page = client.get("/tv/lists").text
    assert 'class="w95-titlebar"' in page
    assert '<span id="title" class="w95-title-text">Lists</span>' in page
    js = TV_JS.read_text(encoding="utf-8")
    ids = set(re.findall(r'getElementById\("([\w-]+)"\)', js))
    assert {"cards", "clock", "list-count", "today", "title"} <= ids
    for name in ids:
        assert f'id="{name}"' in page, name


def test_tv_lists_js_listens_for_changes():
    js = TV_JS.read_text(encoding="utf-8")
    assert 'addEventListener("lists_changed", load)' in js
    assert 'addEventListener("settings_changed", load)' in js
    assert '"/api/events"' in js
    assert '"/api/lists"' in js
    assert '"/api/screens"' in js
    assert '"/api/settings"' in js
    assert "MAX_ITEMS" not in js


def test_tv_css_hides_cursor_and_uses_vh_fonts():
    css = TV_CSS.read_text(encoding="utf-8")
    assert "cursor: none" in css
    sizes = re.findall(r"font-size:\s*([^;]+);", css)
    assert sizes
    for size in sizes:
        assert "px" not in size, size
        assert size.strip().endswith("vh"), size


@needs_node
def test_sort_items_puts_pinned_then_open_then_done_by_sort_order():
    result = _node(
        "const items = ["
        "{text: 'done b', done: true, pinned: false, sort_order: 40},"
        "{text: 'open b', done: false, pinned: false, sort_order: 30},"
        "{text: 'done pin', done: true, pinned: true, sort_order: 5},"
        "{text: 'pin', done: false, pinned: true, sort_order: 50},"
        "{text: 'open a', done: false, pinned: false, sort_order: 10},"
        "{text: 'done a', done: true, pinned: false, sort_order: 20}];"
        "console.log(JSON.stringify({same: globalThis.TvLists === T, has: 'MAX_ITEMS' in T,"
        " order: T.sortItems(items).map(i => i.text)}))"
    )
    assert result["same"] is True
    assert result["has"] is False
    assert result["order"] == ["pin", "open a", "open b", "done pin", "done a", "done b"]


@needs_node
def test_visible_lists_filters_by_screen_and_keeps_done_only_lists():
    result = _node(
        "const screens = [{id: 7, name: 'B', sort_order: 2}, {id: 3, name: 'A', sort_order: 1}];"
        "const lists = ["
        "{id: 1, name: 'Shop', screen_id: 3, items: ["
        "{id: 1, text: 'milk', done: false, pinned: false, sort_order: 10},"
        "{id: 2, text: 'eggs', done: true, pinned: false, sort_order: 5},"
        "{id: 3, text: 'dog food', done: false, pinned: true, sort_order: 30}]},"
        "{id: 2, name: 'Done', screen_id: 3, items: ["
        "{id: 4, text: 'x', done: true, pinned: true, sort_order: 10}]},"
        "{id: 3, name: 'Empty', screen_id: 3, items: []},"
        "{id: 4, name: 'Other', screen_id: 7, items: ["
        "{id: 5, text: 'y', done: false, pinned: false, sort_order: 10}]},"
        "{id: 5, name: 'Hidden', screen_id: null, items: ["
        "{id: 6, text: 'z', done: false, pinned: false, sort_order: 10}]}];"
        "const f = out => out.map(l => [l.name, l.items.map(i => i.text)]);"
        "console.log(JSON.stringify({first: f(T.visibleLists(lists, screens, null)),"
        " seven: f(T.visibleLists(lists, screens, 7)),"
        " none: f(T.visibleLists(lists, [], null)),"
        " title: T.currentScreen(screens, null).name,"
        " more: [T.moreLabel(3, 2), T.moreLabel(1, 0)]}))"
    )
    assert result["first"] == [["Shop", ["dog food", "milk", "eggs"]], ["Done", ["x"]]]
    assert result["seven"] == [["Other", ["y"]]]
    assert result["none"] == []
    assert result["title"] == "A"
    assert result["more"] == ["+3 more, 2 done", "+1 more"]


@needs_node
def test_clock_and_date_format():
    result = _node(
        "const d = new Date(2026, 9, 24, 14, 5);"
        "console.log(JSON.stringify([T.formatClock(d), T.formatDate(d),"
        " T.countLabel(1), T.countLabel(2)]))"
    )
    assert result == ["2:05 pm", "Saturday, October 24", "1 list", "2 lists"]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    port = _free_port()
    config = uvicorn.Config(
        create_app(tmp_path / "data"), host="127.0.0.1", port=port, log_level="error"
    )
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield base
    srv.should_exit = True
    thread.join(timeout=10)


def _seed(base):
    with httpx.Client(base_url=base) as c:
        publix = c.post("/api/lists", json={"name": "Publix"}).json()
        for text in ("Paper towels", "Rotisserie chicken", "Olive oil"):
            c.post(f"/api/lists/{publix['id']}/items", json={"text": text})
        pin = c.post(f"/api/lists/{publix['id']}/items", json={"text": "Dog food"}).json()
        c.patch(f"/api/items/{pin['id']}", json={"pinned": True})
        notes = c.post("/api/lists", json={"name": "Message board", "kind": "notes"}).json()
        for text in ("Plumber comes Tuesday 9am", "Water the plants Thursday"):
            c.post(f"/api/lists/{notes['id']}/items", json={"text": text})
        hidden = c.post("/api/lists", json={"name": "Secret"}).json()
        c.post(f"/api/lists/{hidden['id']}/items", json={"text": "Hidden item"})
        c.patch(f"/api/lists/{hidden['id']}", json={"show_on_tv": False})


class _Chrome:
    def __init__(self, profile, width, height):
        self.proc = subprocess.Popen(
            [
                _chrome(),
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--remote-debugging-port=0",
                f"--user-data-dir={profile}",
                f"--window-size={width},{height}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        port_file = Path(profile) / "DevToolsActivePort"
        for _ in range(200):
            if port_file.exists() and port_file.read_text().strip():
                break
            time.sleep(0.05)
        port = port_file.read_text().split()[0]
        pages = httpx.get(f"http://127.0.0.1:{port}/json/list").json()
        url = next(p["webSocketDebuggerUrl"] for p in pages if p["type"] == "page")
        self.ws = connect(url, max_size=None)
        self.next_id = 0
        self.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
        )

    def send(self, method, params=None):
        self.next_id += 1
        self.ws.send(json.dumps({"id": self.next_id, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv(timeout=30))
            if msg.get("id") == self.next_id:
                return msg.get("result", {})

    def eval(self, expr):
        return self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})[
            "result"
        ].get("value")

    def close(self):
        self.ws.close()
        self.proc.kill()
        self.proc.wait(timeout=10)


LAYOUT = """JSON.stringify({
  cards: [...document.querySelectorAll('#cards .tv-card')].map(c => {
    const r = c.getBoundingClientRect();
    return {html: c.outerHTML, top: r.top, left: r.left, bottom: r.bottom};
  }),
  body: document.body.innerText,
  count: document.getElementById('list-count').textContent,
  scrollW: document.documentElement.scrollWidth,
  scrollH: document.documentElement.scrollHeight,
  w: innerWidth,
  h: innerHeight,
})"""


@needs_chrome
@pytest.mark.parametrize("width,height", [(1920, 1080), (1280, 800)])
def test_headless_screenshot_shows_two_cards(server, tmp_path, width, height):
    _seed(server)
    chrome = _Chrome(tmp_path / "chrome", width, height)
    try:
        chrome.send("Page.navigate", {"url": f"{server}/tv/lists"})
        layout = None
        for _ in range(100):
            layout = json.loads(chrome.eval(LAYOUT) or "null")
            if layout and layout["cards"]:
                break
            time.sleep(0.05)
        png = base64.b64decode(chrome.send("Page.captureScreenshot", {"format": "png"})["data"])
    finally:
        chrome.close()
    shot = tmp_path / f"tv-lists-{width}x{height}.png"
    shot.write_bytes(png)

    cards = layout["cards"]
    assert len(cards) == 2, layout
    assert "Publix" in cards[0]["html"] and "Message board" in cards[1]["html"]
    assert re.search(r'<ul><li class="pin">Dog food</li>', cards[0]["html"])
    assert cards[0]["top"] == cards[1]["top"]
    assert cards[0]["left"] < cards[1]["left"]
    assert "Secret" not in layout["body"] and "Hidden item" not in layout["body"]
    assert layout["count"] == "2 lists"
    assert layout["scrollW"] <= layout["w"] and layout["scrollH"] <= layout["h"]
    assert max(c["bottom"] for c in cards) < height

    img = Image.open(shot).convert("RGB")
    assert img.size == (width, height)
    teal = (0, 128, 128)
    for y in range(0, height, max(1, height // 50)):
        assert img.getpixel((width - 2, y)) == teal, ("right edge", y)
    for x in range(0, width, max(1, width // 50)):
        assert img.getpixel((x, height - 2)) == teal, ("bottom edge", x)
    yellow = [
        (x, y)
        for x in range(0, width, 4)
        for y in range(0, height, 4)
        if img.getpixel((x, y)) == (255, 255, 192)
    ]
    assert yellow
    assert max(x for x, _ in yellow) < width // 2


def _seed_screen(base):
    with httpx.Client(base_url=base) as c:
        screen = c.post("/api/screens", json={"name": "To dos"}).json()
        big = c.post("/api/lists", json={"name": "Big"}).json()
        c.patch(f"/api/lists/{big['id']}", json={"screen_id": screen["id"]})
        for n in range(40):
            c.post(f"/api/lists/{big['id']}/items", json={"text": f"Item {n + 1}"})
        small = c.post("/api/lists", json={"name": "Small"}).json()
        c.patch(f"/api/lists/{small['id']}", json={"screen_id": screen["id"]})
        c.post(f"/api/lists/{small['id']}/items", json={"text": "Open one"})
        done = c.post(f"/api/lists/{small['id']}/items", json={"text": "Done one"}).json()
        c.patch(f"/api/items/{done['id']}", json={"done": True})
        other = c.post("/api/lists", json={"name": "Elsewhere"}).json()
        c.post(f"/api/lists/{other['id']}/items", json={"text": "Not here"})
        r = c.put("/api/settings", json={"lists_screen_id": screen["id"]})
        assert r.status_code == 200


FIT = """JSON.stringify((() => {
  const title = document.getElementById('title').textContent;
  const cards = [...document.querySelectorAll('#cards .tv-card')];
  const big = cards.find(c => c.querySelector('h2').textContent === 'Big');
  const small = cards.find(c => c.querySelector('h2').textContent === 'Small');
  if (!big || !small) return {title, ready: false};
  const ul = big.querySelector('ul');
  const more = big.querySelector('.more');
  const hiddenRows = [...ul.children].filter(li => li.hidden);
  const shownRows = [...ul.children].filter(li => !li.hidden).length;
  const result = {
    title, ready: true,
    names: cards.map(c => c.querySelector('h2').textContent),
    bottom: big.getBoundingClientRect().bottom,
    h: innerHeight,
    more: more ? more.textContent : null,
    hidden: hiddenRows.length,
    shown: shownRows,
    fits: ul.scrollHeight <= ul.clientHeight,
  };
  if (hiddenRows.length) {
    hiddenRows[0].hidden = false;
    result.overflowsWithOneMore = ul.scrollHeight > ul.clientHeight;
    hiddenRows[0].hidden = true;
  }
  const done = [...small.querySelectorAll('li')].find(li => li.textContent === 'Done one');
  result.doneClass = done ? done.className : null;
  result.doneDecoration = done ? getComputedStyle(done).textDecorationLine : null;
  result.doneTick = done ? !!done.querySelector('.box.tick') : false;
  return result;
})())"""


@needs_chrome
def test_tv_lists_fits_long_list_shows_done_and_screen_name(server, tmp_path):
    _seed_screen(server)
    chrome = _Chrome(tmp_path / "chrome", 1920, 1080)
    try:
        chrome.send("Page.navigate", {"url": f"{server}/tv/lists"})
        fit = None
        for _ in range(100):
            fit = json.loads(chrome.eval(FIT) or "null")
            if fit and fit["ready"] and fit["title"] == "To dos":
                break
            time.sleep(0.05)
    finally:
        chrome.close()
    assert fit["title"] == "To dos", fit
    assert fit["names"] == ["Big", "Small"]
    assert fit["bottom"] <= fit["h"] == 1080
    assert fit["fits"] is True
    assert fit["hidden"] > 0 and fit["shown"] > 5
    assert fit["hidden"] + fit["shown"] == 40
    assert fit["more"] == f"+{fit['hidden']} more"
    assert fit["overflowsWithOneMore"] is True
    assert fit["doneClass"] == "done"
    assert "line-through" in fit["doneDecoration"]
    assert fit["doneTick"] is True
