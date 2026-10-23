import json
import subprocess
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from app import db, weather
from tests.chrome import _Chrome, needs_chrome, needs_node, server  # noqa: F401

SCREEN_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "tv_screen.js"


def _node(body):
    script = f"const T = require({json.dumps(str(SCREEN_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


def _days(today):
    return [
        {
            "date": (today + timedelta(days=i)).isoformat(),
            "high": high,
            "low": low,
            "rain": rain,
            "label": label,
            "icon": icon,
            "sunrise": f"{(today + timedelta(days=i)).isoformat()}T07:5{i}",
            "sunset": f"{(today + timedelta(days=i)).isoformat()}T18:5{i}",
        }
        for i, (high, low, rain, label, icon) in enumerate(
            [
                (78, 55, 20, "Partly cloudy", "cloud-sun"),
                (70, 52, 80, "Rain", "rain"),
                (66, 50, None, "Thunderstorms", "thunder"),
            ]
        )
    ]


@needs_node
def test_weather_helpers():
    result = _node(
        "const w = {ok: true, days: ["
        "{date: '2026-10-22', high: 80, low: 60, rain: 0, icon: 'sun'},"
        "{date: '2026-10-23', high: 78, low: 55, rain: 20, icon: 'cloud-sun'},"
        "{date: '2026-10-24', high: 70, low: 52, rain: 80, icon: 'rain'},"
        "{date: '2026-10-25', high: null, low: 49, rain: null, icon: 'thunder'}]};"
        "const d = w.days[1];"
        "console.log(JSON.stringify({today: T.weatherToday(w, '2026-10-23'),"
        " missing: T.weatherToday(w, '2026-11-01'), none: T.weatherToday({days: []}, '2026-10-23'),"
        " hiLo: T.hiLo(d), rain: [T.rainText(d), T.rainText(w.days[3])],"
        " next: T.nextDays(w, '2026-10-23'), sun: T.clockAt('2026-10-23T18:55'),"
        " asOf: T.asOf(new Date(2026, 9, 23, 15, 10).toISOString()), bad: T.asOf('nope')}))"
    )
    assert result["today"] == 1 and result["missing"] == 0 and result["none"] == -1
    assert result["hiLo"] == "H 78 L 55"
    assert result["rain"] == ["Rain 20%", "Rain --%"]
    assert result["next"] == [
        {"name": "Sat", "icon": "rain", "hiLo": "H 70 L 52"},
        {"name": "Sun", "icon": "thunder", "hiLo": "H -- L 49"},
    ]
    assert result["sun"] == "6:55 pm"
    assert result["asOf"] == "as of 3:10 pm" and result["bad"] == ""


@needs_node
def test_every_weather_icon_has_a_drawing():
    icons = {icon for _, icon in [*weather.CODES.values(), weather.UNKNOWN]}
    names = sorted(icons | {weather.NIGHT[i] for i in icons if i in weather.NIGHT})
    result = _node(
        f"const names = {json.dumps(names)};"
        "const fallback = T.iconSvg('no-such-icon');"
        "console.log(JSON.stringify(names.map(n => [n, n === 'cloud' || T.iconSvg(n) !== fallback,"
        " /^<svg [^>]*viewBox/.test(T.iconSvg(n)), /<img|url\\(/.test(T.iconSvg(n))])))"
    )
    for name, own, svg, image in result:
        assert own and svg and not image, name


def _seed_cache(base, data_dir, fetched_at):
    conn = db.connect(data_dir / "homeboard.db")
    try:
        text = db.get_settings(conn)["weather_location"]
        assert db.set_weather_place(conn, text, 33.38, -84.8, "Newnan")
    finally:
        conn.close()
    cache = {
        "location": "Newnan",
        "location_text": text,
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "current": {"temp": 71, "label": "Sunny", "icon": "sun", "is_day": True},
        "days": _days(date.today()),
    }
    weather.cache_path(data_dir).write_text(json.dumps(cache), encoding="utf-8")


CARD = """JSON.stringify((() => {
  const c = document.querySelector('#screen .tv-card[data-kind="weather"]');
  if (!c) return null;
  const q = s => c.querySelector(s) ? c.querySelector(s).textContent : null;
  const r = c.getBoundingClientRect();
  const inner = c.querySelector('.tv-wx').getBoundingClientRect();
  return {title: document.getElementById('title').textContent, h2: q('h2'), temp: q('.wx-temp'),
    label: q('.wx-label'), hilo: q('.wx-hilo'), rain: q('.wx-rain'), asof: q('.wx-asof'),
    where: q('.wx-where'), sun: q('.wx-sun'), svg: c.querySelectorAll('svg').length,
    days: [...c.querySelectorAll('.wx-day')].map(d => d.innerText.replace(/\\s+/g, ' ').trim()),
    fits: inner.bottom <= r.bottom && inner.right <= r.right, text: c.innerText};
})())"""


def _show(server, tmp_path, screen_name, ready):  # noqa: F811
    screens = {s["name"]: s for s in httpx.get(f"{server}/api/screens").json()}
    sid = screens[screen_name]["id"]
    httpx.put(f"{server}/api/settings", json={"channel": "screen", "screen_id": sid})
    chrome = _Chrome(tmp_path / "chrome", 1920, 1080)
    try:
        chrome.send("Page.navigate", {"url": f"{server}/tv"})
        out = None
        for _ in range(100):
            out = json.loads(chrome.eval(CARD) or "null")
            if out and out["title"] == screen_name and ready(out):
                break
            time.sleep(0.05)
    finally:
        chrome.close()
    return out


def _data_dir(tmp_path):
    return tmp_path / "data"


@needs_chrome
def test_daily_brief_weather_card_shows_cached_forecast(server, tmp_path):  # noqa: F811
    weather._last_fail.clear()
    _seed_cache(server, _data_dir(tmp_path), datetime.now(UTC))
    out = _show(server, tmp_path, "Daily Brief", lambda o: o["temp"])
    assert out["h2"] == "Newnan"
    assert out["temp"] == "71°" and out["label"] == "Sunny"
    assert out["hilo"] == "H 78 L 55" and out["rain"] == "Rain 20%"
    assert out["asof"] is None and out["days"] == [] and out["sun"] is None
    assert out["svg"] == 1 and out["fits"]


@needs_chrome
def test_stale_forecast_shows_as_of_line(server, tmp_path):  # noqa: F811
    weather._last_fail.clear()
    fetched = datetime.now(UTC) - timedelta(hours=3)
    _seed_cache(server, _data_dir(tmp_path), fetched)
    out = _show(server, tmp_path, "Daily Brief", lambda o: o["temp"])
    local = fetched.astimezone()
    clock = f"{local.hour % 12 or 12}:{local.minute:02d} {'am' if local.hour < 12 else 'pm'}"
    assert out["temp"] == "71°"
    assert out["asof"] == f"as of {clock}"


@needs_chrome
def test_weather_card_without_forecast_says_so(server, tmp_path):  # noqa: F811
    weather._last_fail.clear()
    assert not weather.cache_path(_data_dir(tmp_path)).exists()
    out = _show(server, tmp_path, "Daily Brief", lambda o: o["label"])
    assert out["h2"] == "Weather"
    assert out["label"] == "No forecast yet"
    assert out["where"] == "30263"
    assert out["temp"] is None


@needs_chrome
def test_full_size_weather_adds_next_days_and_sun_times(server, tmp_path):  # noqa: F811
    weather._last_fail.clear()
    _seed_cache(server, _data_dir(tmp_path), datetime.now(UTC))
    screen = httpx.post(f"{server}/api/screens", json={"name": "Sky"}).json()
    r = httpx.post(f"{server}/api/screens/{screen['id']}/widgets", json={"kind": "weather"})
    assert r.status_code == 201, r.text
    out = _show(server, tmp_path, "Sky", lambda o: o["temp"])
    today = date.today()
    names = [(today + timedelta(days=i)).strftime("%a") for i in (1, 2)]
    assert out["temp"] == "71°" and out["hilo"] == "H 78 L 55"
    assert out["days"] == [f"{names[0]} H 70 L 52", f"{names[1]} H 66 L 50"]
    assert out["sun"] and "Sunrise 7:50 am" in out["sun"] and "Sunset 6:50 pm" in out["sun"]
    assert out["svg"] == 3 and out["fits"]
