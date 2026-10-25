import importlib.util
from pathlib import Path

from PIL import Image

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"


def module(name):
    spec = importlib.util.spec_from_file_location(name, DEPLOY / "splash" / f"{name}.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


overlay = module("overlay")
still = module("still")


def test_kiosk_starts_the_overlay_under_the_cover_before_chromium():
    raw = (DEPLOY / "kiosk.sh").read_bytes()
    assert b"\r\n" not in raw
    text = raw.decode("ascii")
    line = (
        '    /opt/homeboard/.venv/bin/python /opt/homeboard/deploy/splash/overlay.py "$COVER" &\n'
    )
    assert line in text
    block = text.split('if [ -r "$COVER" ]; then\n', 1)[1].split("\nfi\n", 1)[0]
    assert line.rstrip("\n") in block
    assert (
        text.index("swayimg") < text.index("sleep 0.5") < text.index(line) < text.index("seq 1 150")
    )
    assert text.index(line) < text.index("exec chromium")


def test_overlay_is_plain_ascii_with_lf_endings():
    raw = (DEPLOY / "splash" / "overlay.py").read_bytes()
    assert b"\r\n" not in raw
    raw.decode("ascii")


def test_pixels_split_into_splash_black_blank_and_photo():
    background = Image.open(DEPLOY / "splash" / "cover" / "background.png").convert("RGB")
    assert overlay.kind(background.getpixel((0, 0))) == "ground"
    assert overlay.kind((27, 63, 143)) == "ground"
    assert overlay.kind((0, 0, 0)) == "black"
    assert overlay.kind((60, 60, 60)) == "blank"
    assert overlay.kind((255, 255, 255)) == "blank"
    assert overlay.kind((207, 159, 84)) == "other"


def test_load_keeps_the_first_frame_and_one_strip_per_frame(tmp_path):
    gif = tmp_path / "boot.gif"
    still.main(["still.py", str(gif), "1024", "600"])
    (width, height), base, box, strips, delay = overlay.load(gif)
    assert (width, height) == (1024, 600)
    assert len(base) == width * height * 4
    assert delay == 0.1
    assert len(strips) == 14
    x0, y0, x1, y1 = box
    assert 0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height
    assert all(len(strip) == (x1 - x0) * (y1 - y0) * 4 for strip in strips)
    assert len(set(strips)) == 14
    first = Image.open(gif).convert("RGBA")
    b, g, r, a = base[:4]
    assert (r, g, b, a) == first.getpixel((0, 0))


def test_wire_strings_pad_to_four_bytes():
    for text in ["", "abc", "wl_compositor", "homeboard-overlay"]:
        raw = overlay.s(text)
        assert len(raw) % 4 == 0
        assert overlay.read_string(raw + b"rest", 0) == (text, len(raw))
