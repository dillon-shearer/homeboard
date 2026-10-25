#!/usr/bin/env bash
URL="${HOMEBOARD_HELPER_URL:-http://127.0.0.1:8002}/playing"
LAST=""
while IFS= read -r LINE; do
    case "$LINE" in
        *'"type":"playback_changed"'* | *'"type":"playback_state"'*) ;;
        *) continue ;;
    esac
    case "$LINE" in
        *'"status":"playing"'*) BODY='{"playing": true}' ;;
        *'"status":"paused"'* | *'"status":"idle"'*) BODY='{"playing": false}' ;;
        *) continue ;;
    esac
    if [ "$BODY" = "$LAST" ]; then
        continue
    fi
    LAST="$BODY"
    curl -s -o /dev/null --max-time 3 -X POST -H 'Content-Type: application/json' -d "$BODY" "$URL" || true
done
