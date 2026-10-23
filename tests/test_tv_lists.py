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

APP = Path(__file__).resolve().parent.parent / "app"
STATIC = APP / "static"
SCREEN_JS = STATIC / "tv_screen.js"
TV_CSS = STATIC / "tv.css"


def _node(body):
    script = f"const T = require({json.dumps(str(SCREEN_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_old_tv_pages_are_gone(client):
    assert client.get("/tv/lists").status_code == 404
    assert client.get("/tv/calendar").status_code == 404
    for path in (
        APP / "templates" / "tv_lists.html",
        APP / "templates" / "tv_calendar.html",
        STATIC / "tv_lists.js",
        STATIC / "tv_calendar.js",
    ):
        assert not path.exists(), path


def test_tv_css_hides_cursor_and_uses_vh_fonts():
    css = TV_CSS.read_text(encoding="utf-8")
    assert "cursor: none" in css
    sizes = re.findall(r"font-size:\s*([^;]+);", css)
    assert sizes
    for size in sizes:
        assert "px" not in size, size
        assert size.strip().endswith("vh"), size


def test_kiosk_swipe_layer_lets_taps_through():
    css = (STATIC / "display.css").read_text(encoding="utf-8")
    block = re.search(r"#swipe \{([^}]*)\}", css).group(1)
    assert "pointer-events: none" in block
    js = (STATIC / "display.js").read_text(encoding="utf-8")
    assert "channelEl.onload = hookFrame" in js
    assert "contentDocument" in js


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
        "console.log(JSON.stringify({order: T.sortItems(items).map(i => i.text),"
        " more: [T.moreLabel(3, 2), T.moreLabel(1, 0)]}))"
    )
    assert result["order"] == ["pin", "open a", "open b", "done pin", "done a", "done b"]
    assert result["more"] == ["+3 more, 2 done", "+1 more"]


@needs_node
def test_lists_helpers_pick_lists_per_size():
    result = _node(
        "const lists = ["
        "{id: 1, name: 'Publix', tv_open: true, items: ["
        "{text: 'a', done: false}, {text: 'b', done: true}, {text: 'c', done: false}]},"
        "{id: 2, name: 'BJs', tv_open: false, items: []},"
        "{id: 3, name: 'Chores', tv_open: true, items: [{text: 'x', done: false}]}];"
        "const ids = out => out.map(l => l.id);"
        "const w = (w, h, list_id) => ({w, h, list_id: list_id === undefined ? null : list_id});"
        "console.log(JSON.stringify({"
        " one: ids(T.shownLists(lists, w(1, 1, 2))),"
        " oneGone: ids(T.shownLists(lists, w(1, 1, 9))),"
        " oneNull: ids(T.shownLists(lists, w(1, 1))),"
        " wide: ids(T.shownLists(lists, w(2, 1))),"
        " tall: ids(T.shownLists(lists, w(1, 2))),"
        " full: ids(T.shownLists(lists, w(2, 2))),"
        " tabsFull: ids(T.tabLists(lists, w(2, 2))),"
        " tabsWide: ids(T.tabLists(lists, w(2, 1))),"
        " labels: lists.map(T.tabLabel),"
        " split: [T.splitTracks(3), T.splitTracks(0)]}))"
    )
    assert result["one"] == [2]
    assert result["oneGone"] == [] and result["oneNull"] == []
    assert result["wide"] == [1, 3] and result["tall"] == [1, 3]
    assert result["full"] == [1, 3]
    assert result["tabsFull"] == [2] and result["tabsWide"] == []
    assert result["labels"] == ["Publix 2", "BJs 0", "Chores 1"]
    assert result["split"] == ["repeat(3, minmax(0, 1fr))", "repeat(1, minmax(0, 1fr))"]


def _lists(base):
    return {lst["name"]: lst for lst in httpx.get(f"{base}/api/lists").json()}


def _screens(base):
    return {s["name"]: s for s in httpx.get(f"{base}/api/screens").json()}


def _seed_three(base):
    with httpx.Client(base_url=base) as c:
        lists = _lists(base)
        publix, bjs = lists["Publix"], lists["BJs"]
        for text in ("Paper towels", "Rotisserie chicken", "Olive oil"):
            c.post(f"/api/lists/{publix['id']}/items", json={"text": text})
        pin = c.post(f"/api/lists/{publix['id']}/items", json={"text": "Dog food"}).json()
        c.patch(f"/api/items/{pin['id']}", json={"pinned": True})
        for text in ("Water", "Rice", "Batteries", "Foil"):
            c.post(f"/api/lists/{bjs['id']}/items", json={"text": text})
        done = c.post(f"/api/lists/{bjs['id']}/items", json={"text": "Bread"}).json()
        c.patch(f"/api/items/{done['id']}", json={"done": True})
        notes = c.post("/api/lists", json={"name": "Message board", "kind": "notes"}).json()
        c.post(f"/api/lists/{notes['id']}/items", json={"text": "Plumber comes Tuesday 9am"})
        assert c.patch(f"/api/lists/{bjs['id']}", json={"tv_open": False}).status_code == 200
    return _lists(base)


STATE = """JSON.stringify((() => {
  const doc = %s;
  if (!doc || !doc.getElementById('screen')) return null;
  const box = n => { const r = n.getBoundingClientRect();
    return {x: r.left + r.width / 2, y: r.top + r.height / 2, top: r.top, bottom: r.bottom,
      left: r.left, right: r.right}; };
  const card = doc.querySelector('#screen .tv-card[data-kind="lists"]');
  return {
    title: doc.getElementById('title').textContent,
    h2: card ? card.querySelector('h2').textContent : null,
    tabs: card ? [...card.querySelectorAll('.tv-tab')].map(t => ({text: t.textContent, ...box(t)})) : [],
    cards: card ? [...card.querySelectorAll('.tv-list')].map(c => ({
      name: c.querySelector('h2').textContent, head: box(c.querySelector('h2')), ...box(c),
      notes: c.classList.contains('notes'),
      pin: !!c.querySelector('li.pin'), fits: c.querySelector('ul').scrollHeight <= c.querySelector('ul').clientHeight,
      text: c.innerText})) : [],
    condensed: card ? !!card.querySelector('.tv-lists.condensed') : false,
    msg: card && card.querySelector('.tv-msg') ? card.querySelector('.tv-msg').textContent : null,
    text: card ? card.innerText : '',
    scrollW: doc.documentElement.scrollWidth,
    scrollH: doc.documentElement.scrollHeight,
  };
})())"""
PAGE = STATE % "document"
FRAME = STATE % "document.getElementById('channel').contentDocument"


def _wait(chrome, expr, ready):
    out = None
    for _ in range(100):
        out = json.loads(chrome.eval(expr) or "null")
        if out and ready(out):
            return out
        time.sleep(0.05)
    return out


def _until(fn, ready):
    v = None
    for _ in range(100):
        v = fn()
        if ready(v):
            return v
        time.sleep(0.05)
    return v


def _click(chrome, x, y):
    for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
        chrome.send(
            "Input.dispatchMouseEvent",
            {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1},
        )


@needs_chrome
def test_notes_screen_tabs_open_and_titles_close_lists(server, tmp_path):  # noqa: F811
    lists = _seed_three(server)
    notes = _screens(server)["Notes"]
    httpx.put(f"{server}/api/settings", json={"channel": "screen", "screen_id": notes["id"]})
    bjs = lists["BJs"]["id"]
    chrome = _Chrome(tmp_path / "chrome", 1920, 1080)
    try:
        chrome.send("Page.navigate", {"url": f"{server}/tv"})
        before = _wait(chrome, PAGE, lambda o: o["title"] == "Notes" and o["cards"])
        tab = before["tabs"][0]
        _click(chrome, tab["x"], tab["y"])
        opened = _until(lambda: _lists(server)["BJs"]["tv_open"], bool)
        after = _wait(chrome, PAGE, lambda o: len(o["cards"]) == 3)
        head = next(c for c in after["cards"] if c["name"] == "Publix")["head"]
        _click(chrome, head["x"], head["y"])
        closed = _until(lambda: _lists(server)["Publix"]["tv_open"], lambda v: v is False)
        last = _wait(chrome, PAGE, lambda o: len(o["cards"]) == 2 and o["tabs"])
    finally:
        chrome.close()
    assert before["h2"] == "Lists"
    assert [c["name"] for c in before["cards"]] == ["Publix", "Message board"]
    assert [t["text"] for t in before["tabs"]] == ["BJs 4"]
    a, b = before["cards"]
    assert a["top"] == b["top"] and a["right"] < b["left"]
    assert abs((a["right"] - a["left"]) - (b["right"] - b["left"])) <= 1
    assert tab["bottom"] < a["top"]
    assert a["pin"] and b["notes"] and a["fits"] and b["fits"]
    assert before["scrollW"] <= 1920 and before["scrollH"] <= 1080
    assert opened is True and _lists(server)["BJs"]["id"] == bjs
    assert [c["name"] for c in after["cards"]] == ["Publix", "BJs", "Message board"]
    assert after["tabs"] == []
    assert "Bread" in next(c for c in after["cards"] if c["name"] == "BJs")["text"]
    assert closed is False
    assert [c["name"] for c in last["cards"]] == ["BJs", "Message board"]
    assert [t["text"] for t in last["tabs"]] == ["Publix 4"]


@needs_chrome
def test_smaller_lists_widgets_show_open_lists_or_one_list(server, tmp_path):  # noqa: F811
    lists = _seed_three(server)
    screens = _screens(server)
    notes = screens["Notes"]
    widget = notes["widgets"][0]
    assert httpx.patch(f"{server}/api/widgets/{widget['id']}", json={"h": 1}).status_code == 200
    httpx.put(f"{server}/api/settings", json={"channel": "screen", "screen_id": notes["id"]})
    chrome = _Chrome(tmp_path / "chrome", 1920, 1080)
    try:
        chrome.send("Page.navigate", {"url": f"{server}/tv"})
        wide = _wait(chrome, PAGE, lambda o: o["title"] == "Notes" and o["condensed"])
        for name in ("Publix", "Message board"):
            lid = lists[name]["id"]
            httpx.patch(f"{server}/api/lists/{lid}", json={"tv_open": False})
        empty = _wait(chrome, PAGE, lambda o: o["msg"])
        brief = screens["Daily Brief"]
        httpx.put(f"{server}/api/settings", json={"screen_id": brief["id"]})
        one = _wait(chrome, PAGE, lambda o: o["title"] == "Daily Brief" and o["h2"] == "Publix")
        lists_widget = next(w for w in brief["widgets"] if w["kind"] == "lists")
        httpx.patch(f"{server}/api/widgets/{lists_widget['id']}", json={"list_id": None})
        pick = _wait(chrome, PAGE, lambda o: o["msg"])
    finally:
        chrome.close()
    assert [c["name"] for c in wide["cards"]] == ["Publix", "Message board"]
    assert wide["tabs"] == [] and "BJs" not in wide["text"]
    assert wide["cards"][0]["top"] == wide["cards"][1]["top"]
    assert empty["msg"] == "No lists open. Open one from your phone or the Notes screen."
    assert empty["cards"] == [] and empty["tabs"] == []
    assert one["h2"] == "Publix" and "Dog food" in one["text"] and "Olive oil" in one["text"]
    assert one["tabs"] == []
    assert pick["h2"] == "Lists"
    assert pick["msg"] == "Pick a list from your phone."


TOUCH = """(() => {
  const t = document.getElementById('channel').contentDocument.querySelector('.tv-tab');
  if (!t) return null;
  const r = t.getBoundingClientRect();
  return {x: r.left + r.width / 2, y: r.top + r.height / 2, text: t.textContent};
})()"""


def _touch(chrome, points):
    chrome.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [points[0]]})
    for p in points[1:]:
        chrome.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [p]})
    chrome.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})


@needs_chrome
def test_kiosk_swipe_changes_screen_and_tap_reaches_iframe(server, tmp_path):  # noqa: F811
    lists = _seed_three(server)
    screens = _screens(server)
    cal, notes = screens["Calendar"], screens["Notes"]
    httpx.put(f"{server}/api/settings", json={"channel": "screen", "screen_id": cal["id"]})
    chrome = _Chrome(tmp_path / "chrome", 1920, 1080)
    try:
        chrome.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
        chrome.send("Page.navigate", {"url": f"{server}/"})
        _wait(chrome, FRAME, lambda o: o["title"] == "Calendar")
        _touch(chrome, [{"x": 1200 - 20 * i, "y": 540} for i in range(11)])
        swiped = _until(
            lambda: httpx.get(f"{server}/api/settings").json(),
            lambda s: s["screen_id"] == notes["id"],
        )
        _wait(chrome, FRAME, lambda o: o["title"] == "Notes" and o["tabs"])
        tab = chrome.eval(TOUCH)
        _touch(chrome, [{"x": tab["x"] + 20 * i, "y": tab["y"]} for i in range(11)])
        back = _until(
            lambda: httpx.get(f"{server}/api/settings").json(),
            lambda s: s["screen_id"] == cal["id"],
        )
        _wait(chrome, FRAME, lambda o: o["title"] == "Calendar")
        still = _lists(server)["BJs"]["tv_open"]
        _touch(chrome, [{"x": 1200 - 20 * i, "y": 540} for i in range(11)])
        _wait(chrome, FRAME, lambda o: o["title"] == "Notes" and o["tabs"])
        tab = chrome.eval(TOUCH)
        time.sleep(1)
        _touch(chrome, [{"x": tab["x"], "y": tab["y"]}])
        opened = _until(lambda: _lists(server)["BJs"]["tv_open"], bool)
        frame = _wait(chrome, FRAME, lambda o: len(o["cards"]) == 3)
        settings = httpx.get(f"{server}/api/settings").json()
    finally:
        chrome.close()
    assert lists["BJs"]["tv_open"] is False
    assert (swiped["channel"], swiped["screen_id"]) == ("screen", notes["id"])
    assert back["screen_id"] == cal["id"] and still is False
    assert tab["text"] == "BJs 4"
    assert opened is True
    assert len(frame["cards"]) == 3
    assert (settings["channel"], settings["screen_id"]) == ("screen", notes["id"])
