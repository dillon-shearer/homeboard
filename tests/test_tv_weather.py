import json
import subprocess
from datetime import date, timedelta
from pathlib import Path

from app import db, weather
from tests.chrome import needs_node

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
                (74, 51, 0, "Sunny", "sun"),
                (69, 50, 10, "Cloudy", "cloud"),
                (40, 30, 60, "Snow", "snow"),
                (61, 48, 5, "Fog", "fog"),
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
        {"name": "Fri", "icon": "cloud-sun", "hiLo": "H 78 L 55", "rain": "20%", "today": True},
        {"name": "Sat", "icon": "rain", "hiLo": "H 70 L 52", "rain": "80%", "today": False},
        {"name": "Sun", "icon": "thunder", "hiLo": "H -- L 49", "rain": "--%", "today": False},
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
