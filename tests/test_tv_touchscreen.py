import base64
import json
import os
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from app import weather
from tests.chrome import _Chrome, needs_chrome, server  # noqa: F401
from tests.test_tv_weather import _seed_cache

WIDTH, HEIGHT = 1024, 600

MEASURE = """JSON.stringify((() => {
  const shown = sel => [...document.querySelectorAll(sel)].filter(n => n.offsetParent);
  const px = list => list.map(n => parseFloat(getComputedStyle(n).fontSize));
  const name = n => (n.getAttribute('data-kind') || n.className) + ' ' + n.textContent.slice(0, 30);
  const cards = [...document.querySelectorAll('#screen .tv-card')];
  return {
    title: document.getElementById('title').textContent,
    ready: !!document.querySelector('#screen .tv-card[data-kind]') &&
      !document.querySelector('#screen .tv-card[data-kind="weather"]:not(:has(.wx-temp))'),
    li: px(shown('.tv-card li').filter(n => !n.closest('.tv-lists.condensed'))),
    condensed: px(shown('.tv-lists.condensed li')),
    h2: px(shown('.tv-card h2')),
    day: px(shown('.tv-cal-day li, .tv-up li')),
    msg: px(shown('.tv-msg')),
    num: px(shown('.tv-cal-grid .d b')),
    chips: px(shown('.tv-cal-grid .ev:not(.more)')),
    more: shown('.more').map(n => n.textContent),
    tabs: shown('.tv-tab').map(t => { const r = t.getBoundingClientRect(); return {h: r.height, w: r.width}; }),
    clipped: cards.filter(c => c.scrollHeight > c.clientHeight + 1 || c.scrollWidth > c.clientWidth + 1).map(name),
    lists: shown('.tv-card ul').filter(u => u.scrollHeight > u.clientHeight + 1).map(name),
  };
})())"""


def _seed(base):
    today = date.today()
    with httpx.Client(base_url=base) as c:
        lists = {lst["name"]: lst for lst in c.get("/api/lists").json()}
        publix, bjs = lists["Publix"], lists["BJs"]
        for i in range(14):
            c.post(f"/api/lists/{publix['id']}/items", json={"text": f"Grocery item {i}"})
        pin = c.post(f"/api/lists/{publix['id']}/items", json={"text": "Dog food"}).json()
        c.patch(f"/api/items/{pin['id']}", json={"pinned": True})
        for i in range(10):
            item = c.post(f"/api/lists/{bjs['id']}/items", json={"text": f"Bulk item {i}"}).json()
            if i % 3 == 0:
                c.patch(f"/api/items/{item['id']}", json={"done": True})
        assert c.patch(f"/api/lists/{bjs['id']}", json={"tv_open": False}).status_code == 200
        notes = c.post("/api/lists", json={"name": "Message board", "kind": "notes"}).json()
        for i in range(8):
            c.post(f"/api/lists/{notes['id']}/items", json={"text": f"Note {i} about the week"})
        for i in range(8):
            r = c.post(
                "/api/events",
                json={
                    "title": f"Today event {i}",
                    "date": today.isoformat(),
                    "time": f"{8 + i:02d}:00",
                    "repeat": "never",
                },
            )
            assert r.status_code == 201, r.text
        for d in range(1, 12):
            for i in range(3):
                c.post(
                    "/api/events",
                    json={
                        "title": f"Event {d}-{i}",
                        "date": (today + timedelta(days=d)).isoformat(),
                        "time": f"{9 + i:02d}:30",
                        "repeat": "never",
                    },
                )
        seeded = {s["name"]: s for s in c.get("/api/screens").json()}
        extra = []
        for name, widgets in (
            (
                "Wide lists",
                [
                    {"kind": "lists", "col": 0, "row": 0, "w": 2, "h": 1},
                    {"kind": "clock", "col": 0, "row": 1, "w": 1, "h": 1},
                    {"kind": "weather", "col": 1, "row": 1, "w": 1, "h": 1},
                ],
            ),
            ("Wide clock", [{"kind": "clock", "col": 0, "row": 0, "w": 2, "h": 1}]),
            ("Big weather", [{"kind": "weather", "col": 0, "row": 0, "w": 2, "h": 2}]),
        ):
            screen = c.post("/api/screens", json={"name": name}).json()
            for body in widgets:
                r = c.post(f"/api/screens/{screen['id']}/widgets", json=body)
                assert r.status_code == 201, r.text
            extra.append(screen)
    return [seeded["Daily Brief"], seeded["Calendar"], seeded["Notes"]], extra


def _show(base, chrome, screen):
    httpx.put(f"{base}/api/settings", json={"channel": "screen", "screen_id": screen["id"]})
    chrome.send("Page.navigate", {"url": f"{base}/tv"})
    out = None
    for _ in range(100):
        out = json.loads(chrome.eval(MEASURE) or "null")
        if out and out["title"] == screen["name"] and out["ready"]:
            break
        time.sleep(0.05)
    time.sleep(0.3)
    return json.loads(chrome.eval(MEASURE))


@needs_chrome
def test_tv_is_readable_and_tappable_at_1024x600(server, tmp_path):  # noqa: F811
    weather._last_fail.clear()
    _seed_cache(server, tmp_path / "data", datetime.now(UTC))
    seeded, extra = _seed(server)
    shots = Path(os.environ.get("TV_SHOTS_DIR") or tmp_path)
    chrome = _Chrome(tmp_path / "chrome", WIDTH, HEIGHT)
    try:
        seen = {}
        for screen in seeded + extra:
            out = _show(server, chrome, screen)
            assert out["title"] == screen["name"]
            png = chrome.send("Page.captureScreenshot", {"format": "png"})["data"]
            (shots / f"tv_{screen['name'].replace(' ', '_')}.png").write_bytes(
                base64.b64decode(png)
            )
            seen[screen["name"]] = out
            assert out["clipped"] == [], (screen["name"], out["clipped"])
            assert out["lists"] == [], (screen["name"], out["lists"])
            assert all(size >= 18 for size in out["li"]), (screen["name"], out["li"])
            assert all(size >= 18 for size in out["day"] + out["msg"]), screen["name"]
            assert all(size >= 16 for size in out["condensed"]), (screen["name"], out["condensed"])
            assert all(size >= 22 for size in out["h2"]), (screen["name"], out["h2"])
            assert all(size >= 16 for size in out["num"]), screen["name"]
            assert all(size >= 14 for size in out["chips"]), screen["name"]
            for tab in out["tabs"]:
                assert tab["h"] >= 48 and tab["w"] >= 48, (screen["name"], tab)
    finally:
        chrome.close()
    brief, cal, notes = (seen[s["name"]] for s in seeded)
    assert brief["li"] and brief["h2"] and brief["day"]
    assert cal["num"] and cal["chips"] and cal["day"]
    assert notes["tabs"] and notes["li"]
    assert seen["Wide lists"]["condensed"]
    assert any("more" in m for m in seen["Wide lists"]["more"])
    assert any(m.startswith("+") for m in cal["more"])
    assert any("more" in m for m in brief["more"] + notes["more"])
