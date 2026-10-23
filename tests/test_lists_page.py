import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

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
    assert "/static/win95.css?v=" in r.text
    assert "/static/lists.css?v=" in r.text
    assert "/static/lists.js?v=" in r.text
    assert '<a class="w95-back" href="/admin">Start</a>' in r.text
    assert '<span class="w95-title-text">Lists</span>' in r.text


def test_lists_page_has_ids_lists_js_uses(client):
    page = client.get("/admin/lists").text
    js = LISTS_JS.read_text()
    ids = set(re.findall(r'\$\("([\w-]+)"\)', js))
    assert {"tabs", "items", "item-input", "show-on-tv", "toast", "toast-text"} <= ids
    for name in ids:
        assert f'id="{name}"' in page, name
    assert re.search(
        r'<input id="show-on-tv" type="checkbox"><span>Show this list on the TV</span>', page
    )
    assert 'name="kind" value="checklist"' in page and 'name="kind" value="notes"' in page
    for label in ("Clear done", "Rename", "Delete list"):
        assert f">{label}</button>" in page


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
