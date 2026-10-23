import json
import shutil
import subprocess
from pathlib import Path

import pytest

ADMIN_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "admin.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

DOM_STUB = """
function El(tag) {
  this.tagName = tag;
  this.children = [];
  this.attrs = {};
  this.className = "";
  this.textContent = "";
  this.parentNode = null;
  var self = this;
  this.classList = {
    contains: function (c) { return self.className.split(" ").indexOf(c) !== -1; },
    toggle: function (c, on) {
      var list = self.className.split(" ").filter(function (x) { return x && x !== c; });
      if (on) list.push(c);
      self.className = list.join(" ");
    },
  };
}
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
globalThis.document = {
  createElement: function (t) { return new El(t); },
  createElementNS: function (ns, t) { return new El(t); },
};
function badges(tile) {
  return tile.children.filter(function (c) { return c.classList.contains("badge"); }).map(function (c) {
    return { cls: c.className, text: c.textContent, svg: c.children.length > 0 };
  });
}
function view(tile) {
  return {
    cls: tile.className,
    badges: badges(tile),
    label: tile.querySelector(".tile-open").getAttribute("aria-label"),
    last: tile.children[tile.children.length - 1].className,
  };
}
"""


def _node(body):
    script = f"const A = require({json.dumps(str(ADMIN_JS))});\n{DOM_STUB}\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


def photo(**kw):
    p = {"id": 7, "thumb_url": "/t/7.jpg", "caption": None, "enabled": True}
    p.update(kw)
    return json.dumps(p)


@needs_node
def test_caption_gets_cc_badge():
    r = _node(f"console.log(JSON.stringify(view(A.buildTile({photo(caption='Beach day')}))))")
    assert r["cls"] == "tile"
    assert r["badges"] == [{"cls": "badge badge-cc", "text": "CC", "svg": False}]
    assert r["label"] == "Photo: Beach day"
    assert r["last"] == "tile-handle"


@needs_node
def test_null_caption_has_no_badges():
    r = _node(f"console.log(JSON.stringify(view(A.buildTile({photo()}))))")
    assert r["cls"] == "tile"
    assert r["badges"] == []


@needs_node
def test_hidden_photo_gets_hidden_badge_and_disabled_class():
    r = _node(f"console.log(JSON.stringify(view(A.buildTile({photo(enabled=False)}))))")
    assert r["cls"] == "tile disabled"
    assert r["badges"] == [{"cls": "badge badge-hidden", "text": "", "svg": True}]
    assert r["label"] == "Photo, hidden from the board"


@needs_node
def test_tile_state_updates_after_save():
    r = _node(
        f"""
        const p = {photo()};
        const tile = A.buildTile(p);
        p.caption = "Hi"; p.enabled = false;
        A.setTileState(tile, p);
        const both = view(tile);
        p.caption = null; p.enabled = true;
        A.setTileState(tile, p);
        console.log(JSON.stringify({{both: both, none: view(tile)}}));
        """
    )
    assert r["both"]["cls"] == "tile disabled"
    assert sorted(b["cls"] for b in r["both"]["badges"]) == ["badge badge-cc", "badge badge-hidden"]
    assert r["none"]["cls"] == "tile"
    assert r["none"]["badges"] == []


@needs_node
def test_photo_patch_sends_only_changes_in_one_body():
    r = _node(
        """
        const p = {caption: null, enabled: true};
        console.log(JSON.stringify({
          both: A.photoPatch(p, "  New caption ", false),
          cap: A.photoPatch(p, "New", true),
          en: A.photoPatch(p, "", false),
          none: A.photoPatch(p, "   ", true),
          clear: A.photoPatch({caption: "Old", enabled: false}, "", false),
        }));
        """
    )
    assert r["both"] == {"caption": "New caption", "enabled": False}
    assert r["cap"] == {"caption": "New"}
    assert r["en"] == {"enabled": False}
    assert r["none"] == {}
    assert r["clear"] == {"caption": None}
