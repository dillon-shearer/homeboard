import configparser
import importlib.util
from pathlib import Path

from PIL import Image, ImageSequence

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
COVER = DEPLOY / "splash" / "cover"

spec = importlib.util.spec_from_file_location("still", DEPLOY / "splash" / "still.py")
still = importlib.util.module_from_spec(spec)
spec.loader.exec_module(still)


def load(name):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read_string((DEPLOY / name).read_text(encoding="utf-8"))
    return parser


def connector(drm, name, status, modes):
    path = drm / name
    path.mkdir(parents=True)
    (path / "status").write_text(status + "\n")
    (path / "modes").write_text("".join(m + "\n" for m in modes))


def test_screen_size_reads_the_first_connected_screen(tmp_path):
    connector(tmp_path, "card1-HDMI-A-1", "disconnected", [])
    connector(tmp_path, "card1-HDMI-A-2", "connected", ["4096x2160", "1920x1080"])
    connector(tmp_path, "card1-Writeback-1", "unknown", ["1024x768"])
    assert still.screen_size(tmp_path) == (4096, 2160)


def test_screen_size_handles_interlaced_modes(tmp_path):
    connector(tmp_path, "card1-HDMI-A-1", "connected", ["1920x1080i"])
    assert still.screen_size(tmp_path) == (1920, 1080)


def test_screen_size_falls_back_to_1080p(tmp_path):
    connector(tmp_path, "card1-HDMI-A-1", "disconnected", [])
    assert still.screen_size(tmp_path) == (1920, 1080)
    assert still.screen_size(tmp_path / "missing") == (1920, 1080)


def test_still_matches_the_cover_layout_at_1080p():
    ground, frames, bar = still.render(1920, 1080)
    assert ground.size == (1920, 1080)
    track = Image.open(COVER / "track.png").convert("RGB")
    assert bar == (518, 747, 518 + track.width, 747 + track.height)
    for x, y in [(10, 10), (400, 17), (880, 30)]:
        assert ground.getpixel((518 + x, 747 + y)) == track.getpixel((x, y))
    background = Image.open(COVER / "background.png").convert("RGB")
    assert ground.getpixel((5, 5)) == background.getpixel((5, 5))
    label = (516, 696, 516 + 317, 696 + 40)
    assert ground.crop(label).tobytes() != background.crop(label).tobytes()
    assert len(frames) == 14
    for frame in frames:
        assert frame.getpixel((5, 5)) == ground.getpixel((5, 5))
        assert frame.crop(label).tobytes() == ground.crop(label).tobytes()


def white_run(frame, y=760):
    xs = [x for x in range(519, 1401) if min(frame.getpixel((x, y))) > 230]
    return (xs[0], xs[-1]) if xs else None


def test_blocks_march_left_to_right_inside_the_bar():
    _, frames, _ = still.render(1920, 1080)
    runs = [white_run(frame) for frame in frames]
    assert all(runs)
    lefts = [r[0] for r in runs]
    rights = [r[1] for r in runs]
    assert lefts == sorted(lefts)
    assert rights[:6] == sorted(rights[:6])
    assert lefts[0] < 530 and rights[-1] > 1390
    assert len(set(runs)) == 14


def test_still_keeps_the_screen_shape_and_caps_the_width():
    assert still.render(4096, 2160)[0].size == (1920, 1012)
    assert still.render(1280, 720)[0].size == (1280, 720)


def test_still_main_writes_a_looping_animated_gif(tmp_path):
    out = tmp_path / "boot.gif"
    still.main(["still.py", str(out), "3840", "2160"])
    gif = Image.open(out)
    assert gif.format == "GIF"
    assert gif.size == (1920, 1080)
    assert gif.n_frames == 14
    assert gif.info["loop"] == 0
    assert gif.info["duration"] == 100
    frames = [f.convert("RGB") for f in ImageSequence.Iterator(gif)]
    assert len({white_run(f) for f in frames}) == 14
    assert len({f.getpixel((5, 5)) for f in frames}) == 1


def test_kiosk_shows_the_cover_before_chromium():
    text = (DEPLOY / "kiosk.sh").read_text(encoding="utf-8")
    assert "COVER=/var/cache/homeboard/boot.gif" in text
    assert 'exec cage -- /bin/bash "$0" session' in text
    assert 'swayimg --fullscreen --scale=fit --no-sway "$COVER" &' in text
    assert text.index("exec cage") < text.index("swayimg") < text.index("exec chromium")
    assert text.count("exec chromium") == 1


def test_install_draws_the_still_and_enables_preload():
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert " rsync swayimg \\\n" in text
    assert "install -d -m 755 /var/cache/homeboard" in text
    assert "rm -f /var/cache/homeboard/boot.png\n" in text
    assert '"$APP_DIR/deploy/splash/still.py" /var/cache/homeboard/boot.gif' in text
    assert text.index("== Setting up Python") < text.index("== Kiosk start screen")
    assert "    homeboard-preload.service \\\n" in text


def test_preload_service_stops_when_the_kiosk_starts():
    raw = (DEPLOY / "homeboard-preload.service").read_bytes()
    assert b"\r\n" not in raw
    p = load("homeboard-preload.service")
    assert p["Service"]["ExecStart"] == "/bin/bash /opt/homeboard/deploy/preload.sh"
    assert p["Install"]["WantedBy"] == "multi-user.target"
    k = load("homeboard-kiosk.service")["Service"]
    assert k["ExecStartPre"] == "-+/bin/systemctl stop homeboard-preload.service"
    assert "homeboard-preload.service" not in load("homeboard-kiosk.service")["Unit"]["After"]


def test_preload_reads_the_still_and_programs_first():
    raw = (DEPLOY / "preload.sh").read_bytes()
    assert raw.startswith(b"#!/usr/bin/env bash\n")
    assert b"\r\n" not in raw
    text = raw.decode()
    for name in ["/usr/bin/swayimg", "/usr/bin/cage", "libgallium-", "/usr/lib/chromium/chromium"]:
        assert name in text
    assert text.index("/var/cache/homeboard/boot.gif") < text.index("for program in")
    assert text.index("/usr/bin/swayimg") < text.index("/usr/lib/chromium/chromium")
