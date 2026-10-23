import json
import shutil
import subprocess
from pathlib import Path

import pytest

DISPLAY_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "display.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

HARNESS = r"""
let now = 0;
let seq = 0;
const timers = new Map();
globalThis.setTimeout = (fn, ms) => { const id = ++seq; timers.set(id, { at: now + (ms || 0), fn }); return id; };
globalThis.clearTimeout = (id) => { timers.delete(id); };
globalThis.setInterval = () => 0;
Date.now = () => now;
const flush = () => new Promise((r) => setImmediate(r));
async function advance(ms) {
  const end = now + ms;
  for (;;) {
    await flush();
    let next = null;
    for (const [id, t] of timers) if (t.at <= end && (!next || t.at < next[1].at)) next = [id, t];
    if (!next) break;
    timers.delete(next[0]);
    now = next[1].at;
    next[1].fn();
  }
  now = end;
  await flush();
}

function el(id) {
  const classes = new Set();
  const attrs = {};
  const e = {
    id, hidden: false, textContent: "", dataset: {}, complete: false, naturalWidth: 0,
    onload: null, onerror: null,
    classList: {
      add: (c) => classes.add(c), remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
      toggle: (c, on) => { if (on) classes.add(c); else classes.delete(c); },
    },
    getAttribute: (k) => (k in attrs ? attrs[k] : null),
    removeAttribute: (k) => { delete attrs[k]; },
  };
  Object.defineProperty(e, "src", {
    get: () => attrs.src,
    set: (v) => {
      attrs.src = v;
      e.complete = false;
      Promise.resolve().then(() => { e.complete = true; e.naturalWidth = 10; if (e.onload) e.onload(); });
    },
  });
  return e;
}
const els = { "layer-a": el("layer-a"), "layer-b": el("layer-b"), caption: el("caption"), clock: el("clock") };
globalThis.document = { getElementById: (id) => els[id] || null, body: { insertBefore() {} } };
globalThis.window = globalThis;

const server = {
  settings: { interval_s: 20, transition: true, fit: "contain", shuffle: false, captions: true },
  photos: [
    { id: 1, url: "/p/1.jpg", caption: "first" },
    { id: 2, url: "/p/2.jpg", caption: "second" },
    { id: 3, url: "/p/3.jpg", caption: "" },
  ],
};
globalThis.fetch = (url, opts) => {
  let body = {};
  if (url === "/api/settings") body = server.settings;
  else if (url === "/api/photos") body = server.photos;
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
};
const sources = [];
globalThis.EventSource = function () {
  const handlers = {};
  this.addEventListener = (k, fn) => { handlers[k] = fn; };
  this.close = () => {};
  this.emit = (k) => handlers[k] && handlers[k]();
  sources.push(this);
};

const cap = els.caption;
const snap = () => ({ show: cap.classList.contains("show"), text: cap.textContent });
"""


def _run(body):
    script = f"{HARNESS}\nrequire({json.dumps(str(DISPLAY_JS))});\n(async () => {{\n{body}\n}})();"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@needs_node
def test_caption_stays_for_whole_photo_then_changes():
    r = _run(
        """
        await advance(100);
        const start = snap();
        await advance(19000);
        const late = snap();
        await advance(1000);
        const fading = snap();
        await advance(600);
        const next = snap();
        await advance(20000);
        const noCaption = snap();
        console.log(JSON.stringify({ start, late, fading, next, noCaption }));
        """
    )
    assert r["start"] == {"show": True, "text": "first"}
    assert r["late"] == {"show": True, "text": "first"}
    assert r["fading"]["show"] is False
    assert r["next"] == {"show": True, "text": "second"}
    assert r["noCaption"]["show"] is False


@needs_node
def test_turning_captions_off_hides_right_away():
    r = _run(
        """
        await advance(100);
        const before = snap();
        server.settings = Object.assign({}, server.settings, { captions: false });
        sources[0].emit("settings_changed");
        await flush();
        const after = snap();
        await advance(20500);
        const nextPhoto = snap();
        console.log(JSON.stringify({ before, after, nextPhoto }));
        """
    )
    assert r["before"] == {"show": True, "text": "first"}
    assert r["after"]["show"] is False
    assert r["nextPhoto"]["show"] is False


@needs_node
def test_single_photo_keeps_caption_without_flicker():
    r = _run(
        """
        server.photos = [{ id: 1, url: "/p/1.jpg", caption: "only" }];
        await advance(100);
        const seen = [];
        for (let i = 0; i < 60; i++) { await advance(1000); seen.push(snap().show); }
        console.log(JSON.stringify({ text: cap.textContent, allShown: seen.every(Boolean) }));
        """
    )
    assert r == {"text": "only", "allShown": True}
