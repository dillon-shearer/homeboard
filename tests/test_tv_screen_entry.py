import json
import re
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.chrome import _Chrome, needs_chrome, server  # noqa: F401

HINT = "Tap Edit to add, rename, lay out or delete screens."

SHOWN = """JSON.stringify({
  row: [...document.querySelectorAll('#tv-stops > *')].filter((e) => !e.hidden)
    .map((e) => e.textContent),
  hint: !document.getElementById('screens-hint').hidden
})"""

BOXES = """JSON.stringify({
  row: (() => { const r = document.getElementById('tv-stops').getBoundingClientRect();
    return [r.left, r.width]; })(),
  screens: [...document.querySelectorAll('#tv-stops .stop-edit')].map((e) => {
    const r = e.getBoundingClientRect(); return [r.left, r.top, r.width, r.bottom]; }),
  add: document.getElementById('screens-new').getBoundingClientRect().top,
  done: document.getElementById('screens-edit').getBoundingClientRect().top,
  hint: !document.getElementById('screens-hint').hidden
})"""


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def _wait(fn, tries=100):
    out = None
    for _ in range(tries):
        out = fn()
        if out:
            return out
        time.sleep(0.05)
    return out


def test_start_page_has_the_edit_hint_and_no_screen_cancel(client):
    page = client.get("/admin").text
    assert f'<p id="screens-hint" class="w95-note">{HINT}</p>' in page
    assert 'id="screen-cancel"' not in page
    assert re.search(r'<button type="submit" id="screen-save"[^>]*>Done</button>', page)


@needs_chrome
def test_no_screens_shows_new_screen_without_edit(server, tmp_path):  # noqa: F811
    for s in httpx.get(f"{server}/api/screens").json():
        httpx.delete(f"{server}/api/screens/{s['id']}")
    assert httpx.get(f"{server}/api/screens").json() == []
    chrome = _Chrome(tmp_path / "chrome", 390, 900)
    chrome.send("Page.navigate", {"url": f"{server}/admin"})
    try:
        want = {"row": ["Photos", "New screen", "Edit"], "hint": True}
        shown = _wait(lambda: json.loads(chrome.eval(SHOWN) or "{}") == want)
    finally:
        chrome.close()
    assert shown


@needs_chrome
def test_edit_mode_puts_each_screen_on_its_own_row_at_390(server, tmp_path):  # noqa: F811
    names = [s["name"] for s in httpx.get(f"{server}/api/screens").json()]
    chrome = _Chrome(tmp_path / "chrome", 390, 900)
    chrome.send("Page.navigate", {"url": f"{server}/admin"})
    try:
        want = {"row": ["Photos", *names, "Edit"], "hint": True}
        before = _wait(lambda: json.loads(chrome.eval(SHOWN) or "{}") == want)
        chrome.eval("document.getElementById('screens-edit').click()")
        boxes = json.loads(chrome.eval(BOXES))
    finally:
        chrome.close()
    assert before
    left, width = boxes["row"]
    tops = [s[1] for s in boxes["screens"]]
    assert len(tops) == len(names) > 1 and tops == sorted(set(tops))
    for s in boxes["screens"]:
        assert abs(s[0] - left) < 1 and abs(s[2] - width) < 1
    assert boxes["add"] == boxes["done"]
    assert boxes["add"] >= boxes["screens"][-1][3] - 1
    assert boxes["hint"] is False
