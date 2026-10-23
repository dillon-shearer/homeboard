import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ADMIN_JS = ROOT / "app" / "static" / "admin.js"
ADMIN_HTML = ROOT / "app" / "templates" / "admin.html"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

PAGE_STUB = """
function El(tag, id) {
  this.tagName = tag;
  this.id = id || "";
  this.children = [];
  this.attrs = {};
  this.className = "";
  this.text = "";
  this.hidden = false;
  this.disabled = false;
  this.value = "";
  this.style = {};
  this.parentNode = null;
  this.handlers = {};
  var self = this;
  this.classList = {
    contains: function (c) { return self.className.split(" ").indexOf(c) !== -1; },
    toggle: function (c, on) {
      var list = self.className.split(" ").filter(function (x) { return x && x !== c; });
      if (on) list.push(c);
      self.className = list.join(" ");
    },
    add: function (c) { this.toggle(c, true); },
    remove: function (c) { this.toggle(c, false); },
  };
}
Object.defineProperty(El.prototype, "textContent", {
  get: function () { return this.text; },
  set: function (v) { this.text = String(v); this.children = []; },
});
El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
El.prototype.getAttribute = function (k) { return k in this.attrs ? this.attrs[k] : null; };
El.prototype.appendChild = function (c) { c.parentNode = this; this.children.push(c); return c; };
El.prototype.insertBefore = function (c, ref) {
  var i = ref ? this.children.indexOf(ref) : -1;
  c.parentNode = this;
  if (i === -1) this.children.push(c); else this.children.splice(i, 0, c);
  return c;
};
El.prototype.removeChild = function (c) { this.children.splice(this.children.indexOf(c), 1); c.parentNode = null; return c; };
El.prototype.querySelector = function (sel) {
  for (var i = 0; i < this.children.length; i++) {
    var c = this.children[i];
    if (sel.charAt(0) === "." ? c.classList.contains(sel.slice(1)) : c.tagName === sel) return c;
    var deep = c.querySelector(sel);
    if (deep) return deep;
  }
  return null;
};
El.prototype.querySelectorAll = function (sel) {
  return this.children.filter(function (c) { return c.classList.contains(sel.slice(1)); });
};
El.prototype.addEventListener = function (type, fn) { (this.handlers[type] = this.handlers[type] || []).push(fn); };
El.prototype.fire = function (type, e) {
  e = e || {};
  e.target = e.target || this;
  e.currentTarget = this;
  (this.handlers[type] || []).forEach(function (fn) { fn(e); });
};
El.prototype.focus = function () {};

var byId = {};
globalThis.window = globalThis;
globalThis.addEventListener = function () {};
globalThis.setInterval = function () { return 0; };
globalThis.document = {
  body: { contains: function () { return true; } },
  createElement: function (t) { return new El(t); },
  createElementNS: function (ns, t) { return new El(t); },
  getElementById: function (id) { return byId[id] || (byId[id] = new El("div", id)); },
  querySelectorAll: function () { return []; },
  addEventListener: function () {},
};
["upload-dialog", "photo-dialog", "grid-empty", "grid-error", "show-more"].forEach(function (id) {
  document.getElementById(id).hidden = true;
});
document.getElementById("upload-close").disabled = true;

var server = { photos: { ok: true, body: [] } };
globalThis.fetch = function (url) {
  var res = { ok: true, status: 200, body: {} };
  if (url.indexOf("/api/photos") === 0) res = server.photos;
  if (url === "/api/status") res = { ok: true, status: 200, body: { display_connected: true, photo_count: 0 } };
  return Promise.resolve({
    ok: res.ok,
    status: res.status || (res.ok ? 200 : 500),
    json: function () { return Promise.resolve(res.body); },
  });
};
globalThis.FormData = function () { this.parts = []; };
FormData.prototype.append = function (k, f) { this.parts.push(f.name); };
var xhrs = [];
globalThis.XMLHttpRequest = function () { this.upload = {}; this.status = 0; this.responseText = ""; xhrs.push(this); };
XMLHttpRequest.prototype.open = function () {};
XMLHttpRequest.prototype.send = function (form) { this.name = form.parts[0]; };
function xhrFor(name) { return xhrs.filter(function (x) { return x.name === name; })[0]; }
function finish(name, status, body) {
  var x = xhrFor(name);
  x.status = status;
  x.responseText = body ? JSON.stringify(body) : "";
  x.onload();
}
function file(name, type) { return { name: name, type: type || "image/jpeg" }; }
function pick(files) {
  var input = document.getElementById("file-input");
  input.fire("change", { target: { files: files, value: "x" } });
}
function rows() {
  return document.getElementById("upload-list").children.map(function (li) {
    return { name: li.children[0].textContent, state: li.children[2].textContent };
  });
}
function flush() { return new Promise(function (r) { setTimeout(r, 0); }); }
function $(id) { return document.getElementById(id); }
"""


def _node(body, setup=""):
    script = (
        f"{PAGE_STUB}\n{setup}\nconst A = require({json.dumps(str(ADMIN_JS))});\n"
        f"(async function () {{\n{body}\n}})().catch(function (e) {{ console.error(e); process.exit(1); }});"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@needs_node
def test_second_batch_keeps_first_batch_rows_until_they_finish():
    r = _node(
        """
        await flush();
        pick([file("a.jpg"), file("b.jpg"), file("notes.txt", "text/plain")]);
        xhrFor("a.jpg").upload.onprogress({ lengthComputable: true, loaded: 1, total: 2 });
        pick([file("c.jpg")]);
        const during = rows();
        finish("a.jpg", 200, { uploaded: [] });
        finish("b.jpg", 413, { errors: [{ error: "too large" }] });
        await flush();
        const firstDone = { rows: rows(), closeDisabled: $("upload-close").disabled };
        finish("c.jpg", 200, { uploaded: [] });
        await flush();
        const allDone = { rows: rows(), closeDisabled: $("upload-close").disabled };
        pick([file("d.jpg")]);
        console.log(JSON.stringify({ during: during, firstDone: firstDone, allDone: allDone, third: rows() }));
        """
    )
    assert r["during"] == [
        {"name": "a.jpg", "state": "50%"},
        {"name": "b.jpg", "state": "Waiting..."},
        {"name": "notes.txt", "state": "Only JPEG, PNG, HEIC and WebP photos"},
        {"name": "c.jpg", "state": "Waiting..."},
    ]
    assert r["firstDone"]["closeDisabled"] is True
    assert r["firstDone"]["rows"][:3] == [
        {"name": "a.jpg", "state": "Done"},
        {"name": "b.jpg", "state": "This photo is too large"},
        {"name": "notes.txt", "state": "Only JPEG, PNG, HEIC and WebP photos"},
    ]
    assert r["allDone"]["closeDisabled"] is False
    assert r["allDone"]["rows"][3] == {"name": "c.jpg", "state": "Done"}
    assert r["third"] == [{"name": "d.jpg", "state": "Waiting..."}]


@needs_node
def test_new_batch_clears_done_rows_but_keeps_errors_of_running_batch():
    r = _node(
        """
        await flush();
        pick([file("a.jpg"), file("notes.txt", "text/plain"), file("b.jpg")]);
        finish("a.jpg", 200, { uploaded: [] });
        await flush();
        pick([file("c.jpg")]);
        console.log(JSON.stringify(rows()));
        """
    )
    assert r == [
        {"name": "notes.txt", "state": "Only JPEG, PNG, HEIC and WebP photos"},
        {"name": "b.jpg", "state": "Waiting..."},
        {"name": "c.jpg", "state": "Waiting..."},
    ]


@needs_node
def test_photo_list_error_shows_and_try_again_reloads():
    r = _node(
        """
        await flush();
        const failed = { error: $("grid-error").hidden, empty: $("grid-empty").hidden, retry: $("grid-retry").disabled };
        server.photos = { ok: true, body: [{ id: 3, thumb_url: "/t/3.jpg", caption: null, enabled: true }] };
        $("grid-retry").fire("click");
        const busy = $("grid-retry").disabled;
        await flush();
        const fixed = { error: $("grid-error").hidden, tiles: $("grid").querySelectorAll(".tile").length, retry: $("grid-retry").disabled };
        console.log(JSON.stringify({ failed: failed, busy: busy, fixed: fixed }));
        """,
        setup="server.photos = { ok: false, status: 500, body: {} };",
    )
    assert r["failed"] == {"error": False, "empty": True, "retry": False}
    assert r["busy"] is True
    assert r["fixed"] == {"error": True, "tiles": 1, "retry": False}


def test_admin_html_has_photo_list_error():
    html = ADMIN_HTML.read_text()
    assert '<div id="grid-error" class="empty grid-error w95-sunken" role="alert" hidden>' in html
    assert '<p class="error">Could not load the photos.</p>' in html
    assert '<button type="button" id="grid-retry" class="w95-btn">Try Again</button>' in html
    js = ADMIN_JS.read_text()
    assert '$("grid-retry").addEventListener("click", refreshGrid);' in js
    assert "loadGrid().catch(function () {})" not in js
