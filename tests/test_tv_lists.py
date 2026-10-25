import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.chrome import needs_node

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
