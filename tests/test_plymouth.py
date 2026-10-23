import configparser
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
THEME = DEPLOY / "plymouth" / "homeboard"
CMDLINE_WORDS = [
    "quiet",
    "splash",
    "loglevel=0",
    "logo.nologo",
    "vt.global_cursor_default=0",
    "plymouth.ignore-serial-consoles",
]


def load(path):
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read_string(path.read_text(encoding="utf-8"))
    return parser


@pytest.mark.parametrize(
    "name",
    [
        "homeboard.plymouth",
        "homeboard.script",
        "background.png",
        "track.png",
        "blocks.png",
        "label-boot.png",
        "label-reboot.png",
        "label-shutdown.png",
    ],
)
def test_theme_file_exists(name):
    path = THEME / name
    assert path.is_file()
    if name.endswith(".png"):
        assert path.read_bytes().startswith(b"\x89PNG")
    else:
        assert b"\r\n" not in path.read_bytes()


def test_theme_uses_the_script_module():
    p = load(THEME / "homeboard.plymouth")
    assert p["Plymouth Theme"]["ModuleName"] == "script"
    assert p["script"]["ImageDir"] == "/usr/share/plymouth/themes/homeboard"
    assert p["script"]["ScriptFile"] == "/usr/share/plymouth/themes/homeboard/homeboard.script"


def test_script_scales_and_animates_the_blocks():
    text = (THEME / "homeboard.script").read_text(encoding="utf-8")
    for needle in [
        "Window.GetWidth()",
        "Window.GetHeight()",
        "Plymouth.SetRefreshFunction(refresh)",
        "Plymouth.GetMode()",
        'scaled("blocks.png")',
        ".Crop(",
        "SetImage(",
        "steps = 14;",
    ]:
        assert needle in text
    for name in ["background.png", "track.png", "label-boot.png", "label-reboot.png"]:
        assert f'"{name}"' in text


def test_install_sets_up_the_boot_screen():
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert "plymouth plymouth-themes" in text
    assert "THEME_DIR=/usr/share/plymouth/themes/homeboard" in text
    assert '"$APP_DIR"/deploy/plymouth/homeboard/* "$THEME_DIR"/' in text
    assert "plymouth-set-default-theme -R homeboard" in text
    assert "disable_splash=1" in text
    assert "CMDLINE_TXT=/boot/firmware/cmdline.txt" in text
    assert 'cp "$CMDLINE_TXT" "$CMDLINE_TXT.homeboard.bak"' in text
    assert "SPLASH_WORDS=(" + " ".join(CMDLINE_WORDS) + ")" in text
    assert "/etc/systemd/system/plymouth-quit.service.d/homeboard.conf" in text
    assert text.index("== Getting the code") < text.index("== Boot screen")


def test_plymouth_quit_keeps_the_last_frame():
    lines = (DEPLOY / "plymouth" / "plymouth-quit.conf").read_text(encoding="utf-8").splitlines()
    assert lines == [
        "[Unit]",
        "After=homeboard-api.service nginx.service",
        "",
        "[Service]",
        "ExecStart=",
        "ExecStart=-/usr/bin/plymouth quit --retain-splash",
    ]


def test_kiosk_starts_after_plymouth_quits():
    u = load(DEPLOY / "homeboard-kiosk.service")["Unit"]
    assert "plymouth-quit.service" in u["After"].split()
    assert "plymouth-quit.service" in u["Wants"].split()
    assert "plymouth-quit.service" not in u["Conflicts"].split()
    for name in ["plymouth-halt", "plymouth-poweroff", "plymouth-reboot", "plymouth-kexec"]:
        assert f"{name}.service" in u["Before"].split()
