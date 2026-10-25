import json
import logging
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app import spotify
from app.main import create_app

PLAYER = {
    "is_playing": True,
    "progress_ms": 61000,
    "device": {"id": "dev1", "name": "Home Board", "is_active": True, "volume_percent": 40},
    "item": {
        "name": "Song One",
        "duration_ms": 200000,
        "artists": [{"name": "Band A"}, {"name": "Band B"}],
        "album": {
            "name": "Album X",
            "images": [
                {"url": "https://i.scdn.co/small", "width": 64, "height": 64},
                {"url": "https://i.scdn.co/big", "width": 640, "height": 640},
                {"url": "https://i.scdn.co/mid", "width": 300, "height": 300},
            ],
        },
    },
}
NO_DEVICE = (
    404,
    {"error": {"status": 404, "message": "Player command failed: No active device found"}},
)


class FakeSpotify:
    def __init__(self):
        self.calls = []
        self.routes = {
            ("POST", "https://accounts.spotify.com/api/token"): (
                200,
                {"access_token": "acc", "expires_in": 3600, "refresh_token": "ref"},
            ),
            ("GET", "/me/player"): (200, PLAYER),
            ("PUT", "/me/player/play"): (204, None),
            ("PUT", "/me/player/pause"): (204, None),
            ("POST", "/me/player/next"): (204, None),
            ("POST", "/me/player/previous"): (204, None),
            ("PUT", "/me/player/volume"): (204, None),
            ("PUT", "/me/player"): (204, None),
        }

    def __call__(self, method, url, headers=None, body=None):
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}{parts.path}"
        path = key.removeprefix(spotify.API_URL)
        self.calls.append((method, path, parse_qs(parts.query), headers or {}, body))
        value = self.routes.get((method, path))
        if value is None:
            raise AssertionError((method, url))
        if isinstance(value, Exception):
            raise value
        return value

    def paths(self):
        return [(m, p) for m, p, *_ in self.calls]

    def count(self, method, path):
        return self.paths().count((method, path))


@pytest.fixture
def fake(monkeypatch):
    fake = FakeSpotify()
    monkeypatch.setattr(spotify, "fetch", fake)
    return fake


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.delenv("HOMEBOARD_HOST", raising=False)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def client(data_dir, fake):
    spotify.forget()
    with TestClient(create_app(data_dir)) as c:
        yield c
    spotify.forget()


@pytest.fixture
def linked(client, creds, data_dir):
    spotify.token_path(data_dir).write_text(json.dumps({"refresh_token": "ref"}))
    return client


def test_no_credentials_says_not_linked(client, fake, monkeypatch, caplog):
    for name in ("CREDENTIALS_DIRECTORY", "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    caplog.set_level(logging.DEBUG)
    r = client.get("/api/spotify/now")
    assert r.status_code == 200
    assert r.json() == {"linked": False, "reason": "no credentials"}
    for path in ("/play", "/pause", "/next", "/previous", "/play-playlist"):
        body = {"id": "p1"} if path == "/play-playlist" else None
        assert client.post("/api/spotify" + path, json=body).json()["linked"] is False
    assert client.get("/api/spotify/playlists").json()["linked"] is False
    assert client.get("/api/spotify/login", follow_redirects=False).json()["linked"] is False
    assert fake.calls == []
    assert not [rec for rec in caplog.records if rec.exc_info]


def test_credentials_file_wins(tmp_path, monkeypatch):
    (tmp_path / "spotify").write_text(
        "SPOTIFY_CLIENT_ID=file-id\nSPOTIFY_CLIENT_SECRET=file-secret\n"
    )
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "env-id")
    assert spotify.credentials() == ("file-id", "file-secret")


def test_not_linked_without_refresh_token(client, creds, fake):
    assert client.get("/api/spotify/now").json() == {"linked": False, "reason": "not linked"}
    assert fake.calls == []


def test_now_playing_from_fake(linked, fake):
    body = linked.get("/api/spotify/now").json()
    assert body == {
        "linked": True,
        "playing": True,
        "on_board": True,
        "track": "Song One",
        "artist": "Band A, Band B",
        "album": "Album X",
        "cover": "https://i.scdn.co/big",
        "progress_ms": 61000,
        "duration_ms": 200000,
        "volume": 40,
        "device": "Home Board",
        "stale": False,
    }
    token_call = fake.calls[0]
    assert token_call[0] == "POST" and parse_qs(token_call[4].decode()) == {
        "grant_type": ["refresh_token"],
        "refresh_token": ["ref"],
    }
    assert fake.calls[1][3]["Authorization"] == "Bearer acc"


def test_other_device_is_not_on_board(linked, fake):
    fake.routes[("GET", "/me/player")] = (
        200,
        {**PLAYER, "is_playing": False, "device": {"name": "Phone", "volume_percent": 70}},
    )
    body = linked.get("/api/spotify/now").json()
    assert body["playing"] is False and body["on_board"] is False
    assert body["device"] == "Phone" and body["track"] == "Song One"


def test_nothing_playing(linked, fake):
    fake.routes[("GET", "/me/player")] = (204, None)
    body = linked.get("/api/spotify/now").json()
    assert body["linked"] is True and body["playing"] is False and body["on_board"] is False
    assert all(body[k] is None for k in ("track", "artist", "cover", "progress_ms", "device"))


def test_two_polls_inside_3_seconds_make_one_call(linked, fake, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(spotify, "_clock", lambda: clock[0])
    linked.get("/api/spotify/now")
    clock[0] += 2.9
    linked.get("/api/spotify/now")
    assert fake.count("GET", "/me/player") == 1
    clock[0] += 0.2
    linked.get("/api/spotify/now")
    assert fake.count("GET", "/me/player") == 2


def test_spotify_calls_share_one_tls_context(monkeypatch):
    seen = []

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def urlopen(req, timeout, context):
        seen.append(context)
        return Resp()

    monkeypatch.setattr(spotify.urllib.request, "urlopen", urlopen)
    spotify.fetch("GET", "https://api.spotify.com/v1/me/player")
    spotify.fetch("GET", "https://api.spotify.com/v1/me/player")
    assert seen[0] is not None and seen[0] is seen[1]


def test_network_failure_returns_last_known_as_stale(linked, fake, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(spotify, "_clock", lambda: clock[0])
    assert linked.get("/api/spotify/now").json()["stale"] is False
    fake.routes[("GET", "/me/player")] = OSError("down")
    clock[0] += 5
    body = linked.get("/api/spotify/now").json()
    assert body["stale"] is True and body["track"] == "Song One"


def test_token_is_reused_until_60_seconds_before_expiry(linked, fake, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(spotify, "_clock", lambda: clock[0])
    linked.post("/api/spotify/pause")
    clock[0] += 3600 - 61
    linked.post("/api/spotify/pause")
    assert fake.count("POST", "https://accounts.spotify.com/api/token") == 1
    clock[0] += 2
    linked.post("/api/spotify/pause")
    assert fake.count("POST", "https://accounts.spotify.com/api/token") == 2


def test_controls_call_the_right_endpoints(linked, fake):
    for path, call in (
        ("/pause", ("PUT", "/me/player/pause")),
        ("/next", ("POST", "/me/player/next")),
        ("/previous", ("POST", "/me/player/previous")),
        ("/play", ("PUT", "/me/player/play")),
    ):
        r = linked.post("/api/spotify" + path)
        assert r.status_code == 200 and r.json()["ok"] is True
        assert fake.paths()[-1] == call
    r = linked.put("/api/spotify/volume", json={"percent": 35})
    assert r.status_code == 200
    assert fake.calls[-1][:3] == ("PUT", "/me/player/volume", {"volume_percent": ["35"]})
    assert linked.put("/api/spotify/volume", json={"percent": 101}).status_code == 422


def test_no_active_device_is_409(linked, fake):
    fake.routes[("PUT", "/me/player/pause")] = NO_DEVICE
    fake.routes[("POST", "/me/player/next")] = NO_DEVICE
    for path in ("/pause", "/next"):
        r = linked.post("/api/spotify" + path)
        assert r.status_code == 409
        assert r.json() == {"detail": "no active device"}


def test_playlists_follow_pagination(linked, fake):
    second = f"{spotify.API_URL}/me/playlists/page2"
    fake.routes[("GET", "/me/playlists")] = (
        200,
        {
            "items": [
                {
                    "id": "p1",
                    "name": "Mix",
                    "images": [{"url": "https://i/1", "width": 300, "height": 300}],
                    "tracks": {"total": 12},
                }
            ],
            "next": second,
        },
    )
    fake.routes[("GET", "/me/playlists/page2")] = (
        200,
        {
            "items": [{"id": "p2", "name": "Chill", "images": None, "tracks": {"total": 3}}],
            "next": None,
        },
    )
    body = linked.get("/api/spotify/playlists").json()
    assert body == {
        "linked": True,
        "playlists": [
            {"id": "p1", "name": "Mix", "cover": "https://i/1", "tracks": 12},
            {"id": "p2", "name": "Chill", "cover": None, "tracks": 3},
        ],
    }


def test_play_playlist_moves_to_board_then_plays(linked, fake):
    fake.routes[("GET", "/me/player/devices")] = (
        200,
        {
            "devices": [
                {"id": "ph", "name": "Phone", "is_active": True},
                {"id": "hb", "name": "Home Board", "is_active": False},
            ]
        },
    )
    r = linked.post("/api/spotify/play-playlist", json={"id": "p1"})
    assert r.status_code == 200
    transfer, play = fake.calls[-2], fake.calls[-1]
    assert transfer[:2] == ("PUT", "/me/player")
    assert json.loads(transfer[4]) == {"device_ids": ["hb"], "play": False}
    assert play[:3] == ("PUT", "/me/player/play", {"device_id": ["hb"]})
    assert json.loads(play[4]) == {"context_uri": "spotify:playlist:p1"}


def test_play_playlist_skips_transfer_when_board_active(linked, fake):
    fake.routes[("GET", "/me/player/devices")] = (
        200,
        {"devices": [{"id": "hb", "name": "Home Board", "is_active": True}]},
    )
    linked.post("/api/spotify/play-playlist", json={"id": "p1"})
    assert fake.count("PUT", "/me/player") == 0


def test_play_playlist_without_board_device_is_409(linked, fake):
    fake.routes[("GET", "/me/player/devices")] = (200, {"devices": []})
    r = linked.post("/api/spotify/play-playlist", json={"id": "p1"})
    assert r.status_code == 409


def test_shown_playlists_keep_order_and_clear(client):
    assert client.get("/api/spotify/shown").json() == []
    two = [
        {"id": "p2", "name": "Chill", "cover": None},
        {"id": "p1", "name": "Mix", "cover": "https://i/1"},
    ]
    assert client.put("/api/spotify/shown", json=two).status_code == 200
    assert client.get("/api/spotify/shown").json() == two
    assert client.put("/api/spotify/shown", json=[]).json() == []
    assert client.get("/api/spotify/shown").json() == []


def test_login_redirects_and_callback_saves_token(linked, fake, data_dir, monkeypatch):
    spotify.token_path(data_dir).unlink()
    monkeypatch.setenv("HOMEBOARD_HOST", "homeboard.example.com")
    r = linked.get("/api/spotify/login", follow_redirects=False)
    assert r.status_code in (302, 303, 307)
    url = urlsplit(r.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == spotify.AUTHORIZE_URL
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    redirect = "https://homeboard.example.com/api/spotify/callback"
    assert q["client_id"] == "cid" and q["response_type"] == "code"
    assert q["redirect_uri"] == redirect
    assert set(q["scope"].split()) == {
        "user-read-playback-state",
        "user-modify-playback-state",
        "playlist-read-private",
        "playlist-read-collaborative",
    }
    r = linked.get(
        "/api/spotify/callback",
        params={"code": "abc", "state": q["state"]},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/admin/sound"
    form = parse_qs(fake.calls[-1][4].decode())
    assert form == {
        "grant_type": ["authorization_code"],
        "code": ["abc"],
        "redirect_uri": [redirect],
    }
    assert json.loads(spotify.token_path(data_dir).read_text()) == {"refresh_token": "ref"}
    again = linked.get(
        "/api/spotify/callback",
        params={"code": "abc", "state": q["state"]},
        follow_redirects=False,
    )
    assert again.status_code == 400


def test_callback_with_wrong_state_is_400_and_saves_nothing(client, creds, fake, data_dir):
    client.get("/api/spotify/login", follow_redirects=False)
    r = client.get("/api/spotify/callback", params={"code": "abc", "state": "wrong"})
    assert r.status_code == 400
    assert not spotify.token_path(data_dir).exists()
    assert fake.calls == []


def test_unlink_deletes_token(linked, fake, data_dir):
    assert linked.get("/api/spotify/now").json()["linked"] is True
    assert linked.post("/api/spotify/unlink").json() == {"linked": False, "reason": "not linked"}
    assert not spotify.token_path(data_dir).exists()
    assert linked.get("/api/spotify/now").json()["linked"] is False
