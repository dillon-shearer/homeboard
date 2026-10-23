import shutil
import sys
import tempfile
from pathlib import Path

from shot import ROOT, SHOTS, find_chrome, shoot

BOOT = ROOT / "deploy" / "splash" / "boot.html"
WIDTH, HEIGHT = 1920, 1080


def main() -> None:
    bare = "--bare" in sys.argv[1:]
    chrome = find_chrome()
    SHOTS.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="homeboard-shot-"))
    url = BOOT.as_uri() + ("?bare=1" if bare else "")
    name = f"boot-{WIDTH}x{HEIGHT}" + ("-bare" if bare else "") + ".png"
    try:
        shoot(chrome, url, WIDTH, HEIGHT, SHOTS / name, tmp / "chrome")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
