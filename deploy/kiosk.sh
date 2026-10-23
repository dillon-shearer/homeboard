#!/usr/bin/env bash
set -u

exec cage -- chromium \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --incognito \
    --autoplay-policy=no-user-gesture-required \
    --ozone-platform=wayland \
    file:///opt/homeboard/deploy/splash/boot.html
