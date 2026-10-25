import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from websockets.sync.client import connect

from app import weather
from app.main import create_app

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def _chrome():
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    for path in CHROME_PATHS:
        if Path(path).exists():
            return path
    return None


class _KeepOpen(httpx.HTTPTransport):
    def __exit__(self, *args):
        pass

    def close(self):
        pass


_transport = _KeepOpen()


def api_at(base=""):
    return httpx.Client(base_url=base, transport=_transport, timeout=30)


api = api_at()

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
needs_chrome = pytest.mark.skipif(_chrome() is None, reason="chrome not installed")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


HOOKS = """(() => {
  const realSet = window.setTimeout, realClear = window.clearTimeout;
  const realEvery = window.setInterval, realNow = Date.now;
  const timers = new Map(), every = [], heard = {}, pending = [];
  let skipped = 0;
  window.__sent = [];
  window.__errors = [];
  window.addEventListener("error", (e) => window.__errors.push(String(e.message)));
  window.addEventListener("unhandledrejection", (e) => window.__errors.push(String(e.reason)));
  const realError = console.error;
  console.error = function (...args) {
    window.__errors.push(args.join(" "));
    realError.apply(console, args);
  };
  const realFetch = window.fetch;
  window.__inflight = 0;
  window.fetch = function (url, opts) {
    opts = opts || {};
    if (opts.method && opts.method !== "GET") {
      const body = typeof opts.body === "string" ? JSON.parse(opts.body) : null;
      window.__sent.push({method: opts.method, url: String(url), body: body});
    }
    window.__inflight += 1;
    const done = () => { window.__inflight -= 1; };
    const out = realFetch.apply(this, arguments);
    out.then(done, done);
    return out;
  };
  window.setTimeout = function (fn, ms, ...args) {
    const id = realSet.call(window, () => { timers.delete(id); fn(...args); }, ms);
    timers.set(id, {fn, ms: ms || 0, args});
    return id;
  };
  window.clearTimeout = function (id) { timers.delete(id); realClear.call(window, id); };
  window.setInterval = function (fn, ms, ...args) {
    every.push({fn, ms, args});
    return realEvery.call(window, fn, ms, ...args);
  };
  Date.now = () => realNow() + skipped;
  if (window.EventSource) {
    const listen = EventSource.prototype.addEventListener;
    EventSource.prototype.addEventListener = function (type, fn, opts) {
      return listen.call(this, type, function (e) {
        heard[type] = (heard[type] || 0) + 1;
        const out = fn.call(this, e);
        pending.push(Promise.resolve(out));
        return out;
      }, opts);
    };
  }
  window.__test = {
    heard: (type) => heard[type] || 0,
    settle: () => Promise.all(pending.splice(0)).then(() => true),
    waiting: (ms) => [...timers.values()].filter((t) => t.ms === ms).length,
    fire: (ms) => {
      for (const [id, t] of [...timers]) {
        if (t.ms !== ms) continue;
        timers.delete(id);
        realClear.call(window, id);
        t.fn(...t.args);
      }
      return true;
    },
    tick: (ms) => Promise.all(every.filter((t) => t.ms === ms).map((t) => t.fn(...t.args)))
      .then(() => true),
    skip: (ms) => { skipped += ms; return true; },
  };
})();"""


def _offline(url):
    raise OSError(f"offline in tests: {url}")


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(weather, "fetch_json", _offline)
    port = _free_port()
    config = uvicorn.Config(
        create_app(tmp_path / "data"), host="127.0.0.1", port=port, log_level="error"
    )
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield base
    srv.should_exit = True
    thread.join(timeout=10)


class _Chrome:
    def __init__(self, profile, width, height):
        self.proc = subprocess.Popen(
            [
                _chrome(),
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-features=BackForwardCache",
                "--remote-debugging-port=0",
                f"--user-data-dir={profile}",
                f"--window-size={width},{height}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        port_file = Path(profile) / "DevToolsActivePort"
        text = ""
        for _ in range(200):
            try:
                text = port_file.read_text().strip()
            except OSError:
                text = ""
            if text:
                break
            time.sleep(0.05)
        port = text.split()[0]
        pages = httpx.get(f"http://127.0.0.1:{port}/json/list").json()
        url = next(p["webSocketDebuggerUrl"] for p in pages if p["type"] == "page")
        self.ws = connect(url, max_size=None)
        self.next_id = 0
        self.size(width, height)
        self.send("Page.enable")
        self.send("Page.addScriptToEvaluateOnNewDocument", {"source": HOOKS})

    def size(self, width, height):
        self.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
        )

    def touch(self, enabled=True, points=1):
        self.send(
            "Emulation.setTouchEmulationEnabled", {"enabled": enabled, "maxTouchPoints": points}
        )

    def send(self, method, params=None):
        self.next_id += 1
        self.ws.send(json.dumps({"id": self.next_id, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv(timeout=30))
            if msg.get("id") == self.next_id:
                return msg.get("result", {})

    def eval(self, expr):
        return self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})[
            "result"
        ].get("value")

    def run(self, expr):
        return self.send(
            "Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True}
        )["result"].get("value")

    def wait(self, expr, tries=200):
        for _ in range(tries):
            value = self.eval(expr)
            if value:
                return value
            time.sleep(0.05)
        raise AssertionError(expr)

    def close(self):
        self.ws.close()
        self.proc.kill()
        self.proc.wait(timeout=10)


@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    chrome = _Chrome(tmp_path_factory.mktemp("chrome"), 1920, 1080)
    yield chrome
    chrome.close()


@pytest.fixture
def chrome(browser, server):
    yield browser
    browser.send("Page.navigate", {"url": "about:blank"})
    browser.wait("location.href === 'about:blank'")
    browser.send("Storage.clearDataForOrigin", {"origin": server, "storageTypes": "all"})
    browser.touch(False)
    browser.size(1920, 1080)
