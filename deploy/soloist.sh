#!/usr/bin/env bash
set -euo pipefail
ARGS=("$@")
if [ -n "${CREDENTIALS_DIRECTORY:-}" ] && [ -r "$CREDENTIALS_DIRECTORY/soloist" ]; then
    KEY="$(sed -n 's/^SOLOIST_API_KEY=//p' "$CREDENTIALS_DIRECTORY/soloist")"
    ARGS+=(--api-key "$KEY")
fi
exec /opt/soloist/soloist "${ARGS[@]}"
