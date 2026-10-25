#!/usr/bin/env bash
set -euo pipefail
if [ -n "${CREDENTIALS_DIRECTORY:-}" ] && [ -r "$CREDENTIALS_DIRECTORY/soloist" ]; then
    export SOLOIST_API_KEY_FILE="$CREDENTIALS_DIRECTORY/soloist"
    export LD_PRELOAD=/opt/soloist/soloist-key.so
fi
exec /opt/soloist/soloist "$@"
