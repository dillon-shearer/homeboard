import base64
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from PIL import Image
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "shots"
SIZES = [(390, 844), (412, 915)]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def find_chrome() -> str:
    env = os.environ.get("CHROME_PATH")
    if env:
        return env
    local = os.environ.get("LOCALAPPDATA")
    if local:
        base = Path(local) / "ms-playwright"
        if base.is_dir():
            dirs = sorted((d.name for d in base.iterdir()), reverse=True)
            for prefix, rel in (
                (
                    "chromium_headless_shell-",
                    "chrome-headless-shell-win64/chrome-headless-shell.exe",
                ),
                ("chromium_headless_shell-", "chrome-win/headless_shell.exe"),
                ("chromium-", "chrome-win64/chrome.exe"),
                ("chromium-", "chrome-win/chrome.exe"),
            ):
                for d in dirs:
                    exe = base / d / rel
                    if d.startswith(prefix) and exe.is_file():
                        return str(exe)
    for p in (
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ):
        if Path(p).is_file():
            return p
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    sys.exit("Chrome not found; set CHROME_PATH")


def photo_bytes(color, size) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", quality=85)
    return buf.getvalue()


def wait_ready(base: str, proc: subprocess.Popen) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            sys.exit("uvicorn exited early")
        try:
            if httpx.get(f"{base}/healthz", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    sys.exit("uvicorn did not start")


def seed(base: str) -> None:
    created = []
    for name, color, size in (
        ("sunset.jpg", (230, 120, 40), (1200, 800)),
        ("forest.jpg", (40, 140, 70), (800, 1200)),
    ):
        files = {"files": (name, photo_bytes(color, size), "image/jpeg")}
        r = httpx.post(f"{base}/api/photos", files=files, timeout=30)
        r.raise_for_status()
        created.extend(r.json()["created"])
    r = httpx.patch(f"{base}/api/photos/{created[-1]['id']}", json={"enabled": False})
    r.raise_for_status()


def shoot(chrome: str, url: str, width: int, height: int, out: Path, profile: Path) -> None:
    args = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={profile}",
        f"--window-size={width},{height}",
        "--virtual-time-budget=5000",
        f"--screenshot={out}",
        url,
    ]
    subprocess.run(args, check=True, capture_output=True, timeout=60)
    if not out.is_file() or out.stat().st_size == 0:
        sys.exit(f"no screenshot written to {out}")
    with Image.open(out) as img:
        if img.width != width:
            sys.exit(f"{out.name} is {img.width}px wide, expected {width}")
    print(f"wrote {out.relative_to(ROOT)}")


class Cdp:
    def __init__(self, ws):
        self.ws = ws
        self.n = 0

    def send(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        return self.n

    def call(self, method, **params):
        mid = self.send(method, **params)
        while True:
            msg = json.loads(self.ws.recv(timeout=30))
            if msg.get("id") == mid:
                if "error" in msg:
                    sys.exit(f"{method} failed: {msg['error']}")
                return msg["result"]

    def eval(self, expr):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            sys.exit(f"page error in {expr!r}: {r['exceptionDetails']}")
        return r["result"].get("value")

    def wait(self, expr, what):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.eval(expr):
                return
            time.sleep(0.1)
        sys.exit(f"timed out waiting for {what}")


def api_photos(base: str) -> dict:
    r = httpx.get(f"{base}/api/photos?all=1", timeout=10)
    r.raise_for_status()
    return {p["id"]: p for p in r.json()}


def wait_api(check, what: str) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.1)
    sys.exit(f"timed out waiting for {what}")


def drive_menu(chrome: str, base: str, out: Path, profile: Path) -> None:
    width, height = 390, 844
    port = free_port()
    proc = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile}",
            f"--window-size={width},{height}",
            f"--remote-debugging-port={port}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = None
        deadline = time.monotonic() + 30
        while ws_url is None and time.monotonic() < deadline:
            try:
                targets = httpx.get(f"http://127.0.0.1:{port}/json", timeout=1).json()
                ws_url = next(t["webSocketDebuggerUrl"] for t in targets if t["type"] == "page")
            except (httpx.HTTPError, StopIteration, ValueError):
                time.sleep(0.2)
        if ws_url is None:
            sys.exit("Chrome DevTools did not start")
        with connect(ws_url, max_size=None, open_timeout=30) as ws:
            cdp = Cdp(ws)
            cdp.call("Page.enable")
            cdp.call(
                "Emulation.setDeviceMetricsOverride",
                width=width,
                height=height,
                deviceScaleFactor=1,
                mobile=True,
            )
            cdp.call("Page.navigate", url=f"{base}/admin/photos")
            cdp.wait(
                "document.querySelectorAll('#grid .tile img').length === 2 && "
                "[...document.querySelectorAll('#grid .tile img')].every(i => i.complete)",
                "the photo grid",
            )

            tile = "document.querySelector('#grid .tile.disabled')"
            off_id = cdp.eval(f"{tile}.getAttribute('data-id')")
            if off_id is None:
                sys.exit("no disabled tile to toggle")
            cdp.eval(f"{tile}.querySelector('button.tile-open').click()")
            cdp.wait("!document.getElementById('photo-dialog').hidden", "the photo window")
            cdp.eval("document.getElementById('caption-input').value = 'Forest'")
            cdp.eval("document.getElementById('photo-enabled').click()")
            cdp.eval("document.getElementById('photo-save').click()")
            wait_api(lambda: api_photos(base)[int(off_id)]["enabled"], "the save PATCH")
            if api_photos(base)[int(off_id)]["caption"] != "Forest":
                sys.exit("save did not send the caption")
            cdp.wait(
                f"!!document.querySelector('.tile[data-id=\"{off_id}\"]:not(.disabled) .badge-cc')",
                "the CC badge",
            )
            print(f"showed photo {off_id} and captioned it")

            first = "document.querySelector('#grid .tile')"
            del_id = cdp.eval(f"{first}.getAttribute('data-id')")
            cdp.eval(f"{first}.querySelector('button.tile-open').click()")
            cdp.wait("!document.getElementById('photo-dialog').hidden", "the photo window")
            labels = cdp.eval(
                "[...document.querySelectorAll('#photo-edit button')].map(b => b.textContent)"
            )
            if labels != ["Save", "Cancel", "Delete"]:
                sys.exit(f"photo window buttons are {labels}")
            time.sleep(0.3)
            shot = cdp.call("Page.captureScreenshot", format="png")
            out.write_bytes(base64.b64decode(shot["data"]))
            with Image.open(out) as img:
                if img.width != width:
                    sys.exit(f"{out.name} is {img.width}px wide, expected {width}")
            print(f"wrote {out.relative_to(ROOT)}")

            cdp.eval("document.getElementById('photo-delete').click()")
            cdp.wait("!document.getElementById('photo-confirm').hidden", "the delete confirm")
            cdp.eval("document.getElementById('confirm-delete').click()")
            wait_api(lambda: int(del_id) not in api_photos(base), "the DELETE")
            cdp.wait(
                f"!document.querySelector('.tile[data-id=\"{del_id}\"]') && "
                "document.querySelectorAll('#grid .tile').length === 1",
                "the tile removal",
            )
            print(f"deleted photo {del_id}")
            cdp.send("Browser.close")
        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def main() -> None:
    chrome = find_chrome()
    SHOTS.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="homeboard-shot-"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, HOMEBOARD_DATA=str(tmp / "data"))
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
    )
    try:
        wait_ready(base, proc)
        seed(base)
        for width, height in SIZES:
            shoot(
                chrome,
                f"{base}/admin/photos",
                width,
                height,
                SHOTS / f"admin-{width}.png",
                tmp / f"chrome-{width}",
            )
        drive_menu(chrome, base, SHOTS / "admin-390-menu.png", tmp / "chrome-menu")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
