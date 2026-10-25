import json

import pytest
from fastapi.testclient import TestClient

from app import spotify
from app.main import create_app
from tests.test_spotify import NO_DEVICE, PLAYER, FakeSpotify

CONTROLS = [
    ("post", "/play", ("PUT", "/me/player/play"), True),
    ("post", "/pause", ("PUT", "/me/player/pause"), False),
    ("post", "/next", ("POST", "/me/player/next"), False),
    ("post", "/previous", ("POST", "/me/player/previous"), False),
    ("put", "/volume", ("PUT", "/me/player/volume"), False),
]
DEVICES = {
    "devices": [
        {"id": "ph", "name": "Phone", "is_active": False},
        {"id": "hb", "name": "Home Board", "is_active": False},
    ]
}


class IdleSpotify(FakeSpotify):
    def __init__(self, devices=DEVICES):
        super().__init__()
        self.active = False
        self.routes[("GET", "/me/player/devices")] = (200, devices)

    def __call__(self, method, url, headers=None, body=None):
        value = super().__call__(method, url, headers, body)
        path = self.calls[-1][1]
        if (method, path) == ("PUT", "/me/player"):
            self.active = True
            return value
        if path.startswith("/me/player") and path != "/me/player/devices" and not self.active:
            return NO_DEVICE
        return value


@pytest.fixture
def fake(monkeypatch):
    fake = IdleSpotify()
    monkeypatch.setattr(spotify, "fetch", fake)
    return fake


@pytest.fixture
def linked(tmp_path, fake, monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    data_dir = tmp_path / "data"
    spotify.forget()
    with TestClient(create_app(data_dir)) as c:
        spotify.token_path(data_dir).write_text(json.dumps({"refresh_token": "ref"}))
        yield c
    spotify.forget()


def _send(client, verb, path):
    body = {"percent": 30} if path == "/volume" else None
    return getattr(client, verb)("/api/spotify" + path, json=body)


@pytest.mark.parametrize("verb,path,call,play", CONTROLS)
def test_control_takes_over_home_board_and_retries(linked, fake, verb, path, call, play):
    r = _send(linked, verb, path)
    assert r.status_code == 200, r.text
    assert r.json() == {"linked": True, "ok": True}
    sent = [c for c in fake.calls if c[1].startswith("/me/player")]
    assert [c[:2] for c in sent] == [
        call,
        ("GET", "/me/player/devices"),
        ("PUT", "/me/player"),
        call,
    ]
    assert json.loads(sent[2][4]) == {"device_ids": ["hb"], "play": play}
    assert sent[3][2]["device_id"] == ["hb"]
    if path == "/volume":
        assert sent[3][2]["volume_percent"] == ["30"]


@pytest.mark.parametrize("verb,path,call,play", CONTROLS)
def test_control_without_home_board_is_409(linked, fake, verb, path, call, play):
    fake.routes[("GET", "/me/player/devices")] = (200, {"devices": [DEVICES["devices"][0]]})
    r = _send(linked, verb, path)
    assert r.status_code == 409
    assert r.json() == {"detail": "Home Board speaker not found"}
    assert fake.count("PUT", "/me/player") == 0


def test_active_device_needs_no_take_over(linked, fake):
    fake.active = True
    assert linked.post("/api/spotify/next").status_code == 200
    assert fake.count("GET", "/me/player/devices") == 0
    assert fake.count("POST", "/me/player/next") == 1


def test_now_after_next_asks_spotify_again(linked, fake, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(spotify, "_clock", lambda: clock[0])
    fake.active = True
    linked.get("/api/spotify/now")
    assert fake.count("GET", "/me/player") == 1
    fake.routes[("GET", "/me/player")] = (
        200,
        {**PLAYER, "item": {**PLAYER["item"], "name": "Two"}},
    )
    assert linked.post("/api/spotify/next").status_code == 200
    clock[0] += 0.5
    assert linked.get("/api/spotify/now").json()["track"] == "Two"
    assert fake.count("GET", "/me/player") == 2


def test_failed_control_still_clears_now_cache(linked, fake, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(spotify, "_clock", lambda: clock[0])
    fake.active = True
    linked.get("/api/spotify/now")
    fake.routes[("POST", "/me/player/next")] = (500, None)
    assert linked.post("/api/spotify/next").status_code == 502
    linked.get("/api/spotify/now")
    assert fake.count("GET", "/me/player") == 2
