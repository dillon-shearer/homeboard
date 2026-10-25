from urllib.parse import parse_qs, urlsplit

from app import spotify
from tests.chrome import needs_chrome
from tests.test_lists_page import _wait
from tests.test_sound_page import _fake, _open
from tests.test_spotify import client, creds, data_dir, fake, linked  # noqa: F401


def test_me_returns_display_name(linked, fake):  # noqa: F811
    fake.routes[("GET", "/me")] = (200, {"id": "u1", "display_name": "Dill"})
    assert linked.get("/api/spotify/me").json() == {"linked": True, "display_name": "Dill"}


def test_me_falls_back_to_user_id(linked, fake):  # noqa: F811
    fake.routes[("GET", "/me")] = (200, {"id": "u1", "display_name": None})
    assert linked.get("/api/spotify/me").json()["display_name"] == "u1"


def test_profile_is_fetched_once_per_link(linked, fake, monkeypatch):  # noqa: F811
    clock = [1000.0]
    monkeypatch.setattr(spotify, "_clock", lambda: clock[0])
    fake.routes[("GET", "/me")] = (200, {"id": "u1", "display_name": "Dill"})
    for _ in range(5):
        linked.get("/api/spotify/now")
        linked.get("/api/spotify/me")
        clock[0] += 4
    assert fake.count("GET", "/me") == 1
    assert fake.count("GET", "/me/player") == 5


def test_relink_fetches_profile_again(linked, fake, monkeypatch):  # noqa: F811
    fake.routes[("GET", "/me")] = (200, {"id": "u1", "display_name": "Dill"})
    linked.get("/api/spotify/me")
    monkeypatch.setenv("HOMEBOARD_HOST", "homeboard.example.com")
    login = linked.get("/api/spotify/login", follow_redirects=False)
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    linked.get(
        "/api/spotify/callback", params={"code": "abc", "state": state}, follow_redirects=False
    )
    fake.routes[("GET", "/me")] = (200, {"id": "u2", "display_name": "Sam"})
    assert linked.get("/api/spotify/me").json()["display_name"] == "Sam"
    assert fake.count("GET", "/me") == 2


def test_failed_profile_is_not_cached(linked, fake):  # noqa: F811
    fake.routes[("GET", "/me")] = (500, {"error": {"message": "boom"}})
    assert linked.get("/api/spotify/me").status_code == 502
    fake.routes[("GET", "/me")] = (200, {"id": "u1", "display_name": "Dill"})
    assert linked.get("/api/spotify/me").json()["display_name"] == "Dill"


def test_unlinked_me_says_not_linked(client, creds, fake):  # noqa: F811
    assert client.get("/api/spotify/me").json() == {"linked": False, "reason": "not linked"}
    assert fake.calls == []


@needs_chrome
def test_sound_page_shows_linked_as_display_name(chrome, server, monkeypatch):
    _fake(monkeypatch)
    calls = []

    def profile(data_dir):  # noqa: F811
        calls.append(data_dir)
        return {"linked": True, "display_name": "Dill"}

    monkeypatch.setattr(spotify, "profile", profile)
    _open(chrome, server)
    state = _wait(
        chrome,
        "(t => t.startsWith('Linked as') && t)(document.getElementById('spotify-state').textContent)",
    )
    errors = chrome.eval("window.__errors")
    assert state == "Linked as Dill"
    assert len(calls) == 1
    assert errors == []
