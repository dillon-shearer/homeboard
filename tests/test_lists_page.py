import json
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_tv_lists import _Chrome, _free_port, needs_chrome

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"
LISTS_JS = STATIC / "lists.js"
LISTS_CSS = STATIC / "lists.css"

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
    assert "/static/win95.css?v=" in r.text
    assert "/static/lists.css?v=" in r.text
    assert "/static/lists.js?v=" in r.text
    assert '<a class="w95-back" href="/admin">Start</a>' in r.text
    assert '<span class="w95-title-text">Lists</span>' in r.text


def test_lists_page_has_ids_lists_js_uses(client):
    page = client.get("/admin/lists").text
    js = LISTS_JS.read_text()
    ids = set(re.findall(r'\$\("([\w-]+)"\)', js))
    assert {"tabs", "items", "item-input", "list-screen", "toast", "toast-text"} <= ids
    for name in ids:
        assert f'id="{name}"' in page, name
    assert "show_on_tv" not in js and "show-on-tv" not in page
    panel = page[page.index('<div id="panel"') : page.index('<p id="lists-empty"')]
    assert 'class="form"' not in panel
    after = panel[panel.index('<ul id="items"') :]
    assert re.findall(r"<(\w+)[^>]* id=\"([\w-]+)\"", after) == [
        ("ul", "items"),
        ("p", "items-empty"),
        ("button", "clear-done"),
    ]
    assert re.search(r'<button type="button" id="clear-done"[^>]*hidden>Clear done</button>', panel)
    add = re.search(r'<form id="add-form".*?</form>', panel, re.DOTALL).group(0)
    assert re.search(r'<button type="button" id="list-options"[^>]*aria-label="List options"', add)
    assert "<svg" in add


def test_list_options_dialog_markup(client):
    page = client.get("/admin/lists").text
    start = page.index('<div id="list-dialog" class="modal" hidden>')
    html = page[start : page.index('<div id="name-dialog"', start)]
    assert re.search(r'<input id="list-name"[^>]*maxlength="60"', html)
    assert 'name="kind" value="checklist"> Checklist' in html
    assert 'name="kind" value="notes"> Notes' in html
    select = re.search(r'<select id="list-screen".*?</select>', html, re.DOTALL).group(0)
    options = re.findall(r"<option[^>]*>([^<]*)</option>", select)
    assert options[0] == "Not shown" and options[-1] == "New screen..."
    for label in ("Save", "Cancel", "Delete list"):
        assert f">{label}</button>" in html
    assert 'type="submit" id="list-save" class="w95-btn default">Save<' in html
    assert 'id="delete-list" class="w95-btn del">Delete list<' in html


def test_screen_word_only_in_select_and_new_screen_title(client):
    page = client.get("/admin/lists").text
    body = page[page.index("<body") :]
    body = re.sub(r'<select id="list-screen".*?</select>', "", body, flags=re.DOTALL)
    text = re.sub(r"<[^>]*>", " ", body)
    assert "screen" not in text.lower()
    js = LISTS_JS.read_text()
    messages = re.findall(r'(?:showError|fail)\("([^"]*)"', js)
    assert messages and not [m for m in messages if "screen" in m.lower()]
    assert 'textContent = mode === "screen" ? "New screen" : "New list"' in js


def test_start_lists_icon_links(client):
    page = client.get("/admin").text
    icon = re.search(r'<a class="w95-icon" href="/admin/lists">.*?</a>', page, re.DOTALL)
    assert icon
    assert '<span class="w95-icon-label">Lists</span>' in icon.group(0)
    assert "Coming soon" not in icon.group(0)
    assert "Coming soon" not in page


def test_lists_js_listens_for_changes_and_uses_no_browser_dialogs():
    js = LISTS_JS.read_text()
    assert 'addEventListener("lists_changed"' in js
    assert '"/api/events"' in js
    for banned in ("prompt(", "confirm(", "alert("):
        assert banned not in js


@needs_node
def test_loads_under_node_and_sets_window_lists():
    result = _node(
        "console.log(JSON.stringify({same: globalThis.Lists === L, key: L.TAB_KEY,"
        " fns: Object.keys(L).filter(k => typeof L[k] === 'function')}))"
    )
    assert result["same"] is True
    assert result["key"] == "homeboard.lists.tab"
    assert {"sortItems", "pickTab", "rank", "moveId", "countLabel"} <= set(result["fns"])


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


def test_item_dialog_markup(client):
    page = client.get("/admin/lists").text
    start = page.index('<div id="item-dialog" class="modal" hidden>')
    html = page[start : page.index("<script", start)]
    assert '<span id="item-title" class="w95-title-text">Item</span>' in html
    assert re.search(r'<input id="item-text"[^>]*maxlength="200"[^>]*enterkeyhint="done"', html)
    assert '<input id="item-done" type="checkbox"><span>Done</span>' in html
    assert '<input id="item-pinned" type="checkbox"><span>Pinned</span>' in html
    edit = re.search(r'<form id="item-edit".*?</form>', html, re.DOTALL).group(0)
    for label in ("Save", "Cancel", "Delete"):
        assert f">{label}</button>" in edit
    assert 'type="submit" id="item-save" class="w95-btn default">Save<' in edit
    confirm = html[html.index('<div id="item-confirm" class="w95-body" hidden>') :]
    assert "Delete this item?" in confirm
    assert 'class="confirm-icon"' in confirm
    assert 'id="item-confirm-delete" class="w95-btn default">Delete</button>' in confirm
    assert 'id="item-confirm-cancel" class="w95-btn">Cancel</button>' in confirm


def test_inline_edit_is_gone_and_text_opens_the_item_window():
    js = LISTS_JS.read_text()
    assert "startEdit" not in js and "finishEdit" not in js
    assert "text-edit" not in js
    click = re.search(r"function onItemsClick\(e\) \{.*?\n  \}\n", js, re.DOTALL).group(0)
    assert 'closest(".text")) {\n      openItem(item, false);' in click


def test_swipe_delete_button_is_red():
    css = LISTS_CSS.read_text()
    rule = re.search(r"\.swipe-delete \{[^}]*\}", css).group(0)
    assert "background: #c00000" in rule
    assert "color: #fff" in rule
    assert "font-weight: bold" in rule
    assert re.search(r"\.item \{[^}]*touch-action: pan-y", css)


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
        "const l = {id: 1, name: 'Shop', kind: 'checklist', screen_id: 3};"
        "const off = {id: 2, name: 'Off', kind: 'notes', screen_id: null};"
        "console.log(JSON.stringify(["
        "L.listPatch(l, 'Shop', 'checklist', 3),"
        "L.listPatch(l, ' Shop ', 'checklist', undefined),"
        "L.listPatch(l, 'X', 'checklist', 3),"
        "L.listPatch(l, 'Shop', 'notes', 3),"
        "L.listPatch(l, 'Shop', 'checklist', null),"
        "L.listPatch(l, '', 'checklist', 3),"
        "L.listPatch(off, 'Off', 'notes', null),"
        "L.listPatch(off, 'Off', 'notes', 5)]))"
    )
    assert result == [
        {},
        {},
        {"name": "X"},
        {"kind": "notes"},
        {"screen_id": None},
        {},
        {},
        {"screen_id": 5},
    ]


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
    with httpx.Client(base_url=server) as c:
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
def test_item_window_and_swipe_in_headless_chrome(server, tmp_path):
    with httpx.Client(base_url=server) as c:
        shop = c.post("/api/lists", json={"name": "Shop"}).json()
        ids = [
            c.post(f"/api/lists/{shop['id']}/items", json={"text": t}).json()["id"]
            for t in ("milk", "eggs", "bread")
        ]
    chrome = _Chrome(tmp_path / "chrome", 400, 800)
    try:
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
        items = _items(
            server, shop["id"], lambda items: any(i["text"] == "oat milk" for i in items)
        )
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
    finally:
        chrome.close()


@needs_chrome
def test_list_options_window_in_headless_chrome(server, tmp_path):
    with httpx.Client(base_url=server) as c:
        shop = c.post("/api/lists", json={"name": "Shop"}).json()
        c.post(f"/api/lists/{shop['id']}/items", json={"text": "milk"})
    chrome = _Chrome(tmp_path / "chrome", 400, 800)
    try:
        chrome.send("Page.navigate", {"url": f"{server}/admin/lists"})
        tab = f"document.querySelector('.tab[data-id=\"{shop['id']}\"]')"
        _wait(chrome, f"!!{tab}")
        chrome.eval(f"{tab}.click()")
        _wait(chrome, "document.querySelectorAll('#items .item').length === 1")
        assert not chrome.eval("!document.getElementById('clear-done').hidden")

        chrome.eval("document.getElementById('list-options').click()")
        assert chrome.eval(_shown("list-dialog"))
        assert chrome.eval("document.getElementById('list-title').textContent") == "Shop"
        _wait(chrome, "!document.getElementById('list-screen').disabled")
        chrome.eval(
            "const s = document.getElementById('list-screen');"
            "s.value = 'new'; s.dispatchEvent(new Event('change'))"
        )
        assert chrome.eval(_shown("name-dialog"))
        assert chrome.eval("document.getElementById('name-title').textContent") == "New screen"
        chrome.eval("document.getElementById('name-cancel').click()")
        assert chrome.eval("document.getElementById('list-screen').value") == str(shop["screen_id"])
        chrome.eval(
            "const s2 = document.getElementById('list-screen');"
            "s2.value = 'new'; s2.dispatchEvent(new Event('change'));"
            "document.getElementById('name-input').value = 'Notes';"
            "document.getElementById('name-save').click()"
        )
        _wait(
            chrome,
            "document.getElementById('list-screen').selectedOptions[0].textContent === 'Notes'",
        )
        chrome.eval(
            "document.getElementById('list-name').value = 'Food';"
            'document.querySelector(\'input[name="kind"][value="notes"]\').click();'
            "document.getElementById('list-save').click()"
        )
        assert not chrome.eval(_shown("list-dialog"))
        with httpx.Client(base_url=server) as c:
            for _ in range(100):
                row = next(x for x in c.get("/api/lists").json() if x["id"] == shop["id"])
                if row["name"] == "Food":
                    break
                time.sleep(0.05)
            screens = c.get("/api/screens").json()
        notes = next(s for s in screens if s["name"] == "Notes")
        assert (row["name"], row["kind"], row["screen_id"]) == ("Food", "notes", notes["id"])
        _wait(chrome, f"{tab}.textContent === 'Food'")

        chrome.eval("document.getElementById('list-options').click()")
        _wait(chrome, "!document.getElementById('list-screen').disabled")
        assert chrome.eval("document.getElementById('list-screen').value") == str(notes["id"])
        chrome.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
        assert not chrome.eval(_shown("list-dialog"))
        chrome.eval("document.getElementById('list-options').click()")
        chrome.eval("document.getElementById('delete-list').click()")
        assert not chrome.eval(_shown("list-dialog"))
        assert chrome.eval(_shown("delete-dialog"))
        chrome.eval("document.getElementById('confirm-cancel').click()")
    finally:
        chrome.close()
