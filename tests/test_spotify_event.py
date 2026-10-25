import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import events, spotify
from app.main import create_app
from tests.test_spotify import FakeSpotify


@pytest.fixture
def fake(monkeypatch):
    fake = FakeSpotify()
    monkeypatch.setattr(spotify, "fetch", fake)
    return fake


@pytest.fixture
def app(tmp_path, fake, monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    data_dir = tmp_path / "data"
    spotify.forget()
    app = create_app(data_dir)
    with TestClient(app):
        spotify.token_path(data_dir).write_text(json.dumps({"refresh_token": "ref"}))
        yield app
    spotify.forget()


@pytest.fixture
def local(app):
    return TestClient(app, client=("127.0.0.1", 50000))


def test_local_event_clears_now_cache(local, fake, monkeypatch):
    monkeypatch.setattr(spotify, "_clock", lambda: 1000.0)
    local.get("/api/spotify/now")
    local.get("/api/spotify/now")
    assert fake.count("GET", "/me/player") == 1
    r = local.post("/api/spotify/event", json={"type": "playback_changed", "status": "playing"})
    assert r.status_code == 200
    assert events.last_event == "spotify_changed"
    local.get("/api/spotify/now")
    assert fake.count("GET", "/me/player") == 2


def test_local_event_reaches_sse_subscriber(local):
    async def run():
        async with events.subscribe("127.0.0.1") as queue:
            r = await asyncio.to_thread(
                local.post, "/api/spotify/event", json={"status": "playing"}
            )
            name = await asyncio.wait_for(queue.get(), 1)
        return r.status_code, name

    assert asyncio.run(run()) == (200, "spotify_changed")


def test_ipv6_loopback_is_local(app):
    c = TestClient(app, client=("::1", 50000))
    assert c.post("/api/spotify/event", json={"type": "track_changed"}).status_code == 200


@pytest.mark.parametrize("host", ["192.168.1.20", "testclient"])
def test_remote_event_is_403(app, fake, host, monkeypatch):
    monkeypatch.setattr(spotify, "_clock", lambda: 1000.0)
    local = TestClient(app, client=("127.0.0.1", 50000))
    local.get("/api/spotify/now")
    events.last_event = None
    c = TestClient(app, client=(host, 50000))
    assert c.post("/api/spotify/event", json={"status": "playing"}).status_code == 403
    assert events.last_event is None
    local.get("/api/spotify/now")
    assert fake.count("GET", "/me/player") == 1


def test_tunnel_event_is_403(local):
    r = local.post(
        "/api/spotify/event",
        json={"status": "playing"},
        headers={"X-Homeboard-Tunnel": "1"},
    )
    assert r.status_code in (401, 403)
