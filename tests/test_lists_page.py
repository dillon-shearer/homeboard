import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.chrome import api_at, needs_chrome

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
LISTS_JS = STATIC / "lists.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _node(body):
    script = f"const L = require({json.dumps(str(LISTS_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_lists_page_returns_200_with_styles(client):
    r = client.get("/admin/lists")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    for name in ("win95.css", "lists.css", "lists.js"):
        assert f"/static/{name}?v=" in r.text
    assert 'href="/admin"' in r.text


def test_lists_page_has_no_screen_word(client):
    page = client.get("/admin/lists").text
    body = page[page.index("<body") :]
    text = re.sub(r"<[^>]*>", " ", body)
    assert "screen" not in text.lower()


def test_start_lists_icon_links(client):
    page = client.get("/admin").text
    assert 'href="/admin/lists"' in page
    assert "Coming soon" not in page


def test_lists_js_uses_no_browser_dialogs():
    js = LISTS_JS.read_text()
    for banned in ("prompt(", "confirm(", "alert("):
        assert banned not in js


@needs_node
def test_sort_puts_pinned_first_and_done_last():
    result = _node(
        "const items = ["
        "{id: 1, text: 'done', done: true, pinned: false, sort_order: 10},"
        "{id: 2, text: 'plain', done: false, pinned: false, sort_order: 20},"
        "{id: 3, text: 'pinned', done: false, pinned: true, sort_order: 30}];"
        "console.log(JSON.stringify(L.sortItems(items).map(i => i.text)))"
    )
    assert result == ["pinned", "plain", "done"]


@needs_node
def test_sort_keeps_sort_order_inside_each_group():
    result = _node(
        "const items = ["
        "{id: 1, done: false, pinned: false, sort_order: 30},"
        "{id: 2, done: true, pinned: false, sort_order: 10},"
        "{id: 3, done: false, pinned: false, sort_order: 20},"
        "{id: 4, done: false, pinned: true, sort_order: 40},"
        "{id: 5, done: true, pinned: true, sort_order: 5}];"
        "console.log(JSON.stringify(L.sortItems(items).map(i => i.id)))"
    )
    assert result == [4, 3, 1, 5, 2]


@needs_node
def test_pick_tab_uses_saved_id_or_falls_back_to_first():
    result = _node(
        "const lists = [{id: 4}, {id: 9}];"
        "console.log(JSON.stringify([L.pickTab(lists, '9'), L.pickTab(lists, '77'),"
        " L.pickTab(lists, null), L.pickTab([], '9')]))"
    )
    assert result == [9, 4, 4, None]


@needs_node
def test_item_patch_sends_only_changed_fields():
    result = _node(
        "const it = {id: 1, text: 'old', done: false, pinned: false};"
        "console.log(JSON.stringify(["
        "L.itemPatch(it, 'old', false, false),"
        "L.itemPatch(it, '  old ', false, false),"
        "L.itemPatch(it, 'new', false, false),"
        "L.itemPatch(it, 'old', true, true),"
        "L.itemPatch(it, '', false, false),"
        "L.itemPatch(it, 'old', undefined, false)]))"
    )
    assert result == [{}, {}, {"text": "new"}, {"done": True, "pinned": True}, {}, {}]


@needs_node
def test_swipe_action_from_dx_dy():
    result = _node(
        "console.log(JSON.stringify(["
        "L.swipeAction(-60, 5, false),"
        "L.swipeAction(-120, -40, false),"
        "L.swipeAction(-59, 0, false),"
        "L.swipeAction(-70, 90, false),"
        "L.swipeAction(-70, -70, false),"
        "L.swipeAction(-100, 0, true),"
        "L.swipeAction(40, 5, false), L.SWIPE_PX]))"
    )
    assert result == ["delete", "delete", "none", "none", "none", "none", "close", 60]


@needs_node
def test_list_patch_sends_only_changed_fields():
    result = _node(
        "const l = {id: 1, name: 'Shop', kind: 'checklist', tv_open: true};"
        "const off = {id: 2, name: 'Off', kind: 'notes', tv_open: false};"
        "console.log(JSON.stringify(["
        "L.listPatch(l, 'Shop', 'checklist', true),"
        "L.listPatch(l, ' Shop ', 'checklist', undefined),"
        "L.listPatch(l, 'X', 'checklist', true),"
        "L.listPatch(l, 'Shop', 'notes', true),"
        "L.listPatch(l, 'Shop', 'checklist', false),"
        "L.listPatch(l, '', 'checklist', true),"
        "L.listPatch(off, 'Off', 'notes', false),"
        "L.listPatch(off, 'Off', 'notes', true)]))"
    )
    assert result == [
        {},
        {},
        {"name": "X"},
        {"kind": "notes"},
        {"tv_open": False},
        {},
        {},
        {"tv_open": True},
    ]


def _wait(chrome, expr):
    for _ in range(100):
        value = chrome.eval(expr)
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(expr)


def _row(item_id):
    return f"document.querySelector('#items .item[data-id=\"{item_id}\"]')"


def _touch(item_id, dx, dy):
    return f"""(() => {{
  const t = {_row(item_id)}.querySelector('.text');
  const r = t.getBoundingClientRect();
  const x = r.left + 100, y = r.top + 5;
  const fire = (type, px, py) => t.dispatchEvent(new PointerEvent(type, {{
    bubbles: true, pointerId: 7, pointerType: 'touch', clientX: px, clientY: py}}));
  fire('pointerdown', x, y);
  fire('pointermove', x + {dx} / 2, y + {dy} / 2);
  fire('pointermove', x + {dx}, y + {dy});
  fire('pointerup', x + {dx}, y + {dy});
  return true;
}})()"""


def _items(server, list_id, done):
    with api_at(server) as c:
        for _ in range(100):
            lists = c.get("/api/lists").json()
            items = next(x for x in lists if x["id"] == list_id)["items"]
            if done(items):
                return items
            time.sleep(0.05)
    return items


def _shown(dialog_id):
    return f"!document.getElementById('{dialog_id}').hidden"


@needs_chrome
def test_item_window_and_swipe_in_headless_chrome(chrome, server):
    with api_at(server) as c:
        shop = c.post("/api/lists", json={"name": "Shop"}).json()
        ids = [
            c.post(f"/api/lists/{shop['id']}/items", json={"text": t}).json()["id"]
            for t in ("milk", "eggs", "bread")
        ]
    chrome.size(400, 800)
    chrome.send("Page.navigate", {"url": f"{server}/admin/lists"})
    tab = f"document.querySelector('.tab[data-id=\"{shop['id']}\"]')"
    _wait(chrome, f"!!{tab}")
    chrome.eval(f"{tab}.click()")
    _wait(chrome, "document.querySelectorAll('#items .item').length === 3")

    chrome.eval(f"{_row(ids[0])}.querySelector('.text').click()")
    assert chrome.eval(_shown("item-dialog"))
    assert chrome.eval("document.getElementById('item-text').value") == "milk"
    chrome.eval(
        "document.getElementById('item-text').value = 'oat milk';"
        "document.getElementById('item-pinned').click();"
        "document.getElementById('item-save').click()"
    )
    assert not chrome.eval(_shown("item-dialog"))
    items = _items(server, shop["id"], lambda items: any(i["text"] == "oat milk" for i in items))
    first = next(i for i in items if i["id"] == ids[0])
    assert (first["text"], first["pinned"], first["done"]) == ("oat milk", True, False)
    _wait(chrome, "document.querySelector('.item.pinned .text').textContent === 'oat milk'")

    chrome.eval(f"{_row(ids[1])}.querySelector('.text').click()")
    chrome.eval("document.getElementById('item-delete').click()")
    assert chrome.eval(_shown("item-confirm"))
    assert not chrome.eval(_shown("item-edit"))
    chrome.eval("document.getElementById('item-confirm-cancel').click()")
    assert chrome.eval(_shown("item-edit"))
    chrome.eval("document.getElementById('item-delete').click()")
    chrome.eval("document.getElementById('item-confirm-delete').click()")
    _wait(chrome, f"!{_row(ids[1])}")

    chrome.eval(_touch(ids[2], 10, 80))
    assert not chrome.eval(f"{_row(ids[2])}.classList.contains('open')")
    chrome.eval(_touch(ids[2], -90, 4))
    assert chrome.eval(f"{_row(ids[2])}.classList.contains('open')")
    chrome.eval(
        "document.getElementById('item-input').dispatchEvent(new PointerEvent('pointerdown',"
        " {bubbles: true, pointerId: 8, pointerType: 'touch'}))"
    )
    assert not chrome.eval(f"{_row(ids[2])}.classList.contains('open')")

    chrome.eval(_touch(ids[2], -90, 4))
    chrome.eval(
        f"const b = {_row(ids[2])}.querySelector('.swipe-delete');"
        "b.dispatchEvent(new PointerEvent('pointerdown',"
        " {bubbles: true, pointerId: 9, pointerType: 'touch'}));"
        "b.click()"
    )
    assert chrome.eval(_shown("item-dialog") + " && " + _shown("item-confirm"))
    chrome.eval("document.getElementById('item-confirm-delete').click()")
    _wait(chrome, f"!{_row(ids[2])}")
    items = _items(server, shop["id"], lambda items: len(items) == 1)
    assert [i["id"] for i in items] == [ids[0]]


def _reopen_options(chrome, checked):
    for _ in range(100):
        chrome.eval("document.getElementById('list-options').click()")
        if chrome.eval(f"document.getElementById('list-tv-open').checked === {checked}"):
            return
        chrome.eval("document.getElementById('list-cancel').click()")
        time.sleep(0.05)
    raise AssertionError(f"tv_open checkbox never showed {checked}")


@needs_chrome
def test_list_options_window_in_headless_chrome(chrome, server):
    with api_at(server) as c:
        shop = c.post("/api/lists", json={"name": "Shop"}).json()
        c.post(f"/api/lists/{shop['id']}/items", json={"text": "milk"})
    chrome.size(400, 800)
    chrome.send("Page.navigate", {"url": f"{server}/admin/lists"})
    tab = f"document.querySelector('.tab[data-id=\"{shop['id']}\"]')"
    _wait(chrome, f"!!{tab}")
    chrome.eval(f"{tab}.click()")
    _wait(chrome, "document.querySelectorAll('#items .item').length === 1")
    assert not chrome.eval("!document.getElementById('clear-done').hidden")

    chrome.eval("document.getElementById('list-options').click()")
    assert chrome.eval(_shown("list-dialog"))
    assert chrome.eval("document.getElementById('list-title').textContent") == "Shop"
    assert chrome.eval("document.getElementById('list-screen') === null")
    assert chrome.eval("document.getElementById('list-tv-open').checked") is True
    chrome.eval(
        "document.getElementById('list-name').value = 'Food';"
        'document.querySelector(\'input[name="kind"][value="notes"]\').click();'
        "document.getElementById('list-tv-open').click();"
        "document.getElementById('list-save').click()"
    )
    assert not chrome.eval(_shown("list-dialog"))
    with api_at(server) as c:
        for _ in range(100):
            row = next(x for x in c.get("/api/lists").json() if x["id"] == shop["id"])
            if row["name"] == "Food":
                break
            time.sleep(0.05)
    assert (row["name"], row["kind"], row["tv_open"]) == ("Food", "notes", False)
    _wait(chrome, f"{tab}.textContent === 'Food'")

    _reopen_options(chrome, "false")
    chrome.eval(
        "document.getElementById('list-tv-open').click();"
        "document.getElementById('list-save').click()"
    )
    with api_at(server) as c:
        for _ in range(100):
            row = next(x for x in c.get("/api/lists").json() if x["id"] == shop["id"])
            if row["tv_open"]:
                break
            time.sleep(0.05)
    assert row["tv_open"] is True

    _reopen_options(chrome, "true")
    chrome.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    assert not chrome.eval(_shown("list-dialog"))
    chrome.eval("document.getElementById('list-options').click()")
    chrome.eval("document.getElementById('delete-list').click()")
    assert not chrome.eval(_shown("list-dialog"))
    assert chrome.eval(_shown("delete-dialog"))
    chrome.eval("document.getElementById('confirm-cancel').click()")


@needs_chrome
def test_delete_confirm_names_screen_and_cancel_returns_to_options(chrome, server):
    with api_at(server) as c:
        shop = c.post("/api/lists", json={"name": "Shop"}).json()
        spare = c.post("/api/screens", json={"name": "Spare"}).json()
        c.post(
            f"/api/screens/{spare['id']}/widgets",
            json={"kind": "lists", "col": 0, "row": 0, "w": 1, "h": 1, "list_id": shop["id"]},
        ).raise_for_status()
    chrome.size(390, 844)
    chrome.send("Page.navigate", {"url": f"{server}/admin/lists"})
    tab = f"document.querySelector('.tab[data-id=\"{shop['id']}\"]')"
    _wait(chrome, f"!!{tab}")
    chrome.eval(f"{tab}.click()")
    chrome.eval("document.getElementById('list-options').click()")
    chrome.eval("document.getElementById('list-name').value = 'Typed'")
    chrome.eval("document.getElementById('delete-list').click()")
    assert not chrome.eval(_shown("list-dialog"))
    text = _wait(
        chrome,
        "(t => t.includes('Spare') && t)(document.getElementById('delete-text').textContent)",
    )
    chrome.eval("document.getElementById('confirm-cancel').click()")
    back = json.loads(
        chrome.eval(
            "JSON.stringify({options: !document.getElementById('list-dialog').hidden,"
            " confirm: !document.getElementById('delete-dialog').hidden,"
            " name: document.getElementById('list-name').value,"
            " focus: document.activeElement.id})"
        )
    )
    chrome.eval("document.getElementById('delete-list').click()")
    chrome.eval("document.getElementById('delete-backdrop').click()")
    backdrop = chrome.eval(_shown("list-dialog"))
    chrome.eval("document.getElementById('delete-list').click()")
    chrome.eval("document.getElementById('confirm-delete').click()")
    after = json.loads(
        chrome.eval(
            "JSON.stringify({options: !document.getElementById('list-dialog').hidden,"
            " confirm: !document.getElementById('delete-dialog').hidden})"
        )
    )
    with api_at(server) as c:
        for _ in range(100):
            if all(x["id"] != shop["id"] for x in c.get("/api/lists").json()):
                break
            time.sleep(0.05)
        lists = c.get("/api/lists").json()
        screen = next(s for s in c.get("/api/screens").json() if s["id"] == spare["id"])
    assert text == (
        'Delete "Shop" and everything on it? It is also removed from the TV screen "Spare".'
    )
    assert back == {"options": True, "confirm": False, "name": "Typed", "focus": "delete-list"}
    assert backdrop is True
    assert after == {"options": False, "confirm": False}
    assert all(x["id"] != shop["id"] for x in lists)
    assert screen["widgets"] == []


MONTH_IDS = ["month-prev", "month-select", "year-select", "month-next", "month-today"]

MONTH_ROW = """JSON.stringify({
  w: innerWidth,
  box: document.querySelector('.month').getBoundingClientRect().right,
  scrollW: document.documentElement.scrollWidth,
  years: document.getElementById('year-select').options.length,
  rects: IDS.map(id => {
    const r = document.getElementById(id).getBoundingClientRect();
    return {id, top: r.top, right: r.right, width: r.width};
  }),
  months: [...document.getElementById('month-select').options].map(o => [o.value, o.text]),
  fit: ['month-select', 'year-select'].map(id => {
    const s = document.getElementById(id);
    const c = s.cloneNode(true);
    c.removeAttribute('id');
    c.style.cssText = 'position:absolute;flex:none;width:auto;min-width:0;max-width:none';
    s.parentNode.appendChild(c);
    const need = c.getBoundingClientRect().width;
    c.remove();
    return {id, need, width: s.getBoundingClientRect().width};
  }),
})""".replace("IDS", json.dumps(MONTH_IDS))

MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


@needs_chrome
def test_calendar_month_row_stays_on_one_line(chrome, server):
    sizes = [(320, 844), (360, 844), (390, 844), (412, 915)]
    for n, (touch, (width, height)) in enumerate((t, s) for t in (False, True) for s in sizes):
        chrome.size(width, height)
        chrome.touch(touch, 5)
        chrome.send("Page.navigate", {"url": f"{server}/admin/calendar?{n}"})
        row = None
        for _ in range(100):
            row = json.loads(chrome.eval(f"location.search === '?{n}' && {MONTH_ROW}") or "null")
            if row and row["years"]:
                break
            time.sleep(0.05)
        where = (width, touch)
        assert row["w"] == width, where
        assert len({r["top"] for r in row["rects"]}) == 1, (where, row["rects"])
        for r in row["rects"]:
            assert r["right"] <= width and r["right"] <= row["box"], (where, r)
        assert row["scrollW"] <= width, where
        year = next(r for r in row["rects"] if r["id"] == "year-select")
        assert year["width"] >= 50, (where, year)
        assert [v for v, _ in row["months"]] == [str(m) for m in range(1, 13)], where
        labels = [t for _, t in row["months"]]
        assert labels == ([m[:3] for m in MONTHS] if width < 390 else MONTHS), where
        for f in row["fit"]:
            assert f["width"] >= f["need"] - 0.5, (where, f)
