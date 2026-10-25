import json

from app import spotify
from tests.test_spotify_take_over import fake, linked

__all__ = ["fake", "linked"]

RESTRICTED = (
    403,
    {
        "error": {
            "status": 403,
            "message": "Player command failed: Restriction violated",
            "reason": "UNKNOWN",
        }
    },
)


class Playing:
    def __init__(self, fake):
        self.fake = fake

    def __call__(self, method, url, headers=None, body=None):
        value = self.fake(method, url, headers, body)
        path = self.fake.calls[-1][1]
        if (method, path) == ("PUT", "/me/player/play") and self.fake.active:
            return RESTRICTED
        return value


def test_play_after_take_over_is_ok_when_spotify_restricts_retry(linked, fake, monkeypatch):
    monkeypatch.setattr(spotify, "fetch", Playing(fake))
    r = linked.post("/api/spotify/play")
    assert r.status_code == 200, r.text
    assert r.json() == {"linked": True, "ok": True}
    sent = [c for c in fake.calls if c[1].startswith("/me/player")]
    assert json.loads(sent[2][4]) == {"device_ids": ["hb"], "play": True}


def test_playlist_play_still_reports_retry_failure(linked, fake, monkeypatch):
    monkeypatch.setattr(spotify, "fetch", Playing(fake))
    fake.routes[("GET", "/me/player/devices")] = (
        200,
        {"devices": [{"id": "hb", "name": "Home Board", "is_active": True}]},
    )
    r = linked.post("/api/spotify/play-playlist", json={"id": "p1"})
    assert r.status_code == 409
    assert r.json() == {"detail": "Player command failed: Restriction violated"}


def test_next_after_take_over_still_reports_retry_failure(linked, fake, monkeypatch):
    monkeypatch.setattr(spotify, "fetch", Playing(fake))
    fake.routes[("POST", "/me/player/next")] = RESTRICTED
    r = linked.post("/api/spotify/next")
    assert r.status_code == 409
