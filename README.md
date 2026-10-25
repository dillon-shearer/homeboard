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
python -m pytest -q -n auto
```

### When to write a test

- A test pins a behaviour a user would notice break: a request that returns the wrong thing, a tap that does nothing, data that is lost. It does not assert markup, CSS values, element ids, a count of rows, a schema version number, or the full list of settings keys.
- Use the cheapest layer that can see the bug: pure Python first, then FastAPI TestClient, then the node fake-DOM harness, then headless Chrome. Chrome is only for touch, focus, layout overflow and page scripts the node harness cannot run.
- At most one Chrome session per page per file, and one file per page (TV page, lists page, photos page, screens window, sound page). A new Chrome check joins the existing file for that page as a new assertion inside the existing session. It does not launch another Chrome.
- No fixed sleeps. Wait by polling a condition with a bound (the pattern in tests/chrome.py). A sleep over 0.1 s is a bug.
- One new behaviour, one test. Do not add a second test for the same path from another angle, and do not keep an old test when a new one covers it.
- Before adding a test file, grep tests/ for one that already covers the page or module and add there.

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

The weather widget needs the internet. `GET /api/weather` looks up the place in `weather_location` (a zip or a town name, set in settings) and fetches a 7 day forecast from Open-Meteo (no account or key), with api.zippopotam.us as a fallback for zip lookup. It refreshes at most every 30 minutes and keeps the last good forecast in `weather.json` in the data dir, next to `homeboard.db` (`/var/lib/homeboard/weather.json` on the Pi). When the internet is down it serves that file, with `stale` true once it is older than 90 minutes.
