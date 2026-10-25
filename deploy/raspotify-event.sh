#!/usr/bin/env bash
URL="${HOMEBOARD_HELPER_URL:-http://127.0.0.1:8002}/playing"
case "${PLAYER_EVENT:-}" in
    playing | session_connected) BODY='{"playing": true}' ;;
    paused | stopped | session_disconnected) BODY='{"playing": false}' ;;
    *) exit 0 ;;
esac
curl -s -o /dev/null --max-time 3 -X POST -H 'Content-Type: application/json' -d "$BODY" "$URL" || true
