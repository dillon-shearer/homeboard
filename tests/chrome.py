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


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
needs_chrome = pytest.mark.skipif(_chrome() is None, reason="chrome not installed")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
        self.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False},
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

    def close(self):
        self.ws.close()
        self.proc.kill()
        self.proc.wait(timeout=10)
