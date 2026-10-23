import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import db, events, weather
from app.main import create_app

GEO = {"results": [{"name": "Newnan", "latitude": 33.38, "longitude": -84.8}]}
ZIP = {"places": [{"place name": "Marietta", "latitude": "33.97", "longitude": "-84.5"}]}
FORECAST = {
    "current": {"temperature_2m": 71.4, "weather_code": 0, "is_day": 1},
    "daily": {
        "time": ["2026-10-23", "2026-10-24", "2026-10-25"],
        "weather_code": [2, 61, 95],
        "temperature_2m_max": [78.2, 70.0, 66.6],
        "temperature_2m_min": [55.0, 52.4, 49.9],
        "precipitation_probability_max": [20, 80, None],
        "sunrise": ["2026-10-23T07:50", "2026-10-24T07:51", "2026-10-25T07:52"],
        "sunset": ["2026-10-23T18:55", "2026-10-24T18:54", "2026-10-25T18:53"],
    },
}


class Upstream:
    def __init__(self, geo=GEO, zip_=ZIP, forecast=FORECAST):
        self.geo, self.zip, self.forecast = geo, zip_, forecast
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        for prefix, value in (
            (weather.GEOCODE_URL, self.geo),
            (weather.ZIP_URL, self.zip),
            (weather.FORECAST_URL, self.forecast),
        ):
            if url.startswith(prefix):
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(url)

    def count(self, prefix):
        return sum(url.startswith(prefix) for url in self.calls)


@pytest.fixture
def upstream(monkeypatch):
    up = Upstream()
    monkeypatch.setattr(weather, "fetch_json", up)
    return up


@pytest.fixture
def client(tmp_path, upstream):
    weather._last_fail.clear()
    with TestClient(create_app(tmp_path / "data")) as c:
        yield c


def _settings(tmp_path):
    conn = db.connect(tmp_path / "data" / "homeboard.db")
    try:
        return db.get_settings(conn)
    finally:
        conn.close()


def _later(monkeypatch, minutes):
    real = weather._now()
    monkeypatch.setattr(weather, "_now", lambda: real + timedelta(minutes=minutes))


def test_fresh_app_returns_forecast(client, upstream, tmp_path):
    body = client.get("/api/weather").json()
    assert body["ok"] is True
    assert body["location"] == "Newnan"
    assert body["stale"] is False
    assert body["current"] == {"temp": 71, "label": "Sunny", "icon": "sun", "is_day": True}
    assert len(body["days"]) == 3
    assert {k: body["days"][0][k] for k in ("date", "high", "low", "rain", "label", "icon")} == {
        "date": "2026-10-23",
        "high": 78,
        "low": 55,
        "rain": 20,
        "label": "Partly cloudy",
        "icon": "cloud-sun",
    }
    assert [d["icon"] for d in body["days"]] == ["cloud-sun", "rain", "thunder"]
    assert body["days"][2]["rain"] is None
    geo = next(u for u in upstream.calls if u.startswith(weather.GEOCODE_URL))
    assert "name=30263" in geo and "countryCode=US" in geo
    fc = next(u for u in upstream.calls if u.startswith(weather.FORECAST_URL))
    assert "temperature_unit=fahrenheit" in fc and "forecast_days=3" in fc
    s = _settings(tmp_path)
    assert (s["weather_lat"], s["weather_lon"], s["weather_name"]) == (33.38, -84.8, "Newnan")


def test_mapping_table():
    assert weather.describe(0) == ("Sunny", "sun")
    assert weather.describe(0, is_day=False) == ("Clear", "moon")
    assert weather.describe(2, is_day=False) == ("Partly cloudy", "cloud-moon")
    assert weather.describe(3) == ("Cloudy", "cloud")
    assert weather.describe(45) == ("Fog", "fog")
    assert weather.describe(63) == ("Rain", "rain")
    assert weather.describe(73) == ("Snow", "snow")
    assert weather.describe(95) == ("Thunderstorms", "thunder")
    assert weather.describe(1234) == ("Unknown", "cloud")


def test_geocode_falls_back_to_zippopotam(client, upstream, tmp_path):
    upstream.geo = {"generationtime_ms": 0.1}
    body = client.get("/api/weather").json()
    assert body["ok"] is True and body["location"] == "Marietta"
    assert upstream.count(weather.ZIP_URL + "30263") == 1
    s = _settings(tmp_path)
    assert (s["weather_lat"], s["weather_lon"], s["weather_name"]) == (33.97, -84.5, "Marietta")


def test_place_name_text_skips_country_and_zip(client, upstream, tmp_path):
    client.put("/api/settings", json={"weather_location": "Newnan, GA"})
    upstream.geo = {}
    body = client.get("/api/weather").json()
    assert body == {"ok": False, "error": "no forecast yet", "location": "Newnan, GA"}
    assert upstream.count(weather.ZIP_URL) == 0
    assert "countryCode" not in upstream.calls[0]
    assert _settings(tmp_path)["weather_lat"] is None


def test_two_calls_within_30_minutes_fetch_once(client, upstream, monkeypatch):
    client.get("/api/weather")
    _later(monkeypatch, 29)
    client.get("/api/weather")
    assert upstream.count(weather.FORECAST_URL) == 1
    assert upstream.count(weather.GEOCODE_URL) == 1
    _later(monkeypatch, 31)
    client.get("/api/weather")
    assert upstream.count(weather.FORECAST_URL) == 2
    assert upstream.count(weather.GEOCODE_URL) == 1


def test_failed_fetch_serves_cache_with_stale_flag(client, upstream, monkeypatch):
    first = client.get("/api/weather").json()
    upstream.forecast = OSError("offline")
    _later(monkeypatch, 60)
    body = client.get("/api/weather").json()
    assert upstream.count(weather.FORECAST_URL) == 2
    assert body["ok"] is True and body["stale"] is False
    assert body["fetched_at"] == first["fetched_at"]
    assert body["days"] == first["days"]
    _later(monkeypatch, 120)
    body = client.get("/api/weather").json()
    assert body["ok"] is True and body["stale"] is True
    assert body["fetched_at"] == first["fetched_at"]


def test_no_cache_and_failed_fetch(client, upstream):
    upstream.forecast = OSError("offline")
    r = client.get("/api/weather")
    assert r.status_code == 200
    assert r.json() == {"ok": False, "error": "no forecast yet", "location": "30263"}


def test_geocode_failure_leaves_place_null_and_retries(client, upstream, tmp_path, monkeypatch):
    upstream.geo = OSError("offline")
    upstream.zip = OSError("offline")
    assert client.get("/api/weather").json()["ok"] is False
    assert _settings(tmp_path)["weather_lat"] is None
    client.get("/api/weather")
    assert upstream.count(weather.GEOCODE_URL) == 1
    upstream.geo, upstream.zip = GEO, ZIP
    _later(monkeypatch, 6)
    assert client.get("/api/weather").json()["ok"] is True
    assert _settings(tmp_path)["weather_name"] == "Newnan"


def test_location_change_drops_cache_and_geocodes_again(client, upstream, tmp_path):
    client.get("/api/weather")
    assert upstream.count(weather.GEOCODE_URL) == 1
    assert client.put("/api/settings", json={"weather_location": "30062"}).status_code == 200
    assert not (tmp_path / "data" / weather.CACHE_NAME).exists()
    upstream.geo = {"results": [{"name": "Marietta", "latitude": 33.97, "longitude": -84.5}]}
    body = client.get("/api/weather").json()
    assert upstream.count(weather.GEOCODE_URL) == 2
    assert "name=30062" in upstream.calls[-2]
    assert body["location"] == "Marietta"
    assert _settings(tmp_path)["weather_lat"] == 33.97


def test_cache_file_lives_in_data_dir_only(client, tmp_path):
    before = {p for p in tmp_path.rglob("*")}
    client.get("/api/weather")
    added = {p for p in tmp_path.rglob("*")} - before
    cache = tmp_path / "data" / weather.CACHE_NAME
    assert added <= {cache, tmp_path / "data" / "homeboard.db-journal"}
    assert cache in added
    assert json.loads(cache.read_text())["location"] == "Newnan"


def test_refresh_publishes_weather_changed(client, upstream):
    events.last_event = None
    client.get("/api/weather")
    assert events.last_event == "weather_changed"
    events.last_event = None
    upstream.forecast = OSError("offline")
    client.put("/api/settings", json={"weather_location": "30062"})
    events.last_event = None
    client.get("/api/weather")
    assert events.last_event is None
