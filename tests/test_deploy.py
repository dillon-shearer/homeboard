import configparser
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"

UNITS = [
    "homeboard-api.service",
    "homeboard-kiosk.service",
    "homeboard-kiosk-restart.service",
    "homeboard-kiosk-restart.timer",
    "homeboard-backup.service",
    "homeboard-backup.timer",
    "wifi-powersave-off.service",
]


def load(name):
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read_string((DEPLOY / name).read_text(encoding="utf-8"))
    return parser


@pytest.mark.parametrize("name", UNITS)
def test_unit_parses_with_lf_endings(name):
    raw = (DEPLOY / name).read_bytes()
    assert b"\r\n" not in raw
    parser = load(name)
    assert parser.has_section("Unit")


@pytest.mark.parametrize("name", ["install.sh", "kiosk.sh"])
def test_scripts_use_lf_endings(name):
    raw = (DEPLOY / name).read_bytes()
    assert raw.startswith(b"#!/usr/bin/env bash\n")
    assert b"\r\n" not in raw


def test_api_service():
    s = load("homeboard-api.service")["Service"]
    u = load("homeboard-api.service")["Unit"]
    exec_start = s["ExecStart"].split()
    assert exec_start[0] == "/opt/homeboard/.venv/bin/uvicorn"
    assert "app.main:app" in exec_start
    assert exec_start[exec_start.index("--host") + 1] == "0.0.0.0"
    assert exec_start[exec_start.index("--port") + 1] == "80"
    assert s["User"] == "homeboard"
    assert s["WorkingDirectory"] == "/opt/homeboard"
    assert s["Environment"] == "HOMEBOARD_DATA=/var/lib/homeboard"
    assert s["AmbientCapabilities"] == "CAP_NET_BIND_SERVICE"
    assert s["Restart"] == "always"
    assert s["RestartSec"] == "2"
    assert "network-online.target" in u["After"].split()
    assert "network-online.target" in u["Wants"].split()


def test_kiosk_service():
    p = load("homeboard-kiosk.service")
    s, u = p["Service"], p["Unit"]
    assert "homeboard-api.service" in u["After"].split()
    assert s["Restart"] == "always"
    assert s["RestartSec"] == "3"
    assert s["User"] == "dillon"
    assert s["TTYPath"] == "/dev/tty1"
    assert s["StandardInput"] == "tty"
    assert s["PAMName"] == "login"
    assert s["Environment"].startswith("XDG_RUNTIME_DIR=")
    assert s["ExecStart"].endswith("/opt/homeboard/deploy/kiosk.sh")


def test_kiosk_restart_timer():
    t = load("homeboard-kiosk-restart.timer")["Timer"]
    assert t["OnCalendar"] == "*-*-* 04:00:00"
    assert t["Unit"] == "homeboard-kiosk-restart.service"
    s = load("homeboard-kiosk-restart.service")["Service"]
    assert s["ExecStart"] == "/bin/systemctl restart homeboard-kiosk.service"


def test_backup_timer():
    t = load("homeboard-backup.timer")["Timer"]
    assert "OnCalendar" in t
    assert t["Unit"] == "homeboard-backup.service"
    s = load("homeboard-backup.service")["Service"]
    cmd = s["ExecStart"]
    assert "/var/backups/homeboard-$$(date +%%Y%%m%%d).tar.gz" in cmd
    assert "-C /var/lib homeboard" in cmd
    assert "tail -n +8" in cmd


def test_wifi_powersave_off():
    p = load("wifi-powersave-off.service")
    assert p["Service"]["Type"] == "oneshot"
    assert p["Service"]["ExecStart"].endswith("iw dev wlan0 set power_save off")
    assert "network-online.target" in p["Unit"]["After"].split()


def test_install_enables_every_unit():
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    for name in [
        "homeboard-api.service",
        "homeboard-kiosk.service",
        "wifi-powersave-off.service",
        "homeboard-kiosk-restart.timer",
        "homeboard-backup.timer",
    ]:
        assert name in text
    assert "reboot now" in text


def test_setup_guide_has_steps_and_troubleshooting():
    text = (DEPLOY / "PI_SETUP.md").read_text(encoding="utf-8")
    assert "Raspberry Pi Imager" in text
    assert "ssh dillon@homeboard.local" in text
    assert "journalctl -u homeboard-api -n 50" in text
    assert "## Troubleshooting" in text
    assert text.isascii()
