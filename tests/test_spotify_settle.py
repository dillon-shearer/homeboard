import copy
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app import events, spotify
from app.main import create_app
from tests.test_spotify import PLAYER, FakeSpotify

NEW = copy.deepcopy(PLAYER)
NEW["item"]["name"] = "Song Two"
NEW["item"]["album"]["images"] = [{"url": "https://i.scdn.co/new", "width": 640, "height": 640}]


class Lagging(FakeSpotify):
    def __init__(self, old_reads):
        super().__init__()
        self.old_reads = old_reads

    def __call__(self, method, url, headers=None, body=None):
        out = super().__call__(method, url, headers, body)
        if (
            method == "GET"
            and url.endswith("/me/player")
            and self.count("GET", "/me/player") > self.old_reads
        ):
            return (200, NEW)
        return out


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    monkeypatch.setattr(spotify, "SETTLE_S", (0.01, 0.02, 0.03))
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    spotify.forget()
    spotify.token_path(data_dir).write_text(json.dumps({"refresh_token": "ref"}))

    def use(fake):
        monkeypatch.setattr(spotify, "fetch", fake)
        return fake

    yield data_dir, use
    spotify.forget()


def _run(data_dir):
    sent = []
    before = set(threading.enumerate())
    spotify.settle(data_dir, sent.append)
    for t in set(threading.enumerate()) - before:
        t.join(2)
    return sent


def test_settle_publishes_once_when_spotify_catches_up(setup):
    data_dir, use = setup
    fake = use(Lagging(old_reads=2))
    assert spotify.now_playing(data_dir)["track"] == "Song One"
    sent = _run(data_dir)
    assert sent == ["spotify_changed"]
    assert fake.count("GET", "/me/player") == 4
    now = spotify.now_playing(data_dir)
    assert now["track"] == "Song Two"
    assert now["cover"] == "https://i.scdn.co/new"


def test_settle_stays_quiet_when_nothing_changed(setup):
    data_dir, use = setup
    use(FakeSpotify())
    spotify.now_playing(data_dir)
    assert _run(data_dir) == []


def test_newer_settle_stops_the_older_one(setup, monkeypatch):
    data_dir, use = setup
    fake = use(FakeSpotify())
    spotify.now_playing(data_dir)
    monkeypatch.setattr(spotify, "SETTLE_S", (0.2,))
    before = set(threading.enumerate())
    spotify.settle(data_dir, lambda name: None)
    old = set(threading.enumerate()) - before
    monkeypatch.setattr(spotify, "SETTLE_S", ())
    spotify.settle(data_dir, lambda name: None)
    for t in old:
        t.join(2)
    assert fake.count("GET", "/me/player") == 1


@pytest.mark.parametrize(
    "path, body",
    [
        ("/api/spotify/play-playlist", {"id": "p1"}),
        ("/api/spotify/next", None),
        ("/api/spotify/previous", None),
        ("/api/spotify/play", None),
        ("/api/spotify/pause", None),
    ],
)
def test_player_routes_start_settle(setup, monkeypatch, path, body):
    data_dir, use = setup
    use(FakeSpotify())
    started = []
    monkeypatch.setattr(spotify, "settle", lambda d, publish: started.append(publish))
    monkeypatch.setattr(spotify, "play_playlist", lambda d, pid: {"linked": True, "ok": True})
    with TestClient(create_app(data_dir)) as c:
        c.post(path, json=body)
    assert started == [events.publish]


def test_local_event_starts_settle(setup, monkeypatch):
    data_dir, use = setup
    use(FakeSpotify())
    started = []
    monkeypatch.setattr(spotify, "settle", lambda d, publish: started.append(publish))
    with TestClient(create_app(data_dir), client=("127.0.0.1", 50000)) as c:
        assert c.post("/api/spotify/event", json={"type": "track_changed"}).status_code == 200
    assert started == [events.publish]
