#!/usr/bin/env bash
set -u

COVER=/var/cache/homeboard/boot.gif

if [ "${1:-}" != session ]; then
    exec cage -- /bin/bash "$0" session
fi

if [ -r "$COVER" ]; then
    swayimg --fullscreen --scale=fit --no-sway "$COVER" &
    SWAYIMG=$!
    sleep 0.5
    /opt/homeboard/.venv/bin/python /opt/homeboard/deploy/splash/overlay.py "$COVER" "$SWAYIMG" &
fi

BOOT=file:///opt/homeboard/deploy/splash/boot.html
for _ in $(seq 1 150); do
    if (exec 3<>/dev/tcp/127.0.0.1/80) 2>/dev/null; then
        BOOT=http://localhost/
        break
    fi
    sleep 0.2
done

exec chromium \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --incognito \
    --autoplay-policy=no-user-gesture-required \
    --ozone-platform=wayland \
    "$BOOT"
