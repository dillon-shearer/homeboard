#!/usr/bin/env bash
set -euo pipefail

DATA_DIR=/var/lib/homeboard
DB_DIR=/var/backups/homeboard-db
PHOTOS_DIR=/var/backups/homeboard-photos
PYTHON=/opt/homeboard/.venv/bin/python

mkdir -p "$DB_DIR" "$PHOTOS_DIR"

TMP="$(mktemp "$DB_DIR/.homeboard-db.XXXXXX")"
trap 'rm -f "$TMP"' EXIT
"$PYTHON" -c 'import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()
src.close()' "$DATA_DIR/homeboard.db" "$TMP"
chmod 644 "$TMP"
mv -f "$TMP" "$DB_DIR/homeboard-$(date +%Y%m%d).db"
ls -1t "$DB_DIR"/homeboard-*.db | tail -n +8 | xargs -r rm -f --

rsync -a --delete "$DATA_DIR/photos" "$DATA_DIR/thumbs" "$PHOTOS_DIR/"
