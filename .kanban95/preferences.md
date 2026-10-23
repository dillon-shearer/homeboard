# Home Board

## What this project is for

Home Board (repo `homeboard`) is a DIY wall board. A Raspberry Pi 4B drives an existing monitor and boots straight into a full screen photo slideshow. Two people add, remove, reorder and enable or disable photos from their phones through a small admin site on the home LAN, opened by tapping an NFC sticker on the frame.

This is phase 1 (MVP) of a larger Skylight style board. Later phases add weather and a daily brief, a shared calendar, to do and notes, a touch panel and remote access. They reuse the same server, kiosk and admin site, so MVP choices must not block them.

## Stack and rules

- Python 3.11+, FastAPI, uvicorn, Jinja2, SQLite, Pillow with pillow-heif. Plain HTML and vanilla JS, no build step.
- Local first: no cloud accounts or external services, works with the internet down.
- The display is dumb and read mostly. All input happens on phones. Admin page is mobile first (Chrome, Samsung Internet, iOS Safari). No login.
- Headless and recoverable: systemd services, Chromium kiosk under cage, power loss must come back to a working slideshow.
- Display page must not assume 1080p; a 10 inch touch panel comes later.
- Photos and personal data never go in the repo.
- Dev happens on the desktop (`http://localhost:8000`). The Pi is the deploy target, and agents deploy to it themselves (see below). NFC taps and checks that need a hand on a phone are the operator's; agents write the check and ask.

## How the operator works

Mode: GitHub repo with push. The repo is `homeboard` on GitHub (MIT); the board pushes merged work. The remote is `origin` on GitHub (dillon-shearer/homeboard); master tracks origin/main and merges push. Pushing to GitHub does not change the Pi; nothing on the Pi pulls by itself.

## Deploy every change to the Pi

A ticket that changes anything the Pi runs (`app/`, `deploy/`, `requirements.txt`) is not done until its commit runs on the Pi. GitHub alone is not enough.

- Worker: after the tests pass and before `move_ticket(testing)`, deploy your committed ticket branch to the Pi and check it live there. Say in your summary note that it is deployed and what you checked.
- Tester: check the change on the real Pi as well as locally. Fail the ticket if the Pi does not have the ticket's commit (`sudo -n git -C /opt/homeboard log --oneline -5`) or the live check fails.
- Tickets that only touch tests, docs or `.kanban95/` skip the deploy.

SSH from this PC is key based with passwordless sudo. Run from the ticket worktree in bash:

```
B="$(cygpath -m "$TEMP")/ticket.bundle"
git bundle create "$B" origin/main..HEAD
scp -o BatchMode=yes "$B" dillon@homeboard.local:/tmp/ticket.bundle
ssh -o BatchMode=yes dillon@homeboard.local 'set -e; cd /opt/homeboard; test -z "$(sudo -n git status --porcelain)"; sudo -n git fetch -q origin; sudo -n git fetch -q /tmp/ticket.bundle HEAD; G="sudo -n git -c user.name=homeboard -c user.email=homeboard@pi"; $G merge --no-edit origin/main; $G merge --no-edit FETCH_HEAD; sudo -n bash deploy/install.sh </dev/null; sudo -n systemctl restart homeboard-kiosk; rm -f /tmp/ticket.bundle'
rm -f "$B"
```

Merge, not reset: other tickets may already be deployed ahead of origin/main, and a merge keeps them. If the Pi has uncommitted changes (the script stops) or a merge conflicts, run `sudo -n git -C /opt/homeboard merge --abort`, leave the Pi as it was and ask the operator.
