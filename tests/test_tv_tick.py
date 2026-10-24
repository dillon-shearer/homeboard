import json
import subprocess
import time
from pathlib import Path

import httpx

from tests.chrome import _Chrome, needs_chrome, needs_node, server  # noqa: F401

SCREEN_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "tv_screen.js"
WIDTH, HEIGHT = 1024, 600

FAKE_DOM = """
const node = tag => ({tag, attrs: {}, children: [], className: '', textContent: '',
  setAttribute(k, v) { this.attrs[k] = String(v); },
  appendChild(c) { this.children.push(c); return c; }});
globalThis.document = {createElement: node, createTextNode: t => ({text: t})};
const rows = list => T.itemList(list).children.map(li => ({
  text: li.children.map(c => c.text).filter(Boolean).join(''), item: li.attrs['data-item'] || null}));
"""


@needs_node
def test_item_list_marks_tickable_rows_only():
    items = (
        "[{id: 7, text: 'Milk', done: false, pinned: false, sort_order: 1},"
        " {id: 8, text: 'Eggs', done: true, pinned: false, sort_order: 2},"
        " {id: 9, text: 'Dog food', done: false, pinned: true, sort_order: 3}]"
    )
    script = (
        f"const T = require({json.dumps(str(SCREEN_JS))});\n{FAKE_DOM}"
        f"const items = {items};\n"
        "console.log(JSON.stringify({"
        " grocery: rows({kind: 'grocery', items}),"
        " todo: rows({kind: 'todo', items}),"
        " checklist: rows({kind: 'checklist', items}),"
        " notes: rows({kind: 'notes', items}),"
        " empty: rows({kind: 'checklist', items: []})}))"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    result = json.loads(out.stdout)
    for kind in ("grocery", "todo", "checklist"):
        assert sorted(r["item"] for r in result[kind]) == ["7", "8", "9"], kind
    assert [r["item"] for r in result["notes"]] == [None, None, None]
    assert result["empty"] == [{"text": "", "item": None}]


ROW = """JSON.stringify((() => {
  const li = document.querySelector('#screen li[data-item="%d"]');
  if (!li || li.hidden) return null;
  const r = li.getBoundingClientRect();
  return {x: r.left + r.width / 2, y: r.top + r.height / 2, done: li.classList.contains('done')};
})())"""

HEIGHTS = """JSON.stringify((() => {
  const rows = [...document.querySelectorAll('#screen [data-item]')].filter(n => !n.hidden);
  return {condensed: !!document.querySelector('.tv-lists.condensed'),
    notes: document.querySelectorAll('.tv-card.notes li[data-item]').length,
    heights: rows.map(n => n.getBoundingClientRect().height)};
})())"""


def _done(base, item_id):
    for lst in httpx.get(f"{base}/api/lists").json():
        for item in lst["items"]:
            if item["id"] == item_id:
                return item["done"]
    return None


def _until(fn, ready, tries=100):
    v = None
    for _ in range(tries):
        v = fn()
        if ready(v):
            return v
        time.sleep(0.05)
    return v


def _touch(chrome, points):
    chrome.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [points[0]]})
    for p in points[1:]:
        chrome.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [p]})
    chrome.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})


@needs_chrome
def test_tapping_tv_rows_ticks_items_and_swipes_do_not(server, tmp_path):  # noqa: F811
    with httpx.Client(base_url=server) as c:
        lists = {lst["name"]: lst for lst in c.get("/api/lists").json()}
        publix = lists["Publix"]["id"]
        item = c.post(f"/api/lists/{publix}/items", json={"text": "Paper towels"}).json()
        for text in ("Olive oil", "Rice"):
            c.post(f"/api/lists/{publix}/items", json={"text": text})
        notes = c.post("/api/lists", json={"name": "Message board", "kind": "notes"}).json()
        c.post(f"/api/lists/{notes['id']}/items", json={"text": "Plumber Tuesday"})
        screen = next(s for s in c.get("/api/screens").json() if s["name"] == "Notes")
        widget = screen["widgets"][0]
        assert (widget["kind"], widget["w"], widget["h"]) == ("lists", 2, 2)
        c.put("/api/settings", json={"channel": "screen", "screen_id": screen["id"]})
    row = ROW % item["id"]
    chrome = _Chrome(tmp_path / "chrome", WIDTH, HEIGHT)
    try:
        chrome.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
        chrome.send("Page.navigate", {"url": f"{server}/tv"})
        first = _until(lambda: json.loads(chrome.eval(row) or "null"), bool)
        full = json.loads(chrome.eval(HEIGHTS))
        _touch(chrome, [{"x": first["x"], "y": first["y"]}])
        start = time.monotonic()
        ticked = _until(lambda: json.loads(chrome.eval(row) or "null"), lambda r: r and r["done"])
        ticked_s = time.monotonic() - start
        api_on = _until(lambda: _done(server, item["id"]), bool)
        time.sleep(0.3)
        again = json.loads(chrome.eval(row))
        _touch(chrome, [{"x": again["x"], "y": again["y"]}])
        unticked = _until(
            lambda: json.loads(chrome.eval(row) or "null"), lambda r: r and not r["done"]
        )
        api_off = _until(lambda: _done(server, item["id"]), lambda v: v is False)
        time.sleep(0.3)
        here = json.loads(chrome.eval(row))
        _touch(chrome, [{"x": here["x"] + 15 * i, "y": here["y"]} for i in range(11)])
        time.sleep(1)
        after_swipe = json.loads(chrome.eval(row))
        api_swipe = _done(server, item["id"])
        assert httpx.patch(f"{server}/api/widgets/{widget['id']}", json={"h": 1}).status_code == 200
        small = _until(lambda: json.loads(chrome.eval(HEIGHTS)), lambda o: o["condensed"])
    finally:
        chrome.close()
    assert ticked and ticked["done"] and ticked_s < 1
    assert api_on is True
    assert unticked and not unticked["done"] and api_off is False
    assert after_swipe["done"] is False and api_swipe is False
    for out in (full, small):
        assert out["heights"] and out["notes"] == 0
        assert all(h >= 48 for h in out["heights"]), out
    assert small["condensed"]
