import json
import logging
import re
import sqlite3
import threading
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from app import db, events

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
ZIP_URL = "https://api.zippopotam.us/us/"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CACHE_NAME = "weather.json"
TIMEOUT_S = 10
REFRESH_AFTER = timedelta(minutes=30)
STALE_AFTER = timedelta(minutes=90)
RETRY_AFTER = timedelta(minutes=5)
FORECAST_DAYS = 3

CODES = {
    0: ("Sunny", "sun"),
    1: ("Mostly sunny", "cloud-sun"),
    2: ("Partly cloudy", "cloud-sun"),
    3: ("Cloudy", "cloud"),
    45: ("Fog", "fog"),
    48: ("Fog", "fog"),
    51: ("Drizzle", "drizzle"),
    53: ("Drizzle", "drizzle"),
    55: ("Drizzle", "drizzle"),
    56: ("Freezing drizzle", "sleet"),
    57: ("Freezing drizzle", "sleet"),
    61: ("Light rain", "rain"),
    63: ("Rain", "rain"),
    65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "sleet"),
    67: ("Freezing rain", "sleet"),
    71: ("Light snow", "snow"),
    73: ("Snow", "snow"),
    75: ("Heavy snow", "snow"),
    77: ("Snow", "snow"),
    80: ("Showers", "rain"),
    81: ("Showers", "rain"),
    82: ("Heavy showers", "rain"),
    85: ("Snow showers", "snow"),
    86: ("Snow showers", "snow"),
    95: ("Thunderstorms", "thunder"),
    96: ("Thunderstorms", "thunder"),
    99: ("Thunderstorms", "thunder"),
}
NIGHT = {
    "Sunny": "Clear",
    "Mostly sunny": "Mostly clear",
    "sun": "moon",
    "cloud-sun": "cloud-moon",
}
UNKNOWN = ("Unknown", "cloud")

log = logging.getLogger(__name__)
_lock = threading.Lock()
_last_fail: dict[Path, datetime] = {}
FAILURES = (OSError, ValueError, LookupError, TypeError, sqlite3.Error)


def fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "homeboard"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.load(resp)


def _now() -> datetime:
    return datetime.now(UTC)


def describe(code, is_day=True) -> tuple[str, str]:
    label, icon = CODES.get(code, UNKNOWN)
    if not is_day:
        label, icon = NIGHT.get(label, label), NIGHT.get(icon, icon)
    return label, icon


def cache_path(data_dir) -> Path:
    return Path(data_dir) / CACHE_NAME


def drop_cache(data_dir) -> None:
    path = cache_path(data_dir)
    with _lock:
        path.unlink(missing_ok=True)
        _last_fail.pop(path, None)


def geocode(text: str) -> tuple[float, float, str]:
    is_zip = re.fullmatch(r"\d{5}", text) is not None
    query = {"name": text, "count": 1}
    if is_zip:
        query["countryCode"] = "US"
    try:
        results = fetch_json(f"{GEOCODE_URL}?{urlencode(query)}").get("results") or []
    except FAILURES:
        if not is_zip:
            raise
        results = []
    if results:
        r = results[0]
        return float(r["latitude"]), float(r["longitude"]), r.get("name") or text
    if not is_zip:
        raise LookupError(f"no place found for {text!r}")
    place = fetch_json(f"{ZIP_URL}{text}")["places"][0]
    return float(place["latitude"]), float(place["longitude"]), place.get("place name") or text


def forecast(lat: float, lon: float) -> dict:
    query = {
        "latitude": lat,
        "longitude": lon,
        "temperature_unit": "fahrenheit",
        "timezone": "auto",
        "current": "temperature_2m,weather_code,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max,sunrise,sunset",
        "forecast_days": FORECAST_DAYS,
    }
    data = fetch_json(f"{FORECAST_URL}?{urlencode(query)}")
    cur = data["current"]
    is_day = bool(cur.get("is_day", 1))
    label, icon = describe(cur.get("weather_code"), is_day)
    daily = data["daily"]
    days = []
    for i, day in enumerate(daily["time"]):
        d_label, d_icon = describe(daily["weather_code"][i])
        days.append(
            {
                "date": day,
                "high": _round(daily["temperature_2m_max"][i]),
                "low": _round(daily["temperature_2m_min"][i]),
                "rain": _round(daily["precipitation_probability_max"][i]),
                "label": d_label,
                "icon": d_icon,
                "sunrise": daily["sunrise"][i],
                "sunset": daily["sunset"][i],
            }
        )
    return {
        "current": {
            "temp": _round(cur.get("temperature_2m")),
            "label": label,
            "icon": icon,
            "is_day": is_day,
        },
        "days": days,
    }


def _round(v):
    return None if v is None else round(v)


def _read_cache(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_cache(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def _age(cache: dict) -> timedelta:
    try:
        fetched = datetime.fromisoformat(cache["fetched_at"])
    except (KeyError, TypeError, ValueError):
        return timedelta.max
    return _now() - fetched


def _refresh(conn: sqlite3.Connection, path: Path) -> dict:
    s = db.get_settings(conn)
    text = s["weather_location"]
    lat, lon, name = s["weather_lat"], s["weather_lon"], s["weather_name"]
    if lat is None or lon is None:
        lat, lon, name = geocode(text)
        db.set_weather_place(conn, text, lat, lon, name)
    data = {
        "location": name or text,
        "location_text": text,
        "fetched_at": _now().isoformat(timespec="seconds"),
        **forecast(lat, lon),
    }
    _write_cache(path, data)
    return data


def get_weather(conn: sqlite3.Connection, data_dir) -> dict:
    path = cache_path(data_dir)
    with _lock:
        cache = _read_cache(path)
        s = db.get_settings(conn)
        due = (
            cache is None
            or s["weather_lat"] is None
            or s["weather_lon"] is None
            or cache.get("location_text") != s["weather_location"]
            or _age(cache) >= REFRESH_AFTER
        )
        failed = _last_fail.get(path)
        if due and (failed is None or _now() - failed >= RETRY_AFTER):
            try:
                cache = _refresh(conn, path)
            except FAILURES as e:
                log.warning("weather refresh failed: %s", e)
                _last_fail[path] = _now()
            else:
                _last_fail.pop(path, None)
                events.publish("weather_changed")
    if cache is None:
        return {"ok": False, "error": "no forecast yet", "location": s["weather_location"]}
    return {
        "ok": True,
        "location": cache.get("location"),
        "fetched_at": cache.get("fetched_at"),
        "stale": _age(cache) > STALE_AFTER,
        "current": cache.get("current"),
        "days": cache.get("days", []),
    }
