#!/usr/bin/env bash
set -euo pipefail

REPO_URL=https://github.com/dillon-shearer/homeboard.git
APP_DIR=/opt/homeboard
DATA_DIR=/var/lib/homeboard
KIOSK_USER=dillon
CONFIG_TXT=/boot/firmware/config.txt
CMDLINE_TXT=/boot/firmware/cmdline.txt
THEME_DIR=/usr/share/plymouth/themes/homeboard
ETC_DIR=/etc/homeboard
HTTPS_ENV="$ETC_DIR/https.env"
DNS_CREDENTIALS="$ETC_DIR/dns-credentials.ini"
API_DROPIN_DIR=/etc/systemd/system/homeboard-api.service.d
TUNNEL_ENV="$ETC_DIR/tunnel.env"
PASSWORD_HASH="$ETC_DIR/tunnel-password.hash"

HOST=""
DNS=""
EMAIL=""
NEW_TOKEN=0
TUNNEL=0
NEW_TUNNEL_TOKEN=0
NEW_PASSWORD=0
while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST="${2:?--host needs a value}"; shift 2 ;;
        --dns) DNS="${2:?--dns needs a value}"; shift 2 ;;
        --email) EMAIL="${2:?--email needs a value}"; shift 2 ;;
        --new-token) NEW_TOKEN=1; shift ;;
        --tunnel) TUNNEL=1; shift ;;
        --new-tunnel-token) TUNNEL=1; NEW_TUNNEL_TOKEN=1; shift ;;
        --new-password) TUNNEL=1; NEW_PASSWORD=1; shift ;;
        -*) echo "Unknown option: $1" >&2; exit 1 ;;
        *) REPO_URL="$1"; shift ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this with sudo: sudo bash install.sh" >&2
    exit 1
fi

if [ -s "$TUNNEL_ENV" ]; then
    TUNNEL=1
fi
if [ "$TUNNEL" -eq 1 ] && [ ! -r /dev/tty ] && { [ "$NEW_TUNNEL_TOKEN" -eq 1 ] || [ "$NEW_PASSWORD" -eq 1 ] || [ ! -s "$TUNNEL_ENV" ] || [ ! -s "$PASSWORD_HASH" ]; }; then
    echo "No terminal to ask for the tunnel token or password; run this from an SSH window" >&2
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
trap 'systemctl start homeboard-api.service || true' EXIT
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git python3-venv cage chromium fonts-noto libheif1 curl iw nginx rsync plymouth plymouth-themes
if [ -n "$HOST" ]; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y certbot "python3-certbot-dns-$DNS"
fi
if [ "$TUNNEL" -eq 1 ]; then
    install -d -m 755 /usr/share/keyrings
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" > /etc/apt/sources.list.d/cloudflared.list
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y cloudflared
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
mkdir -p /var/backups/homeboard-db /var/backups/homeboard-photos
chown homeboard:homeboard /var/backups/homeboard-db /var/backups/homeboard-photos

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
    if ! grep -q "^disable_splash=" "$CONFIG_TXT"; then
        printf '\n[all]\ndisable_splash=1\n' >> "$CONFIG_TXT"
    fi
else
    echo "Warning: $CONFIG_TXT not found; skipping display settings" >&2
fi

echo "== Boot screen"
install -d "$THEME_DIR"
install -m 644 "$APP_DIR"/deploy/plymouth/homeboard/* "$THEME_DIR"/
install -d /etc/systemd/system/plymouth-quit.service.d
install -m 644 "$APP_DIR"/deploy/plymouth/plymouth-quit.conf /etc/systemd/system/plymouth-quit.service.d/homeboard.conf
plymouth-set-default-theme -R homeboard
if [ -f "$CMDLINE_TXT" ]; then
    if [ ! -e "$CMDLINE_TXT.homeboard.bak" ]; then
        cp "$CMDLINE_TXT" "$CMDLINE_TXT.homeboard.bak"
    fi
    SPLASH_WORDS=(quiet splash loglevel=0 logo.nologo vt.global_cursor_default=0 plymouth.ignore-serial-consoles)
    read -r -a OLD_WORDS < "$CMDLINE_TXT" || true
    NEW_WORDS=()
    for word in "${OLD_WORDS[@]}"; do
        case " ${SPLASH_WORDS[*]} " in
            *" $word "*) continue ;;
        esac
        case "$word" in
            loglevel=*) continue ;;
        esac
        NEW_WORDS+=("$word")
    done
    NEW_WORDS+=("${SPLASH_WORDS[@]}")
    if [ "${NEW_WORDS[*]}" != "${OLD_WORDS[*]}" ]; then
        printf '%s\n' "${NEW_WORDS[*]}" > "$CMDLINE_TXT.homeboard.tmp"
        mv -f "$CMDLINE_TXT.homeboard.tmp" "$CMDLINE_TXT"
    fi
else
    echo "Warning: $CMDLINE_TXT not found; skipping boot screen settings" >&2
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
install -m 644 "$APP_DIR"/deploy/nginx/homeboard-map.conf /etc/nginx/conf.d/
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
if [ "$TUNNEL" -eq 1 ]; then
    echo "== Setting up the Cloudflare Tunnel and its password"
    install -d -m 700 -o root -g root "$ETC_DIR"
    if [ "$NEW_TUNNEL_TOKEN" -eq 1 ] || [ ! -s "$TUNNEL_ENV" ]; then
        read -rsp "Paste the tunnel token and press Enter (nothing shows): " TUNNEL_TOKEN < /dev/tty
        echo
        TUNNEL_TOKEN="${TUNNEL_TOKEN##*service install }"
        TUNNEL_TOKEN="$(printf '%s' "$TUNNEL_TOKEN" | tr -d '[:space:]')"
        if [ -z "$TUNNEL_TOKEN" ]; then
            echo "No tunnel token given" >&2
            exit 1
        fi
        (umask 077 && printf 'TUNNEL_TOKEN=%s\n' "$TUNNEL_TOKEN" > "$TUNNEL_ENV")
        unset TUNNEL_TOKEN
    fi
    chown root:root "$TUNNEL_ENV"
    chmod 600 "$TUNNEL_ENV"
    if [ "$NEW_PASSWORD" -eq 1 ] || [ ! -s "$PASSWORD_HASH" ]; then
        read -rsp "Type the board password and press Enter (nothing shows): " PASSWORD < /dev/tty
        echo
        read -rsp "Type it again: " PASSWORD_AGAIN < /dev/tty
        echo
        if [ -z "$PASSWORD" ] || [ "$PASSWORD" != "$PASSWORD_AGAIN" ]; then
            unset PASSWORD PASSWORD_AGAIN
            echo "The two passwords were empty or did not match; run this again" >&2
            exit 1
        fi
        PASSWORD_LINE="$(printf '%s' "$PASSWORD" | (cd "$APP_DIR" && .venv/bin/python -m app.auth))"
        unset PASSWORD PASSWORD_AGAIN
        (umask 077 && printf '%s\n' "$PASSWORD_LINE" > "$PASSWORD_HASH")
        unset PASSWORD_LINE
    fi
    chown root:root "$PASSWORD_HASH"
    chmod 600 "$PASSWORD_HASH"
    install -m 644 "$APP_DIR"/deploy/nginx/homeboard-tunnel.conf /etc/nginx/sites-available/homeboard-tunnel
    ln -sf /etc/nginx/sites-available/homeboard-tunnel /etc/nginx/sites-enabled/homeboard-tunnel
    mkdir -p "$API_DROPIN_DIR"
    printf '[Service]\nLoadCredential=tunnel-password:%s\n' "$PASSWORD_HASH" > "$API_DROPIN_DIR/homeboard-tunnel.conf"
else
    rm -f /etc/nginx/sites-enabled/homeboard-tunnel "$API_DROPIN_DIR/homeboard-tunnel.conf"
fi
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
if [ "$TUNNEL" -eq 1 ]; then
    systemctl enable homeboard-tunnel.service
    systemctl restart homeboard-tunnel.service
else
    systemctl disable --now homeboard-tunnel.service 2>/dev/null || true
fi

echo
echo "Install finished. reboot now: sudo reboot"
