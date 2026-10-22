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
- Dev happens on the desktop (`http://localhost:8000`). The Pi is a deploy target only. Real Pi, NFC and phone checks are the operator's; agents write the check and ask.

## How the operator works

Mode: GitHub repo with push. The repo is `homeboard` on GitHub (MIT); the board pushes merged work and the Pi pulls from GitHub. The remote is not set up yet, so until `origin` exists merges land locally only. Do not create or change the remote; ask the operator.
