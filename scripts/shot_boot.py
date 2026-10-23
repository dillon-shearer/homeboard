import base64
import math
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from shot import ROOT, SHOTS, Cdp, find_chrome, free_port, shoot
from websockets.sync.client import connect

BOOT = ROOT / "deploy" / "splash" / "boot.html"
THEME = ROOT / "deploy" / "plymouth" / "homeboard"
WIDTH, HEIGHT = 1920, 1080
LABELS = {
    "boot": "Starting Home Board...",
    "reboot": "Home Board is restarting...",
    "shutdown": "Home Board is shutting down...",
}
STILL = (
    "var s = document.createElement('style');"
    "s.textContent = '.blocks { animation: none !important; transform: none !important; }"
    " .hide { visibility: hidden !important; }"
    " .clear, .clear body { background: transparent !important; }"
    " .clear body * { visibility: hidden; } .clear .show { visibility: visible !important; }';"
    "document.head.appendChild(s);"
)


def rect(cdp: Cdp, selector: str) -> dict:
    return cdp.eval(
        f"(() => {{ const r = document.querySelector('{selector}').getBoundingClientRect();"
        " return {x: r.left, y: r.top, w: r.width, h: r.height}; })()"
    )


def clip(r: dict, pad: int = 0) -> dict:
    x0, y0 = math.floor(r["x"]) - pad, math.floor(r["y"]) - pad
    x1 = math.ceil(r["x"] + r["w"]) + pad
    y1 = math.ceil(r["y"] + r["h"]) + pad
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0, "scale": 1}


def capture(cdp: Cdp, out: Path, area: dict | None = None) -> None:
    params = {"format": "png"}
    if area:
        params["clip"] = area
    shot = cdp.call("Page.captureScreenshot", **params)
    out.write_bytes(base64.b64decode(shot["data"]))
    where = f" at {area['x']},{area['y']} size {area['width']}x{area['height']}" if area else ""
    print(f"wrote {out.relative_to(ROOT)}{where}")


def set_classes(cdp: Cdp, clear: bool, hide: list[str], show: list[str]) -> None:
    script = [
        f"document.documentElement.classList.toggle('clear', {'true' if clear else 'false'});",
        "document.querySelectorAll('.hide, .show').forEach(e => e.classList.remove('hide', 'show'));",
    ]
    script += [f"document.querySelector('{s}').classList.add('hide');" for s in hide]
    script += [f"document.querySelector('{s}').classList.add('show');" for s in show]
    cdp.eval("".join(script))
    cdp.call(
        "Emulation.setDefaultBackgroundColorOverride",
        **({"color": {"r": 0, "g": 0, "b": 0, "a": 0}} if clear else {}),
    )
    time.sleep(0.2)


def export_plymouth(chrome: str, profile: Path) -> None:
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
            f"--window-size={WIDTH},{HEIGHT}",
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
                width=WIDTH,
                height=HEIGHT,
                deviceScaleFactor=1,
                mobile=False,
            )
            cdp.call("Page.navigate", url=BOOT.as_uri())
            cdp.wait("document.readyState === 'complete'", "the boot page")
            cdp.eval(STILL)
            THEME.mkdir(parents=True, exist_ok=True)

            set_classes(cdp, False, ["#label", ".bar"], [])
            capture(cdp, THEME / "background.png")

            bar = rect(cdp, ".bar")
            set_classes(cdp, False, ["#label", ".blocks"], [])
            capture(cdp, THEME / "track.png", clip(bar))

            blocks = rect(cdp, ".blocks")
            set_classes(cdp, True, [], [".blocks"])
            capture(cdp, THEME / "blocks.png", clip(blocks))

            for mode, text in LABELS.items():
                cdp.eval(f"document.getElementById('label').textContent = {text!r}")
                label = cdp.eval(
                    "(() => { const n = document.createRange();"
                    " n.selectNodeContents(document.getElementById('label'));"
                    " const r = n.getBoundingClientRect();"
                    " return {x: r.left, y: r.top, w: r.width, h: r.height}; })()"
                )
                set_classes(cdp, True, [], ["#label"])
                capture(cdp, THEME / f"label-{mode}.png", clip(label, 2))

            print(f"bar {bar}")
            print(f"blocks {blocks}")
            cdp.send("Browser.close")
        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)


def main() -> None:
    bare = "--bare" in sys.argv[1:]
    plymouth = "--plymouth" in sys.argv[1:]
    chrome = find_chrome()
    tmp = Path(tempfile.mkdtemp(prefix="homeboard-shot-"))
    try:
        if plymouth:
            export_plymouth(chrome, tmp / "chrome")
            return
        SHOTS.mkdir(exist_ok=True)
        url = BOOT.as_uri() + ("?bare=1" if bare else "")
        name = f"boot-{WIDTH}x{HEIGHT}" + ("-bare" if bare else "") + ".png"
        shoot(chrome, url, WIDTH, HEIGHT, SHOTS / name, tmp / "chrome")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
