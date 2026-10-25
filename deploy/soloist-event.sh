#!/usr/bin/env bash
URL="${HOMEBOARD_HELPER_URL:-http://127.0.0.1:8002}/playing"
API="${HOMEBOARD_API_URL:-http://127.0.0.1:8000}/api/spotify/event"
LAST=""
while IFS= read -r LINE; do
    case "$LINE" in
        *'"type":"track_changed"'*) KIND=track_changed ;;
        *'"type":"volume_changed"'*) KIND=volume_changed ;;
        *'"type":"playback_changed"'*) KIND=playback_changed ;;
        *'"type":"playback_state"'*) KIND=playback_state ;;
        *) continue ;;
    esac
    case "$LINE" in
        *'"status":"playing"'*) STATUS=playing ;;
        *'"status":"paused"'*) STATUS=paused ;;
        *'"status":"idle"'*) STATUS=idle ;;
        *'"status":"buffering"'*) STATUS=buffering ;;
        *) STATUS="" ;;
    esac
    if [ "$KIND" != playback_state ]; then
        if [ "$KIND" = playback_changed ]; then
            EVENT="{\"type\": \"$KIND\", \"status\": \"$STATUS\"}"
        else
            EVENT="{\"type\": \"$KIND\"}"
        fi
        curl -s -o /dev/null --max-time 2 -X POST -H 'Content-Type: application/json' -d "$EVENT" "$API" || true
    fi
    case "$KIND:$STATUS" in
        playback_changed:playing | playback_state:playing) BODY='{"playing": true}' ;;
        playback_changed:paused | playback_changed:idle | playback_state:paused | playback_state:idle) BODY='{"playing": false}' ;;
        *) continue ;;
    esac
    if [ "$BODY" = "$LAST" ]; then
        continue
    fi
    if curl -sf -o /dev/null --max-time 3 -X POST -H 'Content-Type: application/json' -d "$BODY" "$URL"; then
        LAST="$BODY"
    fi
done
