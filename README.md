# Home Board

A DIY wall board. A Raspberry Pi drives a monitor that shows a full screen photo slideshow, and phones on the home LAN manage the photos through a small admin site.

## Build and test

Needs Python 3.11 or newer. There is no build step: the server is Python and the pages are plain HTML and JS.

Install the dependencies and the test tools:

```
python -m pip install -r requirements-dev.txt
```

Run the tests:

```
python -m pytest -q
```

Tests live under `tests/` as `test_*.py` files. The board runs the same two steps for each ticket: `worktree_setup` and `test` in `.kanban95/config.json`. Both skip cleanly while `requirements-dev.txt` or the tests do not exist yet.

## Run on the desktop

Create a venv and activate it (`.venv\Scripts\activate` on Windows, `source .venv/bin/activate` elsewhere), then install and start:

```
python -m venv .venv
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/healthz. Data goes to `./data` unless `HOMEBOARD_DATA` is set.

## Weather

The weather widget needs the internet. `GET /api/weather` looks up the place in `weather_location` (a zip or a town name, set in settings) and fetches a 3 day forecast from Open-Meteo (no account or key), with api.zippopotam.us as a fallback for zip lookup. It refreshes at most every 30 minutes and keeps the last good forecast in `weather.json` in the data dir, next to `homeboard.db` (`/var/lib/homeboard/weather.json` on the Pi). When the internet is down it serves that file, with `stale` true once it is older than 90 minutes.
