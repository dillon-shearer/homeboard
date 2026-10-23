import io
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
                ("chromium_headless_shell-", "chrome-headless-shell-win64/chrome-headless-shell.exe"),
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
    for name, color, size in (("sunset.jpg", (230, 120, 40), (1200, 800)),
                              ("forest.jpg", (40, 140, 70), (800, 1200))):
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


def main() -> None:
    chrome = find_chrome()
    SHOTS.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="homeboard-shot-"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, HOMEBOARD_DATA=str(tmp / "data"))
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        cwd=ROOT,
        env=env,
    )
    try:
        wait_ready(base, proc)
        seed(base)
        for width, height in SIZES:
            shoot(chrome, f"{base}/admin", width, height, SHOTS / f"admin-{width}.png",
                  tmp / f"chrome-{width}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
