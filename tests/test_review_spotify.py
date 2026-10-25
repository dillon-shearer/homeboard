import asyncio
import copy
import io
import json
import os
import subprocess
import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import events, routes_sound, spotify
from app.main import create_app
from tests.chrome import api, needs_chrome, needs_node
from tests.test_control_panel import DOWN, OPEN, _load, _swipe
from tests.test_helper import helperd
from tests.test_now_playing_cover import _seed
from tests.test_soloist_event import BASH, SCRIPT, line
from tests.test_spotify import NO_DEVICE, PLAYER, FakeSpotify
from tests.test_spotify_volume_buttons import VolumeSpotify
from tests.test_tv_screen import _show_screen, _sp_setup
from tests.test_tv_spotify import _playing, _png

HTTP_FETCH = spotify.fetch


@needs_node
def test_settings_events_publish_loaded_and_saved_values():
    from tests import test_clock_caption_form as form

    result = form._node(
        """
        await flush();
        const cell = radio("caption_pos", "bottom-right");
        cell.checked = true;
        cell.fire("change");
        await flush();
        console.log(JSON.stringify({ events: settingsEvents, puts: puts }));
        """,
        form.FORM_SETUP
        + """
        var settingsEvents = [];
        document.addEventListener("homeboard:settings", function (event) {
          settingsEvents.push({ caption_pos: event.detail.caption_pos });
        });
        """,
    )
    assert result == {
        "events": [
            {"caption_pos": "bottom-left"},
            {"caption_pos": "bottom-right"},
            {"caption_pos": "bottom-right"},
        ],
        "puts": [{"caption_pos": "bottom-right"}],
    }


@needs_chrome
def test_photo_cover_resyncs_on_hello_after_startup_gap(chrome, server, surface_player):
    _seed(server)
    hook = chrome.send(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": "const reviewFetchBeforeStart = window.fetch;"
            "window.fetch = function(url, opts) {"
            "if (url === '/api/photos' && !window.reviewPhotosHeld) {"
            "window.reviewPhotosHeld = true;"
            "return new Promise(resolve => { window.reviewReleasePhotos = () => "
            "resolve(reviewFetchBeforeStart.call(window, url, opts)); }); }"
            "return reviewFetchBeforeStart.apply(this, arguments); };"
        },
    )
    try:
        chrome.send("Page.navigate", {"url": server + "/"})
        chrome.wait("window.reviewReleasePhotos && !document.getElementById('cover').hidden")
        assert chrome.eval("__test.heard('hello')") == 0
        surface_player.now = _playing(track="Missed change", cover=_png((40, 120, 80), 200))
        events.publish("spotify_changed")
        chrome.eval("reviewReleasePhotos()")
        chrome.wait("__test.heard('hello') > 0")
        chrome.run("__test.settle()")
        chrome.wait("window.__inflight === 0")
        assert (
            chrome.eval("document.getElementById('cover').getAttribute('src')")
            == (surface_player.now["cover"])
        )
    finally:
        chrome.send("Page.removeScriptToEvaluateOnNewDocument", {"identifier": hook["identifier"]})


@pytest.fixture
def review_api(tmp_path, monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "review-id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "review-secret")
    fake = FakeSpotify()
    monkeypatch.setattr(spotify, "fetch", fake)
    spotify.forget()
    with TestClient(create_app(tmp_path)) as client:
        spotify.token_path(tmp_path).write_text(json.dumps({"refresh_token": "ref"}))
        yield client, fake, tmp_path
    spotify.forget()


def test_restart_loads_saved_token_and_changed_player(review_api):
    client, fake, data_dir = review_api
    assert client.get("/api/spotify/now").json()["device"] == "Home Board"
    spotify.forget()
    fake.routes[("GET", "/me/player")] = (
        200,
        {**PLAYER, "device": {"name": "Phone", "volume_percent": 25}},
    )
    with TestClient(create_app(data_dir)) as restarted:
        now = restarted.get("/api/spotify/now").json()
    assert now["device"] == "Phone" and not now["on_board"]
    assert fake.count("POST", spotify.TOKEN_URL) == 2
    assert fake.count("PUT", "/me/player") == 0


def test_401_refresh_is_bounded_and_recovers(review_api, monkeypatch):
    client, fake, _ = review_api
    client.get("/api/spotify/now")
    spotify.clear_now()

    def expired_once(method, url, headers=None, body=None):
        response = fake(method, url, headers, body)
        if url.endswith("/me/player") and fake.count("GET", "/me/player") == 2:
            return 401, {"error": {"message": "expired"}}
        return response

    monkeypatch.setattr(spotify, "fetch", expired_once)
    assert client.get("/api/spotify/now").json()["stale"] is False
    assert fake.count("POST", spotify.TOKEN_URL) == 2
    assert fake.count("GET", "/me/player") == 3
    fake.routes[("GET", "/me/player")] = (401, {"error": {"message": "denied"}})
    assert client.get("/api/spotify/now?fresh=1").json()["stale"] is True
    assert fake.count("GET", "/me/player") == 5


def test_simultaneous_cold_polls_share_token_and_player_fetch(review_api):
    _, fake, data_dir = review_api
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: spotify.now_playing(data_dir), range(6)))
    assert all(n["track"] == "Song One" for n in results)
    assert fake.count("POST", spotify.TOKEN_URL) == 1
    assert fake.count("GET", "/me/player") == 1


def test_takeover_and_return_are_observed_without_transfer(review_api):
    client, fake, _ = review_api
    for device in ("Phone", "Car", "Home Board"):
        fake.routes[("GET", "/me/player")] = (200, {**PLAYER, "device": {"name": device}})
        now = client.get("/api/spotify/now?fresh=1").json()
        assert now["device"] == device
        assert now["on_board"] is (device == "Home Board")
    assert all(method in ("GET", "POST") for method, _ in fake.paths())
    assert fake.count("PUT", "/me/player") == 0


def test_play_here_transfers_from_active_phone(review_api):
    client, fake, _ = review_api
    fake.routes[("GET", "/me/player")] = (200, {**PLAYER, "device": {"name": "Phone"}})
    fake.routes[("GET", "/me/player/devices")] = (
        200,
        {"devices": [{"id": "hb", "name": "Home Board", "is_active": False}]},
    )
    assert client.get("/api/spotify/now").json()["on_board"] is False
    assert client.post("/api/spotify/here").status_code == 200
    transfers = [json.loads(c[4]) for c in fake.calls if c[:2] == ("PUT", "/me/player")]
    assert any(c["device_ids"] == ["hb"] for c in transfers)


def test_restart_play_retry_reports_upstream_500(review_api, monkeypatch):
    client, fake, _ = review_api
    fake.routes[("GET", "/me/player/devices")] = (
        200,
        {"devices": [{"id": "hb", "name": "Home Board"}]},
    )

    def failed_retry(method, url, headers=None, body=None):
        response = fake(method, url, headers, body)
        if "/me/player/play" in url:
            if fake.count("PUT", "/me/player/play") == 1:
                return NO_DEVICE
            return 500, {"error": {"message": "backend unavailable"}}
        return response

    monkeypatch.setattr(spotify, "fetch", failed_retry)
    assert client.post("/api/spotify/play").status_code == 502


def test_429_retry_after_prevents_immediate_second_request(review_api, monkeypatch):
    client, _, _ = review_api
    spotify._keep_token({"access_token": "acc", "expires_in": 3600})
    calls = []
    monkeypatch.setattr(spotify, "fetch", HTTP_FETCH)

    def limited(req, **kwargs):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(
            req.full_url, 429, "limited", {"Retry-After": "60"}, io.BytesIO(b"{}")
        )

    monkeypatch.setattr(spotify.urllib.request, "urlopen", limited)
    assert client.get("/api/spotify/now?fresh=1").json()["stale"]
    assert client.get("/api/spotify/now?fresh=1").json()["stale"]
    assert len(calls) == 1


def test_pause_idle_disconnect_then_resume_connects(monkeypatch):
    clock = [100.0]
    actions = []
    monkeypatch.setattr(helperd.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(helperd, "idle_setting", lambda: 1)
    helper = helperd.Helper("AA:BB:CC:DD:EE:FF")
    monkeypatch.setattr(helper, "connect", lambda: actions.append("connect"))
    monkeypatch.setattr(helper, "disconnect", lambda: actions.append("disconnect"))
    helper.set_playing(True)
    helper.set_playing(False)
    clock[0] += 61
    helper.tick()
    helper.set_playing(True)
    clock[0] += 61
    helper.tick()
    assert actions == ["connect", "disconnect", "connect"]
    assert helper.state.playing and helper.state.deadline is None


@pytest.mark.skipif(BASH is None, reason="bash not installed")
def test_bridge_retries_same_state_after_helper_503():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            received.append(self.path)
            self.send_response(503 if len(received) == 1 else 200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        result = subprocess.run(
            [BASH, SCRIPT.as_posix()],
            input=(line("playback_state", status="playing") + "\n") * 2,
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "HOMEBOARD_HELPER_URL": url, "HOMEBOARD_API_URL": url},
            timeout=10,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
    assert result.returncode == 0
    assert received == ["/playing", "/playing"]


def test_worker_thread_event_reaches_every_subscriber():
    async def check():
        async with events.subscribe() as first, events.subscribe() as second:
            async with events.subscribe() as third:
                await asyncio.to_thread(events.publish, "spotify_changed")
                values = await asyncio.wait_for(
                    asyncio.gather(first.get(), second.get(), third.get()), 2
                )
                assert values == ["spotify_changed"] * 3
            await asyncio.to_thread(events.publish, "settings_changed")
            assert await asyncio.wait_for(first.get(), 2) == "settings_changed"
            assert await asyncio.wait_for(second.get(), 2) == "settings_changed"
            assert third.empty()

    asyncio.run(check())


def test_settle_notifies_same_title_and_cover_with_changed_artist(review_api, monkeypatch):
    _, fake, data_dir = review_api
    spotify.now_playing(data_dir)
    new = copy.deepcopy(PLAYER)
    new["item"]["artists"] = [{"name": "Different Band"}]
    new["progress_ms"] = 0
    fake.routes[("GET", "/me/player")] = (200, new)
    monkeypatch.setattr(spotify, "SETTLE_S", (0.001,))
    done = threading.Event()
    sent = []
    original = spotify.now_playing

    def read(*args):
        try:
            return original(*args)
        finally:
            done.set()

    monkeypatch.setattr(spotify, "now_playing", read)
    before = set(threading.enumerate())
    spotify.settle(data_dir, sent.append)
    assert done.wait(2)
    for thread in set(threading.enumerate()) - before:
        thread.join(2)
    assert sent == ["spotify_changed"]


@pytest.fixture
def surface_player(monkeypatch):
    player = VolumeSpotify()
    player.now = _playing(volume=80)
    monkeypatch.setattr(spotify, "now_playing", player.now_playing)
    monkeypatch.setattr(spotify, "control", player.control)
    monkeypatch.setattr(routes_sound, "helper", player.helper)
    return player


def _open_surface(chrome, server, surface):
    if surface == "phone":
        chrome.size(390, 844)
        chrome.send("Page.navigate", {"url": server + "/admin/sound"})
        selector = ".player-track"
    elif surface == "tv":
        ids = _sp_setup(server, [(2, 2)])
        _show_screen(server, ids[(2, 2)])
        chrome.send("Page.navigate", {"url": server + "/tv"})
        selector = ".sp-track"
    else:
        _seed(server)
        _load(chrome, server)
        if surface == "panel":
            _swipe(chrome, 500, DOWN)
            chrome.wait(OPEN)
            selector = "#cp-now-line"
        else:
            selector = "#cover"
    chrome.wait("window.__test && __test.heard('hello') > 0")
    if surface == "cover":
        chrome.wait("!document.getElementById('cover').hidden")
    else:
        chrome.wait(f"document.querySelector({json.dumps(selector)})?.textContent.includes('Sun')")
    chrome.wait("window.__inflight === 0")
    return selector


def _publish(chrome):
    before = chrome.eval("__test.heard('spotify_changed')")
    events.publish("spotify_changed")
    chrome.wait(f"__test.heard('spotify_changed') > {before}")
    chrome.run("__test.settle()")
    chrome.wait("window.__inflight === 0")


@needs_chrome
@pytest.mark.parametrize("surface", ["phone", "tv", "panel", "cover"])
def test_subscribed_surfaces_follow_changed_track(chrome, server, surface_player, surface):
    selector = _open_surface(chrome, server, surface)
    chrome.eval("window.reviewMarker = 134")
    surface_player.now = _playing(track="Review song", cover=_png((10, 90, 170), 200))
    _publish(chrome)
    if surface == "cover":
        assert (
            chrome.eval("document.getElementById('cover').getAttribute('src')")
            == (surface_player.now["cover"])
        )
    else:
        chrome.wait(
            f"document.querySelector({json.dumps(selector)}).textContent.includes('Review')"
        )
    assert chrome.eval("window.reviewMarker") == 134
    assert chrome.eval("window.__errors") == []


@needs_chrome
@pytest.mark.parametrize("surface", ["phone", "tv", "panel"])
def test_quick_volume_taps_wait_for_previous_write(chrome, server, surface_player, surface):
    _open_surface(chrome, server, surface)
    selector = "#cp-now-down" if surface == "panel" else '[data-sp="vol-down"]'
    chrome.eval(
        "window.reviewPuts = []; window.reviewRelease = [];"
        "window.reviewFetch = window.fetch;"
        "window.fetch = function(url, opts) {"
        "if (url === '/api/spotify/volume' && opts?.method === 'PUT') {"
        "reviewPuts.push(JSON.parse(opts.body).percent);"
        "return new Promise(resolve => reviewRelease.push(() => "
        "resolve(new Response('{}', {status: 200})))); }"
        "return reviewFetch.apply(this, arguments); };"
        f"document.querySelector({json.dumps(selector)}).click();"
        f"document.querySelector({json.dumps(selector)}).click();"
    )
    chrome.wait("reviewPuts.length > 0")
    chrome.run("Promise.resolve().then(() => Promise.resolve())")
    pending = chrome.eval("reviewPuts.slice()")
    chrome.eval("reviewRelease.splice(0).forEach(fn => fn())")
    assert pending == [75]
    chrome.wait("reviewPuts.length === 2")
    assert chrome.eval("reviewPuts") == [75, 70]
    chrome.eval("reviewRelease.splice(0).forEach(fn => fn())")


@needs_chrome
@pytest.mark.parametrize("surface", ["phone", "tv", "panel"])
@pytest.mark.xfail(strict=True, reason="Now-playing surfaces omit the active device name")
def test_other_device_is_named_on_every_control_surface(chrome, server, surface_player, surface):
    surface_player.now = _playing(device="Review Phone", on_board=False)
    _open_surface(chrome, server, surface)
    selector = "#control-panel" if surface == "panel" else "body"
    assert "Review Phone" in chrome.eval(
        f"document.querySelector({json.dumps(selector)}).innerText"
    )


@needs_chrome
@pytest.mark.parametrize("surface", ["phone", "tv", "panel"])
@pytest.mark.xfail(
    strict=True, reason="Clients render stale playback as current without a status message"
)
def test_stale_playback_is_explained(chrome, server, surface_player, surface):
    _open_surface(chrome, server, surface)
    surface_player.now = _playing(stale=True)
    _publish(chrome)
    selector = "#control-panel" if surface == "panel" else "body"
    text = chrome.eval(f"document.querySelector({json.dumps(selector)}).innerText").lower()
    assert any(word in text for word in ("stale", "offline", "unreachable", "could not reach"))


@needs_chrome
@pytest.mark.xfail(strict=True, reason="TV volume changes recreate the unchanged cover image")
def test_tv_volume_refresh_keeps_cover_element(chrome, server, surface_player):
    _open_surface(chrome, server, "tv")
    chrome.eval("window.reviewImage = document.querySelector('.sp-cover img')")
    surface_player.now = {**surface_player.now, "volume": 65}
    _publish(chrome)
    chrome.wait("document.querySelector('.sp-vol').textContent === '65%'")
    assert chrome.eval("reviewImage === document.querySelector('.sp-cover img')")


@needs_chrome
def test_cover_setting_and_return_from_other_device(chrome, server, surface_player):
    _open_surface(chrome, server, "cover")
    chrome.eval("window.reviewImage = document.getElementById('cover')")
    surface_player.now = _playing(on_board=False, device="Phone")
    _publish(chrome)
    assert chrome.eval("document.getElementById('cover').hidden")
    surface_player.now = _playing()
    _publish(chrome)
    assert not chrome.eval("document.getElementById('cover').hidden")
    api.put(server + "/api/settings", json={"cover_show": False}).raise_for_status()
    chrome.wait("document.getElementById('cover').hidden")
    _publish(chrome)
    assert chrome.eval("document.getElementById('cover').hidden")
    assert chrome.eval("reviewImage === document.getElementById('cover')")


@needs_chrome
def test_late_cover_response_does_not_restore_old_art(chrome, server, surface_player):
    _open_surface(chrome, server, "cover")
    old = json.dumps(surface_player.now)
    chrome.eval(
        "window.reviewFetch = window.fetch; window.reviewHeld = false;"
        "window.fetch = function(url, opts) {"
        "if (url === '/api/spotify/now' && !reviewHeld) { reviewHeld = true;"
        "return new Promise(resolve => { window.reviewRelease = () => "
        f"resolve(new Response(JSON.stringify({old}), {{status: 200}})); }}); }}"
        "return reviewFetch.apply(this, arguments); };"
    )
    before = chrome.eval("__test.heard('spotify_changed')")
    events.publish("spotify_changed")
    chrome.wait(f"__test.heard('spotify_changed') > {before} && reviewHeld")
    surface_player.now = _playing(track="New song", cover=_png((15, 160, 80), 200))
    events.publish("spotify_changed")
    src = json.dumps(surface_player.now["cover"])
    chrome.wait(f"document.getElementById('cover').getAttribute('src') === {src}")
    chrome.eval("reviewRelease()")
    chrome.run("__test.settle()")
    assert (
        chrome.eval("document.getElementById('cover').getAttribute('src')")
        == (surface_player.now["cover"])
    )


@needs_chrome
def test_settings_speaker_recovers_without_visibility_change(chrome, server, monkeypatch):
    from fastapi.responses import JSONResponse

    healthy = [False]
    monkeypatch.setattr(spotify, "now_playing", lambda *a: {"linked": False})
    monkeypatch.setattr(
        routes_sound,
        "helper",
        lambda *a: (
            JSONResponse({"speaker": {"connected": True, "name": "Review Bar"}})
            if healthy[0]
            else JSONResponse({"detail": "helper down"}, status_code=503)
        ),
    )
    chrome.send("Page.navigate", {"url": server + "/admin/settings"})
    chrome.wait("document.getElementById('speaker-state')?.textContent === 'Helper not running'")
    chrome.wait("__test.heard('hello') > 0 && window.__inflight === 0")
    healthy[0] = True
    chrome.run("__test.tick(5000)")
    chrome.run("__test.tick(10000)")
    chrome.wait("window.__inflight === 0")
    assert chrome.eval("document.getElementById('speaker-state').textContent") == (
        "Connected to Review Bar"
    )


@pytest.mark.skipif(BASH is None, reason="bash not installed")
def test_installer_can_read_missing_initial_wireplumber_config(tmp_path):
    install = Path(__file__).resolve().parents[1] / "deploy" / "install.sh"
    assignment = next(s for s in install.read_text().splitlines() if s.startswith("WP_OLD_SUM="))
    result = subprocess.run(
        [
            BASH,
            "-c",
            'set -euo pipefail\nWP_CONF="$1"\n' + assignment + "\nprintf ready",
            "review",
            str(tmp_path / "missing"),
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ready"
