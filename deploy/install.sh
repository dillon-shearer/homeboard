#!/usr/bin/env bash
set -euo pipefail

REPO_URL=https://github.com/dillon-shearer/homeboard.git
APP_DIR=/opt/homeboard
DATA_DIR=/var/lib/homeboard
KIOSK_USER=dillon
CONFIG_TXT=/boot/firmware/config.txt
ETC_DIR=/etc/homeboard
HTTPS_ENV="$ETC_DIR/https.env"
DNS_CREDENTIALS="$ETC_DIR/dns-credentials.ini"
API_DROPIN_DIR=/etc/systemd/system/homeboard-api.service.d

HOST=""
DNS=""
EMAIL=""
NEW_TOKEN=0
while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST="${2:?--host needs a value}"; shift 2 ;;
        --dns) DNS="${2:?--dns needs a value}"; shift 2 ;;
        --email) EMAIL="${2:?--email needs a value}"; shift 2 ;;
        --new-token) NEW_TOKEN=1; shift ;;
        -*) echo "Unknown option: $1" >&2; exit 1 ;;
        *) REPO_URL="$1"; shift ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this with sudo: sudo bash install.sh" >&2
    exit 1
fi

if [ -z "$HOST" ] && [ -f "$HTTPS_ENV" ]; then
    . "$HTTPS_ENV"
fi
if [ -n "$HOST" ]; then
    if [ -z "$DNS" ] && [ -f "$HTTPS_ENV" ]; then
        DNS="$(sed -n 's/^DNS=//p' "$HTTPS_ENV")"
    fi
    if ! printf '%s' "$HOST" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$'; then
        echo "--host must be a plain name like board.example.com" >&2
        exit 1
    fi
    if [ -n "$EMAIL" ] && ! printf '%s' "$EMAIL" | grep -Eq '^[^[:space:]@]+@[^[:space:]@]+$'; then
        echo "--email must be an email address" >&2
        exit 1
    fi
    case "$DNS" in
        cloudflare) DNS_KEY=dns_cloudflare_api_token ;;
        digitalocean) DNS_KEY=dns_digitalocean_token ;;
        "") echo "--dns is needed with --host, for example --dns cloudflare" >&2; exit 1 ;;
        *) echo "DNS provider $DNS is not supported; use cloudflare or digitalocean" >&2; exit 1 ;;
    esac
fi

echo "== Installing packages"
systemctl stop homeboard-api.service 2>/dev/null || true
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git python3-venv cage chromium fonts-noto libheif1 curl iw nginx
if [ -n "$HOST" ]; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y certbot "python3-certbot-dns-$DNS"
fi

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

echo "== Setting up the web server"
install -m 644 "$APP_DIR"/deploy/nginx/homeboard-proxy.conf /etc/nginx/snippets/
rm -f /etc/nginx/sites-enabled/default
if [ -n "$HOST" ]; then
    echo "== Getting the HTTPS certificate for $HOST"
    install -d -m 700 -o root -g root "$ETC_DIR"
    printf 'HOST=%s\nDNS=%s\nEMAIL=%s\n' "$HOST" "$DNS" "$EMAIL" > "$HTTPS_ENV"
    chmod 600 "$HTTPS_ENV"
    if [ "$NEW_TOKEN" -eq 1 ] || [ ! -s "$DNS_CREDENTIALS" ]; then
        if [ ! -r /dev/tty ]; then
            echo "No terminal to ask for the DNS API token; run this from an SSH window" >&2
            exit 1
        fi
        read -rsp "Paste the DNS API token and press Enter (nothing shows): " TOKEN < /dev/tty
        echo
        if [ -z "$TOKEN" ]; then
            echo "No token given" >&2
            exit 1
        fi
        (umask 077 && printf '%s = %s\n' "$DNS_KEY" "$TOKEN" > "$DNS_CREDENTIALS")
        unset TOKEN
    fi
    chown root:root "$DNS_CREDENTIALS"
    chmod 600 "$DNS_CREDENTIALS"
    if [ -n "$EMAIL" ]; then
        EMAIL_ARGS=(--email "$EMAIL")
    else
        EMAIL_ARGS=(--register-unsafely-without-email)
    fi
    certbot certonly --non-interactive --agree-tos "${EMAIL_ARGS[@]}" \
        --authenticator "dns-$DNS" \
        "--dns-$DNS-credentials" "$DNS_CREDENTIALS" \
        "--dns-$DNS-propagation-seconds" 30 \
        --cert-name "$HOST" -d "$HOST" --keep-until-expiring
    install -d /etc/letsencrypt/renewal-hooks/deploy
    install -m 755 "$APP_DIR"/deploy/certbot-reload-nginx.sh \
        /etc/letsencrypt/renewal-hooks/deploy/homeboard-reload-nginx
    sed "s/__HOST__/$HOST/g" "$APP_DIR"/deploy/nginx/homeboard-https.conf \
        >/etc/nginx/sites-available/homeboard
    mkdir -p "$API_DROPIN_DIR"
    printf '[Service]\nEnvironment=HOMEBOARD_HOST=%s\n' "$HOST" > "$API_DROPIN_DIR/homeboard-host.conf"
else
    install -m 644 "$APP_DIR"/deploy/nginx/homeboard-http.conf /etc/nginx/sites-available/homeboard
    rm -f /etc/systemd/system/homeboard-api.service.d/homeboard-host.conf
fi
ln -sf /etc/nginx/sites-available/homeboard /etc/nginx/sites-enabled/homeboard
nginx -t

echo "== Enabling services"
systemctl daemon-reload
systemctl disable getty@tty1.service || true
systemctl enable \
    homeboard-api.service \
    homeboard-kiosk.service \
    wifi-powersave-off.service \
    homeboard-kiosk-restart.timer \
    homeboard-backup.timer \
    nginx.service
if [ -n "$HOST" ]; then
    systemctl enable --now certbot.timer
fi
systemctl restart homeboard-api.service nginx.service

echo
echo "Install finished. reboot now: sudo reboot"
