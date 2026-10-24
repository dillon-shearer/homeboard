import json
import time

import httpx

from tests.chrome import _Chrome, needs_chrome, needs_node, server  # noqa: F401
from tests.test_lists_page import _node, _shown, _wait


@needs_node
def test_delete_text_names_screens_that_show_the_list():
    result = _node(
        "const l = {id: 5, name: 'Shop'};"
        "const s = (name, ids) => ({name: name, widgets: ids.map(i => ({kind: 'lists', list_id: i}))});"
        "console.log(JSON.stringify(["
        "L.deleteText(l, []),"
        "L.deleteText(l, [s('Notes', [null, 6])]),"
        "L.deleteText(l, [s('Kitchen', [5])]),"
        "L.deleteText(l, [s('A', [5]), s('B', [4]), s('C', [5, 5])]),"
        "L.deleteText(l, [s('A', [5]), s('B', [5]), s('C', [5])])]))"
    )
    assert result == [
        'Delete "Shop" and everything on it?',
        'Delete "Shop" and everything on it?',
        'Delete "Shop" and everything on it? It is also removed from the TV screen "Kitchen".',
        'Delete "Shop" and everything on it? It is also removed from the TV screens "A" and "C".',
        'Delete "Shop" and everything on it? It is also removed from the TV screens "A", "B" and "C".',
    ]


@needs_chrome
def test_delete_confirm_names_screen_and_cancel_returns_to_options(server, tmp_path):  # noqa: F811
    with httpx.Client(base_url=server) as c:
        shop = c.post("/api/lists", json={"name": "Shop"}).json()
        spare = c.post("/api/screens", json={"name": "Spare"}).json()
        c.post(
            f"/api/screens/{spare['id']}/widgets",
            json={"kind": "lists", "col": 0, "row": 0, "w": 1, "h": 1, "list_id": shop["id"]},
        ).raise_for_status()
    chrome = _Chrome(tmp_path / "chrome", 390, 844)
    try:
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
        with httpx.Client(base_url=server) as c:
            for _ in range(100):
                if all(x["id"] != shop["id"] for x in c.get("/api/lists").json()):
                    break
                time.sleep(0.05)
            lists = c.get("/api/lists").json()
            screen = next(s for s in c.get("/api/screens").json() if s["id"] == spare["id"])
    finally:
        chrome.close()
    assert text == (
        'Delete "Shop" and everything on it? It is also removed from the TV screen "Spare".'
    )
    assert back == {"options": True, "confirm": False, "name": "Typed", "focus": "delete-list"}
    assert backdrop is True
    assert after == {"options": False, "confirm": False}
    assert all(x["id"] != shop["id"] for x in lists)
    assert screen["widgets"] == []
