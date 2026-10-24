#!/usr/bin/env bash
set -u

STILL=/var/cache/homeboard/boot.png

if [ "${1:-}" != session ]; then
    exec cage -- /bin/bash "$0" session
fi

if [ -r "$STILL" ]; then
    swayimg --fullscreen --scale=fit --no-sway "$STILL" &
    sleep 0.5
fi

exec chromium \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --incognito \
    --autoplay-policy=no-user-gesture-required \
    --ozone-platform=wayland \
    file:///opt/homeboard/deploy/splash/boot.html
