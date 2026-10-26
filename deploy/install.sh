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
SPEAKER_ENV="$ETC_DIR/speaker.env"
SPOTIFY_ENV="$ETC_DIR/spotify.env"
SOLOIST_ENV="$ETC_DIR/soloist.env"
BT_CONF=/etc/bluetooth/main.conf

HOST=""
DNS=""
EMAIL=""
NEW_TOKEN=0
TUNNEL=0
NEW_TUNNEL_TOKEN=0
NEW_PASSWORD=0
PAIR_SPEAKER=0
SPOTIFY=0
NEW_SPOTIFY=0
NEW_SOLOIST_KEY=0
UPDATE_SOLOIST=0
while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST="${2:?--host needs a value}"; shift 2 ;;
        --dns) DNS="${2:?--dns needs a value}"; shift 2 ;;
        --email) EMAIL="${2:?--email needs a value}"; shift 2 ;;
        --new-token) NEW_TOKEN=1; shift ;;
        --tunnel) TUNNEL=1; shift ;;
        --new-tunnel-token) TUNNEL=1; NEW_TUNNEL_TOKEN=1; shift ;;
        --new-password) TUNNEL=1; NEW_PASSWORD=1; shift ;;
        --pair-speaker) PAIR_SPEAKER=1; shift ;;
        --spotify) SPOTIFY=1; shift ;;
        --new-spotify) SPOTIFY=1; NEW_SPOTIFY=1; shift ;;
        --new-soloist-key) NEW_SOLOIST_KEY=1; shift ;;
        --update-soloist) UPDATE_SOLOIST=1; shift ;;
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
if [ -s "$SPOTIFY_ENV" ]; then
    SPOTIFY=1
fi
if [ "$SPOTIFY" -eq 1 ] && [ ! -r /dev/tty ] && { [ "$NEW_SPOTIFY" -eq 1 ] || [ ! -s "$SPOTIFY_ENV" ]; }; then
    echo "No terminal to ask for the Spotify Client ID and secret; run this from an SSH window" >&2
    exit 1
fi
if [ "$NEW_SOLOIST_KEY" -eq 1 ] && [ ! -r /dev/tty ]; then
    echo "No terminal to ask for the Spotify Soloist API key; run this from an SSH window" >&2
    exit 1
fi
if [ "$PAIR_SPEAKER" -eq 1 ] && [ ! -r /dev/tty ]; then
    echo "No terminal to pick the speaker; run this from an SSH window" >&2
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

STAGE="$(mktemp -d /var/tmp/homeboard-install.XXXXXX)"
PREV_REV=""
ROLLBACK=1
keep() {
    local f
    for f in "$@"; do
        if grep -qxF "$f" "$STAGE/kept" 2>/dev/null; then
            continue
        fi
        echo "$f" >> "$STAGE/kept"
        if [ -e "$f" ] || [ -L "$f" ]; then
            mkdir -p "$STAGE/keep$(dirname "$f")"
            cp -a "$f" "$STAGE/keep$f"
        fi
    done
}
finish() {
    local status=$? f start=start
    set +e
    if [ "$ROLLBACK" -eq 1 ] && [ "$status" -ne 0 ]; then
        echo "== Install did not finish; putting the previous version back" >&2
        start=restart
        if [ -n "$PREV_REV" ]; then
            git -C "$APP_DIR" reset -q --keep "$PREV_REV" \
                || echo "Warning: could not put the code back to $PREV_REV" >&2
        fi
        if [ -d "$APP_DIR/.venv.prev" ]; then
            rm -rf "$APP_DIR/.venv"
            mv "$APP_DIR/.venv.prev" "$APP_DIR/.venv"
        fi
        if [ -f "$STAGE/kept" ]; then
            while read -r f; do
                rm -rf "$f"
                if [ -e "$STAGE/keep$f" ] || [ -L "$STAGE/keep$f" ]; then
                    cp -a "$STAGE/keep$f" "$f"
                fi
            done < "$STAGE/kept"
            systemctl daemon-reload
            if nginx -t 2>/dev/null; then
                systemctl reload-or-restart nginx.service
            fi
        fi
    fi
    rm -rf "$APP_DIR/.venv.prev" "$STAGE"
    systemctl "$start" homeboard-api.service || true
    exit "$status"
}

echo "== Installing packages"
systemctl stop homeboard-api.service 2>/dev/null || true
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git python3-venv cage chromium fonts-noto libheif1 curl iw nginx rsync swayimg patchelf \
    bluez pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth gcc libc6-dev
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
if dpkg -s raspotify >/dev/null 2>&1; then
    systemctl disable --now raspotify.service || true
    DEBIAN_FRONTEND=noninteractive apt-get purge -y raspotify
fi
rm -f /etc/apt/sources.list.d/raspotify.list /usr/share/keyrings/raspotify_key.asc
rm -rf /etc/systemd/system/raspotify.service.d /etc/raspotify
if ! command -v cc >/dev/null 2>&1     || ! printf '#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
'         | cc -x c -fsyntax-only - >/dev/null 2>&1; then
    echo "No working C compiler or libc headers for soloist-key.so; run: sudo apt-get install gcc libc6-dev" >&2
    exit 1
fi

echo "== Creating the homeboard user"
if ! id homeboard >/dev/null 2>&1; then
    useradd --system --home-dir "$DATA_DIR" --no-create-home --shell /usr/sbin/nologin homeboard
fi

if id "$KIOSK_USER" >/dev/null 2>&1; then
    usermod -aG video,render,input,bluetooth "$KIOSK_USER"
    if [ "$(id -u "$KIOSK_USER")" -ne 1000 ]; then
        echo "Warning: $KIOSK_USER is not uid 1000; edit XDG_RUNTIME_DIR in homeboard-kiosk.service and homeboard-helper.service" >&2
    fi
else
    echo "Warning: user $KIOSK_USER does not exist; the kiosk will not start" >&2
fi

echo "== Getting the code"
if [ -d "$APP_DIR/.git" ]; then
    PREV_REV="$(git -C "$APP_DIR" rev-parse HEAD)"
    git -C "$APP_DIR" remote set-url origin "$REPO_URL"
    git -C "$APP_DIR" pull --ff-only
elif [ -e "$APP_DIR" ]; then
    echo "$APP_DIR exists but is not a git clone; move it away and run again" >&2
    exit 1
else
    git clone "$REPO_URL" "$APP_DIR"
fi

echo "== Setting up Python"
REQ_SUM="$(sha256sum "$APP_DIR/requirements.txt" | cut -d' ' -f1)"
rm -rf "$APP_DIR/.venv.prev"
NEW_VENV=0
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    python3 -m venv "$APP_DIR/.venv"
    NEW_VENV=1
fi
if [ "$REQ_SUM" != "$(cat "$APP_DIR/.venv/.requirements.sum" 2>/dev/null || true)" ]; then
    if [ "$NEW_VENV" -eq 0 ]; then
        cp -a "$APP_DIR/.venv" "$APP_DIR/.venv.prev"
    fi
    rm -f "$APP_DIR/.venv/.requirements.sum"
    "$APP_DIR/.venv/bin/pip" install --upgrade pip
    "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
    echo "$REQ_SUM" > "$APP_DIR/.venv/.requirements.sum"
fi

echo "== Checking the new version"
mkdir -p "$STAGE/data"
if ! (cd "$APP_DIR" && HOMEBOARD_DATA="$STAGE/data" .venv/bin/python -c "import app.main"); then
    echo "The new version does not start; nothing else was changed" >&2
    exit 1
fi
keep /etc/udev/rules.d/90-homeboard-ignore-cec-input.rules \
    /etc/nginx/snippets/homeboard-proxy.conf /etc/nginx/conf.d/homeboard-map.conf \
    /etc/nginx/sites-enabled/default /etc/nginx/sites-available/homeboard \
    /etc/nginx/sites-enabled/homeboard /etc/nginx/sites-available/homeboard-tunnel \
    /etc/nginx/sites-enabled/homeboard-tunnel "$API_DROPIN_DIR/homeboard-host.conf" \
    "$API_DROPIN_DIR/homeboard-tunnel.conf" "$API_DROPIN_DIR/homeboard-spotify.conf"
for f in "$APP_DIR"/deploy/*.service "$APP_DIR"/deploy/*.timer; do
    keep "/etc/systemd/system/${f##*/}"
done

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
for unit in plymouth-quit plymouth-halt plymouth-poweroff plymouth-reboot; do
    rm -f "/etc/systemd/system/$unit.service.d/homeboard.conf"
    rmdir "/etc/systemd/system/$unit.service.d" 2>/dev/null || true
done
if [ -d "$THEME_DIR" ]; then
    DEBIAN_FRONTEND=noninteractive apt-get purge -y plymouth plymouth-themes
    rm -rf "$THEME_DIR"
fi
if [ -f "$CMDLINE_TXT" ]; then
    if [ ! -e "$CMDLINE_TXT.homeboard.bak" ]; then
        cp "$CMDLINE_TXT" "$CMDLINE_TXT.homeboard.bak"
    fi
    SPLASH_WORDS=(quiet loglevel=0 logo.nologo vt.global_cursor_default=0)
    read -r -a OLD_WORDS < "$CMDLINE_TXT" || true
    NEW_WORDS=()
    for word in "${OLD_WORDS[@]}"; do
        case " ${SPLASH_WORDS[*]} " in
            *" $word "*) continue ;;
        esac
        case "$word" in
            loglevel=* | splash | plymouth.*) continue ;;
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

echo "== Kiosk start screen"
install -d -m 755 /var/cache/homeboard
rm -f /var/cache/homeboard/boot.png
if ! "$APP_DIR/.venv/bin/python" "$APP_DIR/deploy/splash/still.py" /var/cache/homeboard/boot.gif; then
    echo "Warning: could not draw the kiosk start screen; the screen stays black until Chromium is up" >&2
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
if [ "$SPOTIFY" -eq 1 ]; then
    echo "== Spotify"
    install -d -m 700 -o root -g root "$ETC_DIR"
    if [ "$NEW_SPOTIFY" -eq 1 ] || [ ! -s "$SPOTIFY_ENV" ]; then
        read -rp "Paste the Spotify Client ID and press Enter: " SPOTIFY_ID < /dev/tty
        read -rsp "Paste the Spotify client secret and press Enter (nothing shows): " SPOTIFY_SECRET < /dev/tty
        echo
        SPOTIFY_ID="$(printf '%s' "$SPOTIFY_ID" | tr -d '[:space:]')"
        SPOTIFY_SECRET="$(printf '%s' "$SPOTIFY_SECRET" | tr -d '[:space:]')"
        if [ -z "$SPOTIFY_ID" ] || [ -z "$SPOTIFY_SECRET" ]; then
            unset SPOTIFY_ID SPOTIFY_SECRET
            echo "No Client ID or secret given; the stored Spotify app is unchanged" >&2
            exit 1
        fi
        (umask 077 && printf 'SPOTIFY_CLIENT_ID=%s
SPOTIFY_CLIENT_SECRET=%s
' "$SPOTIFY_ID" "$SPOTIFY_SECRET" > "$SPOTIFY_ENV")
        unset SPOTIFY_ID SPOTIFY_SECRET
    fi
    chown root:root "$SPOTIFY_ENV"
    chmod 600 "$SPOTIFY_ENV"
    mkdir -p "$API_DROPIN_DIR"
    printf '[Service]
LoadCredential=spotify:%s
' "$SPOTIFY_ENV" > "$API_DROPIN_DIR/homeboard-spotify.conf"
    if [ -z "$HOST" ]; then
        echo "Warning: Spotify sign-in needs the https address; set it up as in Part 6 of PI_SETUP.md" >&2
    fi
else
    rm -f "$API_DROPIN_DIR/homeboard-spotify.conf"
fi
nginx -t

echo "== Sound"
WP_CONF="/etc/wireplumber/bluetooth.lua.d/80-homeboard.lua /etc/wireplumber/wireplumber.conf.d/80-homeboard.conf"
WP_OLD_SUM="$(for f in $WP_CONF; do if [ -e "$f" ]; then cat "$f"; fi; done | sha256sum | cut -d' ' -f1)"
WP_RESTARTED=0
install -d -m 755 /etc/wireplumber/bluetooth.lua.d /etc/wireplumber/wireplumber.conf.d
printf 'bluez_monitor.properties["with-logind"] = false\n' \
    > /etc/wireplumber/bluetooth.lua.d/80-homeboard.lua
printf 'wireplumber.profiles = {\n  main = {\n    monitor.bluez.seat-monitoring = disabled\n  }\n}\n' \
    > /etc/wireplumber/wireplumber.conf.d/80-homeboard.conf
systemctl --global enable pipewire.socket pipewire-pulse.socket wireplumber.service
if id "$KIOSK_USER" >/dev/null 2>&1; then
    loginctl enable-linger "$KIOSK_USER"
    systemctl --machine="$KIOSK_USER@.host" --user enable \
        pipewire.socket pipewire-pulse.socket wireplumber.service || true
    if [ "$WP_OLD_SUM" != "$(cat $WP_CONF | sha256sum | cut -d' ' -f1)" ] \
        || ! systemctl --machine="$KIOSK_USER@.host" --user is-active --quiet wireplumber.service; then
        echo "Restarting WirePlumber (its config changed or it was not running)"
        systemctl --machine="$KIOSK_USER@.host" --user restart wireplumber.service || true
        WP_RESTARTED=1
    fi
fi

echo "== Spotify Connect speaker"
if [ "$UPDATE_SOLOIST" -eq 1 ] || [ ! -x /opt/soloist/soloist ] || [ ! -s /opt/soloist/lib/.complete ]; then
    bash "$APP_DIR"/deploy/soloist-update.sh
fi
cc -O2 -shared -fPIC -o /opt/soloist/soloist-key.so.new "$APP_DIR"/deploy/soloist-key.c
mv -f /opt/soloist/soloist-key.so.new /opt/soloist/soloist-key.so
if [ "$NEW_SOLOIST_KEY" -eq 1 ] || { [ ! -s "$SOLOIST_ENV" ] && [ -r /dev/tty ]; }; then
    install -d -m 700 -o root -g root "$ETC_DIR"
    read -rsp "Paste the Spotify Soloist API key and press Enter (nothing shows): " SOLOIST_KEY < /dev/tty
    echo
    SOLOIST_KEY="$(printf '%s' "$SOLOIST_KEY" | tr -d '[:space:]')"
    if [ -n "$SOLOIST_KEY" ]; then
        (umask 077 && printf 'SOLOIST_API_KEY=%s\n' "$SOLOIST_KEY" > "$SOLOIST_ENV")
    fi
    unset SOLOIST_KEY
fi
if [ -s "$SOLOIST_ENV" ]; then
    chown root:root "$SOLOIST_ENV"
    chmod 600 "$SOLOIST_ENV"
else
    echo "Warning: no Spotify Soloist API key, so Home Board does not show in Spotify; run: sudo bash $APP_DIR/deploy/install.sh --new-soloist-key" >&2
fi
SOLOIST_EXPIRES="$(cat /opt/soloist/expires 2>/dev/null || true)"
if [ -n "$SOLOIST_EXPIRES" ] && [ "$(date -d "$SOLOIST_EXPIRES - 14 days" +%s)" -le "$(date +%s)" ]; then
    echo "Warning: the Spotify Soloist build runs out on $SOLOIST_EXPIRES; run: sudo bash $APP_DIR/deploy/install.sh --update-soloist" >&2
fi

echo "== Enabling services"
systemctl daemon-reload
systemctl disable getty@tty1.service || true
systemctl enable \
    homeboard-api.service \
    homeboard-kiosk.service \
    homeboard-preload.service \
    homeboard-helper.service \
    homeboard-soloist.service \
    homeboard-soloist-events.service \
    homeboard-soloist-update.timer \
    bluetooth.service \
    wifi-powersave-off.service \
    homeboard-kiosk-restart.timer \
    homeboard-backup.timer \
    nginx.service
if [ -n "$HOST" ]; then
    systemctl enable --now certbot.timer
fi
systemctl restart homeboard-api.service nginx.service
if ! curl -fsS -m 5 --retry 20 --retry-delay 1 --retry-connrefused -o /dev/null http://127.0.0.1:8000/healthz; then
    echo "The board did not answer http://127.0.0.1:8000/healthz after the restart" >&2
    exit 1
fi
ROLLBACK=0
if [ "$TUNNEL" -eq 1 ]; then
    systemctl enable homeboard-tunnel.service
    systemctl restart homeboard-tunnel.service
else
    systemctl disable --now homeboard-tunnel.service 2>/dev/null || true
fi

echo "== Bluetooth"
BT_RESTART=0
if [ ! -f "$BT_CONF" ] || ! grep -qx 'AutoEnable=true' "$BT_CONF"; then
    if [ -f "$BT_CONF" ] && grep -Eq '^#?AutoEnable=' "$BT_CONF"; then
        sed -i -E 's/^#?AutoEnable=.*/AutoEnable=true/' "$BT_CONF"
    else
        printf '\n[Policy]\nAutoEnable=true\n' >> "$BT_CONF"
    fi
    BT_RESTART=1
fi
if command -v rfkill >/dev/null 2>&1; then
    rfkill unblock bluetooth || true
fi
if [ "$BT_RESTART" -eq 1 ]; then
    systemctl restart bluetooth.service
else
    systemctl start bluetooth.service
fi
if [ "$PAIR_SPEAKER" -eq 1 ]; then
    echo "== Pairing the speaker"
    echo "Put the soundbar in Bluetooth pairing mode with its remote now."
    echo "Looking for Bluetooth devices for 15 seconds..."
    bluetoothctl power on >/dev/null || true
    bluetoothctl --timeout 300 scan on >/dev/null 2>&1 &
    SCAN_PID=$!
    sleep 15
    mapfile -t FOUND < <(bluetoothctl devices \
        | sed -n -E 's/^Device ([0-9A-F:]{17}) (.+)$/\1 \2/p' \
        | grep -Ev '^[0-9A-F:]{17} [0-9A-F]{2}(-[0-9A-F]{2}){5}$' || true)
    if [ "${#FOUND[@]}" -eq 0 ]; then
        kill "$SCAN_PID" 2>/dev/null || true
        echo "No Bluetooth devices found. Put the soundbar in Bluetooth pairing mode with its remote and run: sudo bash $APP_DIR/deploy/install.sh --pair-speaker" >&2
        exit 1
    fi
    for i in "${!FOUND[@]}"; do
        printf '%3d) %s\n' "$((i + 1))" "${FOUND[$i]#* }"
    done
    echo "If the soundbar is not in the list, press Enter, put it in Bluetooth pairing mode with its remote and run: sudo bash $APP_DIR/deploy/install.sh --pair-speaker"
    read -rp "Type the number of the soundbar and press Enter: " CHOICE < /dev/tty
    kill "$SCAN_PID" 2>/dev/null || true
    if ! [[ "$CHOICE" =~ ^[0-9]+$ ]] || [ "$CHOICE" -lt 1 ] || [ "$CHOICE" -gt "${#FOUND[@]}" ]; then
        echo "No speaker picked; the stored speaker is unchanged" >&2
        exit 1
    fi
    SPEAKER_MAC="${FOUND[$((CHOICE - 1))]%% *}"
    SPEAKER_INFO="$(bluetoothctl info "$SPEAKER_MAC" || true)"
    if [[ "$SPEAKER_INFO" != *"Bonded: yes"* ]]; then
        bluetoothctl pairable on >/dev/null
        if ! timeout 60 bluetoothctl pair "$SPEAKER_MAC"; then
            echo "Pairing failed. Put the soundbar in Bluetooth pairing mode with its remote and run: sudo bash $APP_DIR/deploy/install.sh --pair-speaker" >&2
            exit 1
        fi
    fi
    bluetoothctl trust "$SPEAKER_MAC"
    install -d -m 700 -o root -g root "$ETC_DIR"
    (umask 077 && printf 'SPEAKER_MAC=%s\n' "$SPEAKER_MAC" > "$SPEAKER_ENV")
    if ! timeout 60 bluetoothctl connect "$SPEAKER_MAC"; then
        echo "Warning: paired the speaker but could not connect yet; try: curl -s -X POST 127.0.0.1:8002/connect" >&2
    fi
fi
systemctl restart homeboard-helper.service
if [ "$PAIR_SPEAKER" -eq 1 ]; then
    for _ in 1 2 3 4 5; do
        curl -fsS -m 30 -X POST -H 'Content-Type: application/json' -d '{}'             127.0.0.1:8002/volume >/dev/null 2>&1 && break
        sleep 2
    done || true
    echo "Speaker volume: $(curl -sS -m 5 127.0.0.1:8002/volume 2>/dev/null || echo unknown)"
fi
if [ -s "$SOLOIST_ENV" ]; then
    SOLOIST_SUM="$(cat /etc/systemd/system/homeboard-soloist.service "$SOLOIST_ENV"         "$APP_DIR"/deploy/soloist.sh /opt/soloist/soloist /opt/soloist/soloist-key.so 2>/dev/null | sha256sum | cut -d' ' -f1)"
    EVENTS_SUM="$(cat /etc/systemd/system/homeboard-soloist-events.service         "$APP_DIR"/deploy/soloist-event.sh 2>/dev/null | sha256sum | cut -d' ' -f1)"
    if [ "$SOLOIST_SUM" != "$(cat /opt/soloist/service.sum 2>/dev/null)" ]         || [ "$WP_RESTARTED" -eq 1 ]         || ! systemctl is-active --quiet homeboard-soloist.service; then
        echo "Restarting Spotify Soloist (its files changed, WirePlumber restarted or it was not running)"
        systemctl restart homeboard-soloist.service
        echo "$EVENTS_SUM" > /opt/soloist/events.sum
    elif [ "$EVENTS_SUM" != "$(cat /opt/soloist/events.sum 2>/dev/null)" ]         || ! systemctl is-active --quiet homeboard-soloist-events.service; then
        systemctl restart homeboard-soloist-events.service
        echo "$EVENTS_SUM" > /opt/soloist/events.sum
    fi
    echo "$SOLOIST_SUM" > /opt/soloist/service.sum
fi

echo
echo "Install finished. reboot now: sudo reboot"
