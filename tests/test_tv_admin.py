import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.chrome import _Chrome, needs_chrome, server  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent / "app"
TV_JS = ROOT / "static" / "tv_admin.js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_tv_page_loads_its_assets_in_the_admin_shell(client):
    r = client.get("/admin/tv")
    assert r.status_code == 200
    page = r.text
    assert re.search(r'<link rel="stylesheet" href="/static/win95\.css\?v=\d+">', page)
    assert re.search(r'<link rel="stylesheet" href="/static/admin\.css\?v=\d+">', page)
    assert re.search(r'<script src="/static/tv_admin\.js\?v=\d+"></script>', page)
    assert '<a class="w95-back" href="/admin">Start</a>' in page
    assert '<span class="w95-title-text">TV</span>' in page
    assert '<div class="w95-statusbar" role="status">' in page and 'id="display-state"' in page


def test_tv_page_groups_in_order(client):
    page = client.get("/admin/tv").text
    legends = re.findall(r'<h2 id="group-\w+" class="w95-legend">([^<]+)</h2>', page)
    assert legends == ["Showing on the TV", "Back to Photos after", "Weather", "Clock and caption"]
    row = re.search(r'<span id="tv-stops" class="w95-seg">(.*?)</span>', page, re.DOTALL).group(1)
    assert re.findall(r"<button[^>]*>([^<]*)</button>", row) == ["Photos", "New screen", "Edit"]
    assert 'id="screens-new" class="w95-btn" type="button" hidden' in row
    assert '<select id="channel-timeout"' in page
    assert '<label for="weather-location">Location (zip or town)</label>' in page
    assert '<input id="weather-location" class="w95-input" type="text" maxlength="40"' in page
    assert 'id="weather-name"' in page
    for el in ('id="clock-size"', 'id="caption-size"', 'name="clock_pos"', 'name="caption_pos"'):
        assert el in page
    assert '<input type="checkbox" data-key="captions">' in page
    dialog = re.search(r'<div id="name-dialog" class="modal" hidden>.*?</form>', page, re.DOTALL)
    assert dialog and 'class="w95-window dialog"' in dialog.group(0)


def test_tv_js_has_no_native_dialogs():
    js = TV_JS.read_text()
    for name in ("prompt(", "confirm(", "alert("):
        assert name not in js


TV_HARNESS = r"""
const flush = () => new Promise((r) => setImmediate(r));
async function settle() { for (let i = 0; i < 10; i++) await flush(); }
globalThis.setInterval = () => 0;
const all = {};
function make(tag, id) {
  const attrs = {};
  const classes = new Set();
  const listeners = {};
  const e = {
    tag, id, children: [], parentNode: null, hidden: false, disabled: false, value: "",
    textContent: "", innerHTML: "", type: "", className: "",
    classList: {
      contains: (c) => classes.has(c),
      toggle: (c, on) => { if (on) classes.add(c); else classes.delete(c); },
    },
    setAttribute: (k, v) => { attrs[k] = String(v); },
    getAttribute: (k) => (k in attrs ? attrs[k] : null),
    hasAttribute: (k) => k in attrs,
    addEventListener: (k, fn) => { (listeners[k] = listeners[k] || []).push(fn); },
    fire: (k) => {
      if (e.disabled) return;
      (listeners[k] || []).forEach((fn) => fn({ currentTarget: e, target: e, preventDefault() {} }));
    },
    focus() {},
    appendChild: (c) => { c.parentNode = e; e.children.push(c); return c; },
    insertBefore: (c, ref) => {
      c.parentNode = e;
      e.children.splice(e.children.indexOf(ref), 0, c);
      return c;
    },
    removeChild: (c) => { e.children.splice(e.children.indexOf(c), 1); return c; },
    querySelectorAll: (sel) => find(e, sel),
  };
  if (id) all[id] = e;
  return e;
}
function walk(e, out) {
  e.children.forEach((c) => { out.push(c); walk(c, out); });
  return out;
}
function find(root, sel) {
  const names = sel.split(",").map((s) => s.trim().slice(1, -1));
  return walk(root, []).filter((e) => names.some((n) => e.hasAttribute(n)));
}
const body = make("body");
for (const id of ["display-state", "channel-timeout", "name-dialog", "name-form", "name-title",
  "name-input", "name-error", "name-cancel", "name-close", "name-backdrop", "settings-error",
  "weather-location", "weather-name", "clock-size", "clock-size-minus", "clock-size-plus",
  "caption-size", "caption-size-minus", "caption-size-plus", "stack-row"]) {
  body.appendChild(make("x", id));
}
const row = body.appendChild(make("span", "tv-stops"));
for (const [ch, label] of [["photos", "Photos"]]) {
  const b = row.appendChild(make("button"));
  b.setAttribute("data-channel", ch);
  b.textContent = label;
}
const add = row.appendChild(make("button", "screens-new"));
add.textContent = "New screen";
add.hidden = true;
const edit = row.appendChild(make("button", "screens-edit"));
edit.textContent = "Edit";
all["name-dialog"].hidden = true;
globalThis.document = {
  getElementById: (id) => all[id] || null,
  createElement: (tag) => make(tag),
  querySelectorAll: (sel) => find(body, sel),
  addEventListener() {},
};

const server = {
  settings: { channel: "screen", screen_id: 2, channel_timeout_min: 5 },
  screens: [{ id: 4, name: "Kitchen", sort_order: 10 }, { id: 2, name: "Chores", sort_order: 20 }],
};
const calls = [];
globalThis.fetch = (url, opts) => {
  const method = (opts && opts.method) || "GET";
  const sent = opts && opts.body ? JSON.parse(opts.body) : null;
  if (method !== "GET") calls.push({ method, url, body: sent });
  let out = {};
  if (url === "/api/settings") {
    if (sent) server.settings = Object.assign({}, server.settings, sent);
    out = server.settings;
  } else if (url === "/api/status") {
    out = Object.assign({ photo_count: 3, display_connected: true }, server.settings);
  } else if (url === "/api/screens/order") {
    server.screens = sent.ids.map((id) => server.screens.find((s) => s.id === id));
    out = server.screens;
  } else if (url.startsWith("/api/screens/")) {
    const s = server.screens.find((x) => x.id === Number(url.split("/").pop()));
    s.name = sent.name;
    out = s;
  } else if (url === "/api/screens") {
    if (method === "POST") server.screens.push({ id: 9, name: sent.name, sort_order: 30 });
    out = method === "POST" ? server.screens[server.screens.length - 1] : server.screens;
  }
  return Promise.resolve({ ok: true, json: () => Promise.resolve(JSON.parse(JSON.stringify(out))) });
};

const label = (e) => e.textContent || e.getAttribute("aria-label");
const view = () => row.children.filter((c) => !c.hidden).map((c) => {
  if (c.className === "stop-edit") return { edit: c.children.map((b) => [label(b), b.disabled]) };
  return [label(c), c.getAttribute("aria-pressed")];
});
const byText = (text) => walk(row, []).find((e) => e.textContent === text);
"""


def _tv(body):
    script = f"{TV_HARNESS}\nrequire({json.dumps(str(TV_JS))});\n(async () => {{\n{body}\n}})();"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@needs_node
def test_tv_buttons_follow_screens_and_put_settings():
    r = _tv(
        """
        await settle();
        const first = view();
        byText("Kitchen").fire("click");
        await settle();
        const picked = view();
        byText("Photos").fire("click");
        await settle();
        console.log(JSON.stringify({ first, picked, calls }));
        """
    )
    assert r["first"] == [
        ["Photos", "false"],
        ["Kitchen", "false"],
        ["Chores", "true"],
        ["Edit", "false"],
    ]
    assert r["picked"][1] == ["Kitchen", "true"] and r["picked"][2] == ["Chores", "false"]
    assert r["calls"] == [
        {
            "method": "PUT",
            "url": "/api/settings",
            "body": {"channel": "screen", "screen_id": 4},
        },
        {"method": "PUT", "url": "/api/settings", "body": {"channel": "photos"}},
    ]


@needs_node
def test_tv_edit_mode_moves_and_renames_screens():
    r = _tv(
        """
        await settle();
        edit.fire("click");
        const editing = view();
        row.children[1].children[2].fire("click");
        await settle();
        const moved = view();
        byText("Chores").fire("click");
        const dialog = { open: !all["name-dialog"].hidden, value: all["name-input"].value };
        all["name-input"].value = "  Fridge ";
        all["name-form"].fire("submit");
        await settle();
        const closed = all["name-dialog"].hidden;
        edit.fire("click");
        const done = view();
        console.log(JSON.stringify({ editing, moved, dialog, closed, done, calls }));
        """
    )
    assert r["editing"] == [
        ["Photos", "false"],
        {"edit": [["Move Kitchen up", True], ["Kitchen", False], ["Move Kitchen down", False]]},
        {"edit": [["Move Chores up", False], ["Chores", False], ["Move Chores down", True]]},
        ["New screen", None],
        ["Done", "true"],
    ]
    assert [c["edit"][1][0] for c in r["moved"] if isinstance(c, dict)] == ["Chores", "Kitchen"]
    assert r["dialog"] == {"open": True, "value": "Chores"}
    assert r["closed"] is True
    assert r["done"] == [
        ["Photos", "false"],
        ["Fridge", "true"],
        ["Kitchen", "false"],
        ["Edit", "false"],
    ]
    assert r["calls"] == [
        {"method": "PUT", "url": "/api/screens/order", "body": {"ids": [2, 4]}},
        {"method": "PATCH", "url": "/api/screens/2", "body": {"name": "Fridge"}},
    ]


@needs_node
def test_tv_new_screen_button_posts_a_name():
    r = _tv(
        """
        await settle();
        const before = add.hidden;
        edit.fire("click");
        const shownInEdit = !add.hidden;
        add.fire("click");
        const dialog = { open: !all["name-dialog"].hidden, title: all["name-title"].textContent,
          value: all["name-input"].value };
        all["name-input"].value = " Hall ";
        all["name-form"].fire("submit");
        await settle();
        const names = view().filter((c) => c.edit).map((c) => c.edit[1][0]);
        console.log(JSON.stringify({ before, shownInEdit, dialog, names, calls }));
        """
    )
    assert r["before"] is True and r["shownInEdit"] is True
    assert r["dialog"] == {"open": True, "title": "New screen", "value": ""}
    assert r["names"] == ["Kitchen", "Chores", "Hall"]
    assert r["calls"] == [{"method": "POST", "url": "/api/screens", "body": {"name": "Hall"}}]


ROW = """JSON.stringify([...document.querySelectorAll('#tv-stops > *')].filter((e) => !e.hidden)
  .map((e) => e.classList.contains('stop-edit') ? e.getAttribute('data-edit-screen') : e.textContent))"""


def _wait(fn, tries=100):
    out = None
    for _ in range(tries):
        out = fn()
        if out:
            return out
        time.sleep(0.05)
    return out


def _settings(server):  # noqa: F811
    return httpx.get(f"{server}/api/settings").json()


def _open_tv(server, tmp_path):  # noqa: F811
    chrome = _Chrome(tmp_path / "chrome", 412, 900)
    chrome.send("Page.navigate", {"url": f"{server}/admin/tv"})
    return chrome


def _two_screens(server):  # noqa: F811
    httpx.post(f"{server}/api/screens", json={"name": "Chores"})
    return httpx.get(f"{server}/api/screens").json()


@needs_chrome
def test_tv_row_shows_screens_and_picks_one(server, tmp_path):  # noqa: F811
    screens = _two_screens(server)
    want = ["Photos"] + [s["name"] for s in screens] + ["Edit"]
    target = screens[-1]["id"]
    button = f"document.querySelector('[data-screen-id=\"{target}\"]')"
    chrome = _open_tv(server, tmp_path)
    try:
        row = _wait(lambda: json.loads(chrome.eval(ROW) or "[]") == want)
        chrome.eval(f"{button}.click()")
        picked = _wait(lambda: _settings(server)["screen_id"] == target)
        pressed = _wait(lambda: chrome.eval(f"{button}.getAttribute('aria-pressed')") == "true")
    finally:
        chrome.close()
    assert row and picked and pressed
    s = _settings(server)
    assert (s["channel"], s["screen_id"]) == ("screen", target)


@needs_chrome
def test_tv_edit_down_arrow_swaps_first_two_screens(server, tmp_path):  # noqa: F811
    ids = [s["id"] for s in _two_screens(server)]

    def order():
        return [s["id"] for s in httpx.get(f"{server}/api/screens").json()]

    chrome = _open_tv(server, tmp_path)
    try:
        _wait(
            lambda: chrome.eval("document.querySelectorAll('[data-screen-id]').length") == len(ids)
        )
        chrome.eval("document.getElementById('screens-edit').click()")
        row = json.loads(chrome.eval(ROW))
        chrome.eval("document.querySelector('.stop-edit .move:last-child').click()")
        _wait(lambda: order()[:2] == [ids[1], ids[0]])
    finally:
        chrome.close()
    assert row == ["Photos"] + [str(i) for i in ids] + ["New screen", "Done"]
    assert order()[:2] == [ids[1], ids[0]]


@needs_chrome
def test_tv_weather_location_saves_on_blur(server, tmp_path):  # noqa: F811
    field = "document.getElementById('weather-location')"
    chrome = _open_tv(server, tmp_path)
    try:
        _wait(lambda: chrome.eval(f"{field}.value") == "30263")
        chrome.eval(f"{field}.focus(); {field}.select();")
        chrome.send("Input.insertText", {"text": "Atlanta"})
        chrome.eval(f"{field}.blur()")
        _wait(lambda: _settings(server)["weather_location"] == "Atlanta")
        note = _wait(
            lambda: (
                chrome.eval("document.getElementById('weather-name').textContent")
                == "Looking up..."
            )
        )
    finally:
        chrome.close()
    s = _settings(server)
    assert s["weather_location"] == "Atlanta" and s["weather_lat"] is None
    assert note


@needs_chrome
def test_tv_clock_size_stepper_saves(server, tmp_path):  # noqa: F811
    before = _settings(server)["clock_size"]
    chrome = _open_tv(server, tmp_path)
    try:
        _wait(lambda: chrome.eval("document.getElementById('clock-size').value") == str(before))
        chrome.eval("document.getElementById('clock-size-plus').click()")
        _wait(lambda: _settings(server)["clock_size"] == before + 1)
    finally:
        chrome.close()
    assert _settings(server)["clock_size"] == before + 1
