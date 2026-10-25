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
    "homeboard-helper.service",
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


@pytest.mark.parametrize("name", ["install.sh", "kiosk.sh", "homeboard-backup.sh"])
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
    assert exec_start[exec_start.index("--host") + 1] == "127.0.0.1"
    assert exec_start[exec_start.index("--port") + 1] == "8000"
    assert "--proxy-headers" in exec_start
    assert exec_start[exec_start.index("--forwarded-allow-ips") + 1] == "127.0.0.1"
    assert s["User"] == "homeboard"
    assert s["WorkingDirectory"] == "/opt/homeboard"
    assert s["Environment"] == "HOMEBOARD_DATA=/var/lib/homeboard"
    assert s["Restart"] == "always"
    assert s["RestartSec"] == "2"
    assert "network-online.target" in u["After"].split()
    assert "network-online.target" in u["Wants"].split()


def test_kiosk_service():
    p = load("homeboard-kiosk.service")
    s, u = p["Service"], p["Unit"]
    for name in ["homeboard-api.service", "nginx.service"]:
        assert name not in u["After"].split()
        assert name in u["Wants"].split()
    assert "Before" not in u
    assert "plymouth" not in (DEPLOY / "homeboard-kiosk.service").read_text(encoding="utf-8")
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
    assert s["User"] == "homeboard"
    assert s["ExecStart"] == "/opt/homeboard/deploy/homeboard-backup.sh"
    script = (DEPLOY / "homeboard-backup.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in script
    assert "tar " not in script
    assert "cp " not in script
    assert "PYTHON=/opt/homeboard/.venv/bin/python" in script
    assert "src.backup(dst)" in script
    assert '"$DATA_DIR/homeboard.db" "$TMP"' in script
    assert 'mv -f "$TMP" "$DB_DIR/homeboard-$(date +%Y%m%d).db"' in script
    assert 'ls -1t "$DB_DIR"/homeboard-*.db | tail -n +8' in script
    assert 'rsync -a --delete "$DATA_DIR/photos" "$DATA_DIR/thumbs" "$PHOTOS_DIR/"' in script
    install = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert " rsync" in install
    assert (
        "chown homeboard:homeboard /var/backups/homeboard-db /var/backups/homeboard-photos"
        in install
    )


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
    assert "## Restore from a backup" in text
    assert "sudo systemctl stop homeboard-api" in text
    assert (
        "sudo rm -f /var/lib/homeboard/homeboard.db-wal /var/lib/homeboard/homeboard.db-shm" in text
    )
    assert "sudo chown -R homeboard:homeboard /var/lib/homeboard" in text
    assert "scp -r dillon@homeboard.local:/var/backups/homeboard-db" in text
    assert text.isascii()


def test_api_service_is_sandboxed():
    s = load("homeboard-api.service")["Service"]
    assert s["NoNewPrivileges"] == "yes"
    assert s["PrivateTmp"] == "yes"
    assert s["ProtectSystem"] == "strict"
    assert s["ReadWritePaths"] == "/var/lib/homeboard"
    assert s["ProtectHome"] == "yes"
    assert s["PrivateDevices"] == "yes"


def test_install_restarts_api_when_it_fails_after_the_stop():
    lines = (DEPLOY / "install.sh").read_text(encoding="utf-8").splitlines()
    stop = lines.index("systemctl stop homeboard-api.service 2>/dev/null || true")
    assert lines[stop + 1] == "trap 'systemctl start homeboard-api.service || true' EXIT"
    assert stop < lines.index("apt-get update")


def test_boot_page_has_every_state():
    text = (DEPLOY / "splash" / "boot.html").read_text(encoding="utf-8")
    for needle in [
        "Starting Home Board...",
        "Home Board is restarting...",
        "Home Board is not responding. It keeps trying.",
        "/healthz",
        "http://localhost/",
        "90000",
        "bare",
    ]:
        assert needle in text
    assert text.isascii()


def test_boot_page_is_self_contained():
    text = (DEPLOY / "splash" / "boot.html").read_text(encoding="utf-8")
    for needle in ["<link", "src=", "@import", "url(", "@font-face"]:
        assert needle not in text


def test_kiosk_waits_for_port_80_only_under_the_cover():
    text = (DEPLOY / "kiosk.sh").read_text(encoding="utf-8")
    assert "file:///opt/homeboard/deploy/splash/boot.html" in text
    assert "for _ in $(seq 1 150); do\n" in text
    assert "    sleep 0.2\n" in text
    assert "curl" not in text
    assert "healthz" not in text
    assert text.index("swayimg") < text.index("seq 1 150") < text.index("exec chromium")
    for flag in [
        "--kiosk",
        "--noerrdialogs",
        "--disable-infobars",
        "--disable-session-crashed-bubble",
        "--incognito",
        "--autoplay-policy=no-user-gesture-required",
        "--ozone-platform=wayland",
    ]:
        assert flag in text


def test_install_drops_plymouth():
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    packages = text.split("apt-get install -y \\\n", 1)[1].split("\n", 1)[0].split()
    assert "swayimg" in packages
    assert not [p for p in packages if "plymouth" in p]
    assert "plymouth-set-default-theme" not in text
    assert "deploy/plymouth" not in text
    assert not (DEPLOY / "plymouth").exists()
    loop = "for unit in plymouth-quit plymouth-halt plymouth-poweroff plymouth-reboot; do\n"
    assert loop in text
    assert '    rm -f "/etc/systemd/system/$unit.service.d/homeboard.conf"\n' in text
    assert "THEME_DIR=/usr/share/plymouth/themes/homeboard\n" in text
    assert 'if [ -d "$THEME_DIR" ]; then\n' in text
    assert '    rm -rf "$THEME_DIR"\n' in text


def test_install_cmdline_words_drop_splash():
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    line = next(x for x in text.splitlines() if x.strip().startswith("SPLASH_WORDS=("))
    words = line.split("(", 1)[1].rstrip(")").split()
    assert words == ["quiet", "loglevel=0", "logo.nologo", "vt.global_cursor_default=0"]
    assert "loglevel=* | splash | plymouth.*) continue ;;" in text


def test_helper_service_is_installed_and_enabled():
    p = load("homeboard-helper.service")
    s = p["Service"]
    assert s["User"] == "dillon"
    assert s["Restart"] == "always"
    assert s["ExecStart"] == "/usr/bin/python3 /opt/homeboard/deploy/helper/helperd.py"
    assert s["EnvironmentFile"] == "-/etc/homeboard/speaker.env"
    assert "NoNewPrivileges" not in s
    assert p["Install"]["WantedBy"] == "multi-user.target"
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert 'install -m 644 "$APP_DIR"/deploy/*.service' in text
    enable = text.split("systemctl enable \\\n", 1)[1].split("\n\n", 1)[0]
    assert "    homeboard-helper.service \\\n" in enable
    assert "systemctl restart homeboard-helper.service" in text


def test_install_sets_up_bluetooth_audio():
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    packages = text.split("apt-get install -y \\\n", 1)[1].split("\nif ", 1)[0].split()
    for name in ["bluez", "pipewire", "pipewire-pulse", "wireplumber", "libspa-0.2-bluetooth"]:
        assert name in packages
    assert 'loginctl enable-linger "$KIOSK_USER"' in text
    assert "--pair-speaker) PAIR_SPEAKER=1; shift ;;" in text
    assert 'SPEAKER_ENV="$ETC_DIR/speaker.env"' in text
    assert "printf 'SPEAKER_MAC=%s\\n'" in text
    assert "AutoEnable=true" in text
    assert "pairing mode with its remote" in text
    pair = text.split("== Pairing the speaker", 1)[1]
    assert pair.index("bluetoothctl pairable on") < pair.index('bluetoothctl pair "$SPEAKER_MAC"')
    assert '*"Bonded: yes"*' in pair


def test_install_sets_up_spotify_credential():
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    assert "--spotify) SPOTIFY=1; shift ;;" in text
    assert "--new-spotify) SPOTIFY=1; NEW_SPOTIFY=1; shift ;;" in text
    assert 'SPOTIFY_ENV="$ETC_DIR/spotify.env"' in text
    assert 'if [ "$NEW_SPOTIFY" -eq 1 ] || [ ! -s "$SPOTIFY_ENV" ]; then' in text
    assert "SPOTIFY_CLIENT_ID=%s\nSPOTIFY_CLIENT_SECRET=%s\n" in text
    assert 'chown root:root "$SPOTIFY_ENV"' in text and 'chmod 600 "$SPOTIFY_ENV"' in text
    assert (
        "printf '[Service]\nLoadCredential=spotify:%s\n' \"$SPOTIFY_ENV\""
        ' > "$API_DROPIN_DIR/homeboard-spotify.conf"'
    ) in text
    assert 'rm -f "$API_DROPIN_DIR/homeboard-spotify.conf"' in text
    assert text.index("homeboard-spotify.conf") < text.index("systemctl daemon-reload")


def test_setup_guide_has_spotify_steps():
    text = (DEPLOY / "PI_SETUP.md").read_text(encoding="utf-8")
    assert "## Spotify" in text
    assert "https://developer.spotify.com/dashboard" in text
    assert "https://homeboard.datawithdillon.com/api/spotify/callback" in text
    assert "sudo bash /opt/homeboard/deploy/install.sh --spotify" in text
    assert "--new-spotify" in text
