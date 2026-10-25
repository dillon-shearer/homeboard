import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app import events, spotify
from app.main import create_app
from tests.test_spotify import FakeSpotify


def test_subscriber_that_never_reads_holds_a_bounded_queue():
    async def check():
        async with events.subscribe() as queue:
            for i in range(500):
                events.publish("spotify_changed")
                events.publish(f"event_{i}")
            await asyncio.sleep(0)
            assert queue.qsize() <= events.QUEUE_MAX
            assert queue.qsize() == events.QUEUE_MAX

    asyncio.run(check())


def test_repeated_events_coalesce_and_keep_others():
    async def check():
        async with events.subscribe() as queue:
            for _ in range(50):
                events.publish("spotify_changed")
            events.publish("lists_changed")
            events.publish("spotify_changed")
            got = [queue.get_nowait() for _ in range(queue.qsize())]
            assert got == ["spotify_changed", "lists_changed"]
            events.publish("spotify_changed")
            assert await asyncio.wait_for(queue.get(), 1) == "spotify_changed"

    asyncio.run(check())


def test_events_from_a_worker_thread_stay_bounded():
    async def check():
        async with events.subscribe() as queue:
            sender = threading.Thread(
                target=lambda: [events.publish(f"e{i % 100}") for i in range(2000)]
            )
            sender.start()
            await asyncio.to_thread(sender.join)
            await asyncio.sleep(0.05)
            assert queue.qsize() <= events.QUEUE_MAX

    asyncio.run(check())


@pytest.fixture
def linked(tmp_path, monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    spotify.forget()
    spotify.token_path(tmp_path).write_text(json.dumps({"refresh_token": "ref"}))
    fake = FakeSpotify()
    monkeypatch.setattr(spotify, "fetch", fake)
    yield tmp_path, fake
    spotify.forget()


def _workers():
    return [t for t in threading.enumerate() if t.name == "spotify-settle"]


def test_burst_of_control_calls_runs_one_settle_worker(linked, monkeypatch):
    data_dir, fake = linked
    monkeypatch.setattr(spotify, "SETTLE_S", (0.05, 0.1))
    most = 0
    with TestClient(create_app(data_dir)) as client:
        for i in range(20):
            path = "/api/spotify/next" if i % 2 else "/api/spotify/pause"
            assert client.post(path).status_code == 200
            most = max(most, len(_workers()))
        reads = fake.count("GET", "/me/player")
        for t in _workers():
            t.join(2)
    assert most == 1
    assert _workers() == []
    assert fake.count("GET", "/me/player") - reads <= 2


def test_newer_settle_wakes_the_sleeping_worker(linked, monkeypatch):
    data_dir, fake = linked
    spotify.now_playing(data_dir)
    monkeypatch.setattr(spotify, "SETTLE_S", (30,))
    spotify.settle(data_dir, lambda name: None)
    worker = _workers()
    assert len(worker) == 1
    monkeypatch.setattr(spotify, "SETTLE_S", ())
    spotify.settle(data_dir, lambda name: None)
    worker[0].join(1)
    assert not worker[0].is_alive()
    assert fake.count("GET", "/me/player") == 1
