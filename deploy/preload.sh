#!/usr/bin/env bash
set -u

PROGRAMS=(/usr/bin/swayimg /usr/bin/cage /usr/lib/aarch64-linux-gnu/libgallium-*.so /usr/lib/chromium/chromium)

{
    printf '%s\n' /var/cache/homeboard/boot.png
    for program in "${PROGRAMS[@]}"; do
        printf '%s\n' "$program"
        ldd "$program" 2>/dev/null | awk '$3 ~ /^\// {print $3}'
    done
    printf '%s\n' /usr/lib/chromium/*.pak /usr/lib/chromium/*.bin /usr/lib/chromium/icudtl.dat /usr/lib/chromium/locales/en-US.pak
} | awk '!seen[$0]++' | while read -r file; do
    cat "$file" >/dev/null 2>&1
done
