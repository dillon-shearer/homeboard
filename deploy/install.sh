#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${1:-https://github.com/dillon-shearer/homeboard.git}"
APP_DIR=/opt/homeboard
DATA_DIR=/var/lib/homeboard
KIOSK_USER=dillon
CONFIG_TXT=/boot/firmware/config.txt

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this with sudo: sudo bash install.sh" >&2
    exit 1
fi

echo "== Installing packages"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git python3-venv cage chromium fonts-noto libheif1 curl iw

echo "== Creating the homeboard user"
if ! id homeboard >/dev/null 2>&1; then
    useradd --system --home-dir "$DATA_DIR" --no-create-home --shell /usr/sbin/nologin homeboard
fi

if id "$KIOSK_USER" >/dev/null 2>&1; then
    usermod -aG video,render,input "$KIOSK_USER"
    if [ "$(id -u "$KIOSK_USER")" -ne 1000 ]; then
        echo "Warning: $KIOSK_USER is not uid 1000; edit XDG_RUNTIME_DIR in homeboard-kiosk.service" >&2
    fi
else
    echo "Warning: user $KIOSK_USER does not exist; the kiosk will not start" >&2
fi

echo "== Getting the code"
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" remote set-url origin "$REPO_URL"
    git -C "$APP_DIR" pull --ff-only
elif [ -e "$APP_DIR" ]; then
    echo "$APP_DIR exists but is not a git clone; move it away and run again" >&2
    exit 1
else
    git clone "$REPO_URL" "$APP_DIR"
fi

echo "== Setting up Python"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "== Creating the data folders"
mkdir -p "$DATA_DIR/photos" "$DATA_DIR/thumbs"
chown -R homeboard:homeboard "$DATA_DIR"
mkdir -p /var/backups

echo "== Installing services and timers"
install -m 644 "$APP_DIR"/deploy/*.service "$APP_DIR"/deploy/*.timer /etc/systemd/system/

echo "== Hiding the mouse pointer"
install -m 644 "$APP_DIR"/deploy/90-homeboard-ignore-cec-input.rules /etc/udev/rules.d/
udevadm control --reload

echo "== Display settings"
if [ -f "$CONFIG_TXT" ]; then
    for key in hdmi_force_hotplug disable_overscan; do
        if ! grep -q "^${key}=" "$CONFIG_TXT"; then
            printf '\n[all]\n%s=1\n' "$key" >> "$CONFIG_TXT"
        fi
    done
else
    echo "Warning: $CONFIG_TXT not found; skipping display settings" >&2
fi

echo "== Keeping logs in memory"
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=volatile\n' > /etc/systemd/journald.conf.d/homeboard.conf
systemctl restart systemd-journald

echo "== Reducing SD card writes"
if ! awk '$1 !~ /^#/ && $2 == "/" { if ($4 ~ /(^|,)noatime(,|$)/) found = 1 } END { exit !found }' /etc/fstab; then
    cp /etc/fstab /etc/fstab.homeboard.bak
    awk 'BEGIN { OFS = "\t" } $1 !~ /^#/ && $2 == "/" { $4 = $4 ",noatime" } { print }' \
        /etc/fstab.homeboard.bak > /etc/fstab
fi

if systemctl list-unit-files dphys-swapfile.service >/dev/null 2>&1; then
    dphys-swapfile swapoff || true
    systemctl disable --now dphys-swapfile.service || true
fi

echo "== Enabling services"
systemctl daemon-reload
systemctl disable getty@tty1.service || true
systemctl enable \
    homeboard-api.service \
    homeboard-kiosk.service \
    wifi-powersave-off.service \
    homeboard-kiosk-restart.timer \
    homeboard-backup.timer

echo
echo "Install finished. reboot now: sudo reboot"
