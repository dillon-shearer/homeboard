import datetime
import json
import os
import subprocess
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import routes_sound, spotify
from app.main import create_app
from tests.chrome import needs_chrome, needs_node
from tests.test_control_panel import DOWN, OPEN, _load, _swipe
from tests.test_helper import helperd
from tests.test_now_playing_cover import _seed
from tests.test_soloist_event import BASH
from tests.test_spotify import PLAYER, FakeSpotify
from tests.test_spotify_volume_buttons import VolumeSpotify
from tests.test_tv_screen import _until
from tests.test_tv_spotify import SCREEN_JS, _playing

UPDATE = Path(__file__).resolve().parents[1] / "deploy" / "soloist-update.sh"
LIMITED = (429, {"error": {"status": 429, "message": "API rate limit exceeded"}})


@pytest.fixture
def linked(tmp_path, monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    fake = FakeSpotify()
    monkeypatch.setattr(spotify, "fetch", fake)
    spotify.forget()
    with TestClient(create_app(tmp_path)) as client:
        spotify.token_path(tmp_path).write_text(json.dumps({"refresh_token": "ref"}))
        yield client, fake
    spotify.forget()


def test_play_on_another_device_controls_it_without_transfer(linked):
    client, fake = linked
    fake.routes[("GET", "/me/player")] = (200, {**PLAYER, "device": {"name": "Phone"}})
    assert client.post("/api/spotify/play").status_code == 200
    assert fake.count("PUT", "/me/player/play") == 1
    assert fake.count("PUT", "/me/player") == 0


def test_play_here_without_home_board_is_409(linked):
    client, fake = linked
    fake.routes[("GET", "/me/player/devices")] = (200, {"devices": [{"id": "p", "name": "Phone"}]})
    r = client.post("/api/spotify/play-here")
    assert r.status_code == 409 and r.json() == {"detail": "Home Board speaker not found"}
    assert fake.count("PUT", "/me/player") == 0


def test_rate_limit_cools_down_reads_and_commands(linked):
    client, fake = linked
    assert client.get("/api/spotify/now").json()["stale"] is False
    fake.routes[("GET", "/me/player")] = LIMITED
    now = client.get("/api/spotify/now?fresh=1").json()
    assert now["stale"] is True and now["reason"] == "rate limited"
    assert now["track"] == "Song One" and now["retry_s"] > 0 and now["updated"]
    reads = fake.count("GET", "/me/player")
    assert client.get("/api/spotify/now?fresh=1").json()["reason"] == "rate limited"
    r = client.post("/api/spotify/next")
    assert r.status_code == 429 and "rate limited" in r.json()["detail"]
    assert fake.count("GET", "/me/player") == reads
    assert fake.count("POST", "/me/player/next") == 0


def test_network_failure_is_marked_unreachable(linked):
    client, fake = linked
    client.get("/api/spotify/now")
    fake.routes[("GET", "/me/player")] = OSError("offline")
    now = client.get("/api/spotify/now?fresh=1").json()
    assert now["stale"] is True and now["reason"] == "unreachable" and now["retry_s"] is None


def test_settle_starts_after_the_command_finishes(linked, monkeypatch):
    client, _ = linked
    order = []
    monkeypatch.setattr(spotify, "control", lambda *a: order.append("control") or {"ok": True})
    monkeypatch.setattr(spotify, "settle", lambda d, publish, last=None: order.append("settle"))
    client.post("/api/spotify/next")
    assert order == ["control", "settle"]


def test_settle_ends_quietly_when_unlinked(tmp_path, monkeypatch):
    monkeypatch.setattr(spotify, "SETTLE_S", (0.001,))
    done = threading.Event()

    def unlinked(*args):
        done.set()
        raise spotify.NotLinked("not linked")

    monkeypatch.setattr(spotify, "now_playing", unlinked)
    errors = []
    monkeypatch.setattr(threading, "excepthook", errors.append)
    before = set(threading.enumerate())
    spotify.settle(tmp_path, lambda name: None)
    assert done.wait(2)
    for thread in set(threading.enumerate()) - before:
        thread.join(2)
    assert errors == []


def test_helper_reconciles_after_restart_and_reports_failed_connect(monkeypatch):
    actions = []
    monkeypatch.setattr(helperd, "idle_setting", lambda: 1)
    helper = helperd.Helper("AA:BB:CC:DD:EE:FF")
    monkeypatch.setattr(helper, "connect", lambda: actions.append("connect") or False)
    helper.set_playing(False)
    monkeypatch.setattr(helperd, "soloist_playing", lambda: None)
    helper.reconcile()
    assert actions == [] and helper.state.deadline is not None
    monkeypatch.setattr(helperd, "soloist_playing", lambda: True)
    helper.reconcile()
    helper.reconcile()
    assert actions == ["connect"]
    assert helper.state.playing and helper.state.deadline is None
    assert helper.connect_ok is False


def _posix(path):
    text = Path(path).as_posix()
    return "/" + text[0].lower() + text[2:] if text[1:2] == ":" else text


def _stub(folder, name, body):
    path = folder / name
    path.write_text("#!/usr/bin/env bash\n" + body, newline="\n")
    path.chmod(0o755)


def _update(tmp_path, built, same):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    soloist = tmp_path / "soloist"
    (soloist / "lib").mkdir(parents=True)
    _stub(soloist / "lib", "ld-linux-aarch64.so.1", "exit 0\n")
    version = f"echo 'soloist 1.0 ({built}) arm64'\n"
    if same:
        _stub(soloist, "soloist", version)
    log = tmp_path / "systemctl.log"
    _stub(
        stubs,
        "curl",
        'while [ $# -gt 0 ]; do [ "$1" = -o ] && printf x > "$2"; shift; done\n',
    )
    _stub(
        stubs,
        "tar",
        'while [ $# -gt 0 ]; do [ "$1" = -C ] && D="$2"; shift; done\n'
        f"printf '%s\\n' '#!/usr/bin/env bash' \"{version.strip()}\" > \"$D/soloist\"\n"
        'chmod +x "$D/soloist"\n',
    )
    _stub(stubs, "patchelf", "exit 0\n")
    _stub(
        stubs,
        "systemctl",
        f'echo "$*" >> "{_posix(log)}"\n'
        'case "$1" in is-enabled) exit 0 ;; is-active) exit 3 ;; esac\n',
    )
    result = subprocess.run(
        [BASH, "-c", 'PATH="$1:$PATH" exec bash "$2"', "update", _posix(stubs), _posix(UPDATE)],
        env={**os.environ, "SOLOIST_DIR": _posix(soloist)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return log.read_text().splitlines() if log.exists() else []


@pytest.mark.skipif(BASH is None, reason="bash not installed")
@pytest.mark.parametrize("same", [True, False])
def test_updater_starts_a_stopped_soloist_with_a_current_build(tmp_path, same):
    built = datetime.date.today().strftime("%Y%m%d")
    calls = _update(tmp_path, built, same)
    assert "restart homeboard-soloist.service" in calls


@pytest.mark.skipif(BASH is None, reason="bash not installed")
def test_updater_leaves_an_expired_build_stopped(tmp_path):
    built = (datetime.date.today() - datetime.timedelta(days=120)).strftime("%Y%m%d")
    assert "restart homeboard-soloist.service" not in _update(tmp_path, built, True)


@needs_node
def test_tv_status_text_and_backoff():
    script = (
        f"const T = require({json.dumps(str(SCREEN_JS))});"
        "const now = {linked: true, track: 'A', playing: true, progress_ms: 1000, duration_ms: 9000};"
        "console.log(JSON.stringify({"
        " stale: [T.staleText(now), T.staleText({...now, stale: true}),"
        "   T.staleText({...now, stale: true, reason: 'rate limited'}),"
        "   T.staleText({linked: true, stale: true})],"
        " device: [T.deviceText({device: 'Phone'}), T.deviceText({device: 'Home Board', on_board: true}),"
        "   T.deviceText({})],"
        " progress: T.progressAt({...now, stale: true}, 5000),"
        " backoff: [T.backoffMs(now, 0), T.backoffMs(now, 1), T.backoffMs(now, 2), T.backoffMs(now, 9)]}))"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    result = json.loads(out.stdout)
    assert result["stale"] == [
        None,
        "Could not reach Spotify. Showing the last known song.",
        "Could not reach Spotify (too many requests). Showing the last known song.",
        "Could not reach Spotify.",
    ]
    assert result["device"] == ["Playing on Phone", "Playing on Home Board", None]
    assert result["progress"] == 1000
    assert result["backoff"] == [9000, 15000, 30000, 120000]


@pytest.fixture
def panel_player(monkeypatch):
    player = VolumeSpotify()
    player.now = _playing(device="Phone", on_board=False)
    monkeypatch.setattr(spotify, "now_playing", player.now_playing)
    monkeypatch.setattr(spotify, "control", player.control)
    monkeypatch.setattr(spotify, "play_here", player.play_here)
    monkeypatch.setattr(routes_sound, "helper", player.helper)
    return player


@needs_chrome
def test_control_panel_plays_here_and_shows_failed_commands(chrome, server, panel_player):
    _seed(server)
    _load(chrome, server)
    _swipe(chrome, 500, DOWN)
    chrome.wait(OPEN)
    chrome.wait("!document.getElementById('cp-now-here').hidden")
    assert chrome.eval("document.getElementById('cp-now-info').textContent") == "Playing on Phone"
    chrome.eval("document.getElementById('cp-now-here').click()")
    assert _until(lambda: panel_player.sent("POST", "/api/spotify/play-here"), bool)
    panel_player.fail["next"] = "Player command failed: Restriction violated"
    chrome.eval("document.getElementById('cp-now-next').click()")
    chrome.wait(
        "document.getElementById('cp-now-info').textContent.includes('Restriction violated')"
    )
    assert chrome.eval("window.__errors") == []
