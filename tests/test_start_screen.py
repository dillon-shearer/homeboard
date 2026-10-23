import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent / "app"
WIN95 = (ROOT / "static" / "win95.css").read_text()
PAGES = ["/admin", "/admin/photos", "/admin/lists", "/admin/calendar", "/admin/about"]
TUNNEL = {"X-Homeboard-Tunnel": "1", "CF-Connecting-IP": "203.0.113.7"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


@pytest.fixture
def tunnel_client(tmp_path, monkeypatch):
    hash_file = tmp_path / "tunnel-password"
    hash_file.write_text(auth.hash_password("letmein") + "\n")
    monkeypatch.setenv("HOMEBOARD_PASSWORD_FILE", str(hash_file))
    with TestClient(create_app(tmp_path / "data"), base_url="https://board.example.com") as c:
        yield c


@pytest.mark.parametrize("path", PAGES)
def test_admin_pages_return_200(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "/static/win95.css?v=" in r.text


def test_start_links_to_photos_and_about(client):
    page = client.get("/admin").text
    assert 'href="/admin/photos"' in page
    assert 'href="/admin/calendar"' in page
    assert "Coming soon" not in page
    assert ">Notes<" not in page and "Grocery List" not in page
    assert 'id="photo-count"' in page and 'id="display-state"' in page


def test_start_has_no_app_group_and_a_small_about_link(client):
    page = client.get("/admin").text
    assert "group-app" not in page and ">App<" not in page
    assert page.count('href="/admin/about"') == 1
    assert '<a href="/admin/about">About this board...</a>' in page
    for icons in re.findall(r'<ul class="w95-icons">.*?</ul>', page, re.DOTALL):
        assert "/admin/about" not in icons


def test_enabled_icons_have_no_subtext(client):
    page = client.get("/admin").text
    enabled = re.findall(r'<a class="w95-icon".*?</a>', page, re.DOTALL)
    assert enabled
    for icon in enabled:
        assert "w95-icon-note" not in icon


def test_start_status_refreshes_every_ten_seconds():
    js = (ROOT / "static" / "start.js").read_text()
    assert '"/api/status"' in js
    assert "STATUS_MS = 10000" in js and "setInterval(loadStatus, STATUS_MS)" in js
    assert '"Display online"' in js and '"Display offline"' in js


def test_photos_keeps_controls_and_back_link(client):
    page = client.get("/admin/photos").text
    assert 'id="file-input" type="file"' in page
    assert 'id="grid"' in page and 'id="interval"' in page
    assert '<a class="w95-back" href="/admin">Start</a>' in page
    for el in ('id="host"', 'id="ip"', 'id="disk"', 'id="version"', 'href="/docs"'):
        assert el not in page


def test_about_page(client):
    page = client.get("/admin/about").text
    for el in ('id="host"', 'id="ip"', 'id="disk"', 'id="version"'):
        assert el in page
    assert 'href="/docs"' not in page
    assert '<a class="w95-back" href="/admin">Start</a>' in page


def test_win95_css_classes():
    for cls in (
        ".w95-window",
        ".w95-titlebar",
        ".w95-close",
        ".w95-group",
        ".w95-btn",
        ".w95-btn.default",
        ".w95-check",
        ".w95-input",
        ".w95-progress",
        ".w95-statusbar",
        ".w95-icon",
        ":focus-visible",
    ):
        assert cls in WIN95, cls
    assert "min-height: 44px" in WIN95 and "height: 44px" in WIN95


def test_win95_sizes_for_phones():
    def rule(sel):
        m = re.search(r"(?m)^" + re.escape(sel) + r" \{([^}]*)\}", WIN95)
        assert m, sel
        return m.group(1)

    assert "min-height: 40px" in rule(".w95-titlebar")
    icon = rule(".w95-icon-img")
    assert "width: 64px" in icon and "height: 64px" in icon
    window = rule(".w95-window")
    assert "max-width: 960px" in window and "width: 100%" in window
    assert "display: flex" in window and "flex-direction: column" in window
    assert "font-size: clamp(13px, 1.1vw + 10px, 16px)" in rule(".w95-desktop")
    assert "minmax(110px, 1fr)" in rule(".w95-icons")
    assert "min-height: 120px" in rule(".w95-icon")


def test_no_dark_mode_or_rounded_corners():
    for text in (
        WIN95,
        (ROOT / "templates" / "start.html").read_text(),
        (ROOT / "templates" / "about.html").read_text(),
        (ROOT / "templates" / "admin.html").read_text(),
    ):
        assert "prefers-color-scheme" not in text
        assert "border-radius" not in text


@pytest.mark.parametrize("path", PAGES)
def test_tunnel_gate_demands_password(tunnel_client, path):
    r = tunnel_client.get(path, headers=TUNNEL, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?next=" + path)
    ok = tunnel_client.post(
        "/login",
        data={"password": "letmein", "next": path},
        headers=TUNNEL,
        follow_redirects=False,
    )
    assert ok.status_code == 303 and ok.headers["location"] == path
    assert tunnel_client.get(path, headers=TUNNEL).status_code == 200


START_JS = ROOT / "static" / "start.js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

START_HARNESS = r"""
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
for (const id of ["photo-count", "display-state", "channel-timeout", "name-dialog", "name-form",
  "name-input", "name-error", "name-cancel", "name-close", "name-backdrop"]) {
  body.appendChild(make("x", id));
}
const row = body.appendChild(make("span", "tv-stops"));
for (const [ch, label] of [["photos", "Photos"]]) {
  const b = row.appendChild(make("button"));
  b.setAttribute("data-channel", ch);
  b.textContent = label;
}
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
    out = server.screens;
  }
  return Promise.resolve({ ok: true, json: () => Promise.resolve(JSON.parse(JSON.stringify(out))) });
};

const label = (e) => e.textContent || e.getAttribute("aria-label");
const view = () => row.children.map((c) => {
  if (c.className === "stop-edit") return { edit: c.children.map((b) => [label(b), b.disabled]) };
  return [label(c), c.getAttribute("aria-pressed")];
});
const byText = (text) => walk(row, []).find((e) => e.textContent === text);
"""


def _start(body):
    script = (
        f"{START_HARNESS}\nrequire({json.dumps(str(START_JS))});\n(async () => {{\n{body}\n}})();"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


def test_start_screen_has_tv_buttons_and_edit(client):
    page = client.get("/admin").text
    row = re.search(r'<span id="tv-stops" class="w95-seg">(.*?)</span>', page, re.DOTALL)
    assert row
    assert re.findall(r"<button[^>]*>([^<]*)</button>", row.group(1)) == [
        "Photos",
        "Edit",
    ]
    assert 'data-channel="photos"' in row.group(1) and 'data-channel="calendar"' not in row.group(1)
    assert 'id="screens-edit"' in row.group(1)
    dialog = re.search(r'<div id="name-dialog" class="modal" hidden>.*?</form>', page, re.DOTALL)
    assert dialog and ">Rename<" in dialog.group(0)
    assert 'class="w95-window dialog"' in dialog.group(0)


def test_start_script_reads_screens(client):
    page = client.get("/admin").text
    src = re.search(r'<script src="(/static/start\.js[^"]*)"', page).group(1)
    js = client.get(src).text
    assert 'fetch("/api/screens"' in js
    assert '"/api/screens/order"' in js and '"/api/screens/" + renameId' in js


def test_start_js_has_no_native_dialogs():
    js = START_JS.read_text()
    for name in ("prompt(", "confirm(", "alert("):
        assert name not in js


def test_screen_dialog_styles_live_in_win95():
    for sel in (".modal", ".modal-backdrop", ".dialog", ".title-close", ".buttons"):
        assert re.search(r"(?m)^" + re.escape(sel) + r" \{", WIN95), sel
    assert ".modal[hidden]" in WIN95


@needs_node
def test_start_buttons_follow_screens_and_put_settings():
    r = _start(
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
def test_start_edit_mode_moves_and_renames_screens():
    r = _start(
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


def test_screen_rename_and_order_api(client):
    first = client.get("/api/screens").json()[0]["id"]
    second = client.post("/api/screens", json={"name": "Chores"}).json()["id"]
    r = client.put("/api/screens/order", json={"ids": [second, first]})
    assert [s["id"] for s in r.json()][:2] == [second, first]
    r = client.patch(f"/api/screens/{second}", json={"name": "Fridge"})
    assert r.json()["name"] == "Fridge"
    assert client.get("/api/screens").json()[0]["name"] == "Fridge"
    r = client.put("/api/settings", json={"channel": "screen", "screen_id": second})
    assert (r.json()["channel"], r.json()["screen_id"]) == ("screen", second)
