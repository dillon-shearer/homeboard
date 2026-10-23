#!/usr/bin/env bash
set -u

for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null http://localhost/healthz; then
        break
    fi
    sleep 1
done

exec cage -- chromium \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --incognito \
    --autoplay-policy=no-user-gesture-required \
    --ozone-platform=wayland \
    http://localhost/
