# Home Board

A DIY wall board. A Raspberry Pi drives a monitor that shows a full screen photo slideshow, and phones on the home LAN manage the photos through a small admin site.

## Build and test

Needs Python 3.11 or newer. There is no build step: the server is Python and the pages are plain HTML and JS.

Install the dependencies:

```
python -m pip install -r requirements.txt
```

Run the tests:

```
python -m pytest -q
```

Tests live under `tests/` as `test_*.py` files. The board runs the same two steps for each ticket: `worktree_setup` and `test` in `.kanban95/config.json`. Both skip cleanly while `requirements.txt` or the tests do not exist yet.

## Run on the desktop

Create a venv and activate it (`.venv\Scriptsctivate` on Windows, `source .venv/bin/activate` elsewhere), then install and start:

```
python -m venv .venv
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/healthz. Data goes to `./data` unless `HOMEBOARD_DATA` is set.

Board smoke test passed.
