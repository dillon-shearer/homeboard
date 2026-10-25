#!/usr/bin/env bash
set -euo pipefail
DIR="${SOLOIST_DIR:-/opt/soloist}"
SOLOIST_URL=https://soloist-builds.spotifycdn.com/soloist_release_arm64.tar.gz
GLIBC_URL=https://snapshot.debian.org/archive/debian/20261020T000000Z/pool/main/g/glibc/libc6_2.41-12+deb13u4_arm64.deb
GLIBC_SHA256=8784eda966b189c777a384dac5ce009e8fc9b52d006926c5a013e7fa8aa688cc
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

start_if_stopped() {
    if [[ "$(cat "$DIR/expires")" > "$(date +%F)" ]] \
        && systemctl is-enabled --quiet homeboard-soloist.service 2>/dev/null \
        && ! systemctl is-active --quiet homeboard-soloist.service; then
        echo "Starting Spotify Soloist again with the current build"
        systemctl restart homeboard-soloist.service
    fi
}

install -d -m 755 "$DIR"
if [ "$(cat "$DIR/lib/.complete" 2>/dev/null)" != "$GLIBC_SHA256" ]; then
    curl -fsSL -o "$TMP/libc6.deb" "$GLIBC_URL"
    echo "$GLIBC_SHA256  $TMP/libc6.deb" | sha256sum -c --quiet -
    dpkg-deb -x "$TMP/libc6.deb" "$TMP/libc6"
    rm -rf "$DIR/lib.new" "$DIR/lib.old"
    install -d -m 755 "$DIR/lib.new"
    cp -a "$TMP/libc6/usr/lib/aarch64-linux-gnu/." "$DIR/lib.new/"
    if [ ! -e "$DIR/lib.new/ld-linux-aarch64.so.1" ]; then
        echo "The glibc package has no ld-linux-aarch64.so.1" >&2
        exit 1
    fi
    sync
    echo "$GLIBC_SHA256" > "$DIR/lib.new/.complete"
    if [ -e "$DIR/lib" ]; then
        mv "$DIR/lib" "$DIR/lib.old"
    fi
    mv "$DIR/lib.new" "$DIR/lib"
    sync
    rm -rf "$DIR/lib.old"
fi

curl -fsSL -o "$TMP/soloist.tar.gz" "$SOLOIST_URL"
tar -xzf "$TMP/soloist.tar.gz" -C "$TMP" soloist
patchelf --set-interpreter "$DIR/lib/ld-linux-aarch64.so.1" --set-rpath "$DIR/lib" "$TMP/soloist"
VERSION="$("$TMP/soloist" --version)"
BUILT="$(printf '%s\n' "$VERSION" | sed -n 's/.* (\([0-9]\{8\}\)) .*/\1/p')"
if [ -z "$BUILT" ]; then
    echo "Could not read the Spotify Soloist build date from: $VERSION" >&2
    exit 1
fi
if [ -x "$DIR/soloist" ] && cmp -s "$TMP/soloist" "$DIR/soloist"; then
    date -d "$BUILT + 90 days" +%F > "$DIR/expires"
    echo "Spotify Soloist is up to date: $VERSION, runs out on $(cat "$DIR/expires")"
    start_if_stopped
    exit 0
fi
install -m 755 "$TMP/soloist" "$DIR/soloist.new"
mv -f "$DIR/soloist.new" "$DIR/soloist"
date -d "$BUILT + 90 days" +%F > "$DIR/expires"
echo "Installed $VERSION, runs out on $(cat "$DIR/expires")"
systemctl try-restart homeboard-soloist.service
start_if_stopped
