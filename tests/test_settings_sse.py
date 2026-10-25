import asyncio

import pytest
from fastapi.testclient import TestClient

from app import __version__, events
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_put_interval_persists(client):
    r = client.put("/api/settings", json={"interval_s": 30})
    assert r.status_code == 200
    assert r.json()["interval_s"] == 30
    assert events.last_event == "settings_changed"
    body = client.get("/api/settings").json()
    assert body["interval_s"] == 30
    assert set(body) == {
        "interval_s",
        "transition",
        "fit",
        "shuffle",
        "captions",
        "channel",
        "channel_timeout_min",
        "screen_id",
        "clock_size",
        "caption_size",
        "clock_pos",
        "caption_pos",
        "clock_on_top",
        "date_size",
        "date_pos",
        "date_on_top",
        "date_show",
        "brightness",
        "weather_location",
        "weather_lat",
        "weather_lon",
        "weather_name",
        "speaker_idle_minutes",
        "updated_at",
    }


def test_put_interval_out_of_range_is_422(client):
    assert client.put("/api/settings", json={"interval_s": 3}).status_code == 422
    assert client.get("/api/settings").json()["interval_s"] == 20


def test_put_unknown_fit_is_422(client):
    assert client.put("/api/settings", json={"fit": "stretch"}).status_code == 422


def test_put_subset_of_fields(client):
    r = client.put("/api/settings", json={"fit": "cover", "shuffle": True})
    assert r.status_code == 200
    body = r.json()
    assert (body["fit"], body["shuffle"], body["interval_s"]) == ("cover", True, 20)


def test_subscriber_receives_published_event():
    async def run():
        async with events.subscribe("127.0.0.1") as queue:
            assert events.display_connected()
            events.publish("photos_changed")
            name = await asyncio.wait_for(queue.get(), 1)
        assert not events.display_connected()
        return name

    assert asyncio.run(run()) == "photos_changed"
    assert events.subscribers == set()


def test_events_stream_starts_with_hello(client):
    client.app.state.sse_limit = 0
    with client.stream("GET", "/api/events", headers={"Accept": "text/event-stream"}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["cache-control"] == "no-cache"
        assert r.headers["x-accel-buffering"] == "no"
        first = next(r.iter_text())
    assert first.startswith("event: hello")
    assert '"interval_s": 20' in first
    assert events.subscribers == set()


def test_status_has_nine_keys(client):
    body = client.get("/api/status").json()
    assert set(body) == {
        "display_connected",
        "photo_count",
        "current_photo_id",
        "channel",
        "screen_id",
        "hostname",
        "ip",
        "disk_free_bytes",
        "version",
    }
    assert body["photo_count"] == 0
    assert body["display_connected"] is False
    assert body["current_photo_id"] is None
    assert body["version"] == __version__ == "0.1.0"
    assert isinstance(body["disk_free_bytes"], int) and body["disk_free_bytes"] > 0
