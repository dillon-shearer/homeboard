import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import POSITIONS
from app.main import create_app
from tests.test_clock_caption_form import FORM_SETUP
from tests.test_clock_caption_form import _node as _form_node

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
    assets = re.findall(r'"(/static/[^"]+\?v=\d+)"', r.text)
    assert any("tv_admin.js" in a for a in assets)
    for url in assets:
        assert client.get(url).status_code == 200, url


def test_tv_page_groups_in_order(client):
    page = client.get("/admin/tv").text
    legends = re.findall(r'class="w95-legend">([^<]+)</h2>', page)
    assert legends == ["Back to Photos after", "Weather", "Clock, date and caption"]
    assert ">Location (zip or town)</label>" in page


def test_start_page_shows_the_tv_row_last(client):
    page = client.get("/admin").text
    legends = re.findall(r'class="w95-legend">([^<]+)</h2>', page)
    assert legends == ["Board", "Showing on the TV"]


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
    textContent: "", innerHTML: "", type: "", className: "", style: {},
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
  "caption-size", "caption-size-minus", "caption-size-plus", "stack-row", "screen-dialog",
  "date-size", "date-size-minus", "date-size-plus", "date-stack-row",
  "screen-form", "screen-name", "screen-error", "screen-layout", "screen-close", "screen-backdrop",
  "screens-hint", "screen-delete", "screen-delete-dialog", "screen-delete-text",
  "screen-confirm-delete", "screen-confirm-cancel", "screen-delete-close",
  "screen-delete-backdrop", "widget-row", "widget-add", "widget-size", "widget-list-field",
  "widget-list", "widget-remove"]) {
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
all["screen-dialog"].hidden = true;
all["screen-delete-dialog"].hidden = true;
globalThis.document = {
  getElementById: (id) => all[id] || null,
  createElement: (tag) => make(tag),
  querySelectorAll: (sel) => find(body, sel),
  addEventListener() {},
};

const server = {
  settings: { channel: "screen", screen_id: 2, channel_timeout_min: 5 },
  screens: [
    { id: 4, name: "Kitchen", sort_order: 10, widgets: [] },
    { id: 2, name: "Chores", sort_order: 20, widgets: [] },
  ],
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
    if (method === "POST") server.screens.push({ id: 9, name: sent.name, sort_order: 30, widgets: [] });
    out = method === "POST" ? server.screens[server.screens.length - 1] : server.screens;
  } else if (url === "/api/lists") {
    out = [{ id: 1, name: "Groceries" }];
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


def _node(body):
    script = f"const T = require({json.dumps(str(TV_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@needs_node
def test_js_fits_checks_the_grid_and_the_other_widgets():
    r = _node(
        """
        const top = { id: 1, col: 0, row: 0, w: 1, h: 1 };
        const tall = { id: 2, col: 1, row: 0, w: 1, h: 2 };
        console.log(JSON.stringify({
          emptyFull: T.fits([], null, 0, 0, 2, 2),
          leaves: T.fits([], null, 1, 0, 2, 1),
          bottomWide: T.fits([top], null, 0, 1, 2, 1),
          bottomTall: T.fits([top], null, 0, 1, 1, 2),
          overTop: T.fits([top], null, 0, 0, 2, 1),
          selfIgnored: T.fits([top], top, 0, 0, 2, 2),
          besideTall: T.fits([top, tall], top, 0, 0, 2, 1),
          belowTop: T.fits([top, tall], top, 0, 0, 1, 2),
          negative: T.fits([], null, -1, 0, 1, 1),
        }));
        """
    )
    assert r == {
        "emptyFull": True,
        "leaves": False,
        "bottomWide": True,
        "bottomTall": False,
        "overTop": False,
        "selfIgnored": True,
        "besideTall": False,
        "belowTop": True,
        "negative": False,
    }


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
        await settle();
        const dialog = { open: !all["screen-dialog"].hidden, value: all["screen-name"].value };
        all["screen-name"].value = "  Fridge ";
        all["screen-form"].fire("submit");
        await settle();
        const closed = all["screen-dialog"].hidden;
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
def test_weather_location_change_puts_it_and_shows_looking_up():
    r = _tv(
        """
        await settle();
        const field = all["weather-location"];
        field.value = " Atlanta ";
        field.fire("change");
        await settle();
        console.log(JSON.stringify({ calls, note: all["weather-name"].textContent }));
        """
    )
    assert r["calls"] == [
        {"method": "PUT", "url": "/api/settings", "body": {"weather_location": "Atlanta"}}
    ]
    assert r["note"] == "Looking up..."


@needs_node
def test_tv_new_screen_button_posts_a_name_and_opens_the_screen_window():
    r = _tv(
        """
        await settle();
        const before = add.hidden;
        edit.fire("click");
        const shownInEdit = !add.hidden;
        add.fire("click");
        const dialog = { open: !all["name-dialog"].hidden, value: all["name-input"].value };
        all["name-input"].value = " Hall ";
        all["name-form"].fire("submit");
        await settle();
        const names = view().filter((c) => c.edit).map((c) => c.edit[1][0]);
        const screen = { open: !all["screen-dialog"].hidden, name: all["screen-name"].value,
          nameClosed: all["name-dialog"].hidden };
        console.log(JSON.stringify({ before, shownInEdit, dialog, names, screen, calls }));
        """
    )
    assert r["before"] is True and r["shownInEdit"] is True
    assert r["dialog"] == {"open": True, "value": ""}
    assert r["names"] == ["Kitchen", "Chores", "Hall"]
    assert r["screen"] == {"open": True, "name": "Hall", "nameClosed": True}
    assert r["calls"] == [{"method": "POST", "url": "/api/screens", "body": {"name": "Hall"}}]


def test_tv_date_controls_offer_every_spot(client):
    page = client.get("/admin/tv").text
    assert re.findall(r'name="date_pos" value="([a-z-]+)"', page) == list(POSITIONS)
    for label in ("Date spot", "Date above", "Date below", "Show the date"):
        assert label in page


@needs_node
def test_tv_date_stack_row_and_date_radios_put_one_field():
    r = _form_node(
        """
        await flush();
        const rows = [$("date-stack-row").hidden];
        const pick = (key, value) => { const el = radio(key, value); el.checked = true; el.fire("change"); };
        pick("date_pos", "top-left");
        await flush();
        rows.push($("date-stack-row").hidden);
        pick("date_pos", "bottom-left");
        await flush();
        rows.push($("date-stack-row").hidden);
        pick("date_on_top", "true");
        await flush();
        pick("date_pos", "middle-right");
        await flush();
        rows.push($("date-stack-row").hidden);
        console.log(JSON.stringify({ rows, puts, size: $("date-size").value }));
        """,
        FORM_SETUP,
    )
    assert r["rows"] == [False, True, False, True]
    assert r["puts"] == [
        {"date_pos": "top-left"},
        {"date_pos": "bottom-left"},
        {"date_on_top": True},
        {"date_pos": "middle-right"},
    ]
    assert r["size"] == 3
