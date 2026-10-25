import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "soloist-event.sh"
GIT_BASH = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git" / "bin" / "bash.exe"
BASH = str(GIT_BASH) if GIT_BASH.exists() else shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not installed")


@pytest.fixture
def helper():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            received.append((self.path, json.loads(body)))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", received
    server.shutdown()
    server.server_close()


def line(kind, **fields):
    return "1792909930795 " + json.dumps({"type": kind, **fields}, separators=(",", ":"))


def run(lines, url, api=None):
    env = dict(os.environ, HOMEBOARD_HELPER_URL=url, HOMEBOARD_API_URL=api or url)
    stdin = "".join(x + "\n" for x in lines)
    return subprocess.run(
        [BASH, SCRIPT.as_posix()], input=stdin, text=True, env=env, timeout=10, check=False
    )


def helper_posts(received):
    return [r for r in received if r[0] == "/playing"]


def api_posts(received):
    return [r[1] for r in received if r[0] == "/api/spotify/event"]


@pytest.mark.parametrize(
    "kind, status, playing",
    [
        ("playback_changed", "playing", True),
        ("playback_changed", "paused", False),
        ("playback_changed", "idle", False),
        ("playback_state", "playing", True),
        ("playback_state", "paused", False),
    ],
)
def test_event_posts_playing_state(helper, kind, status, playing):
    url, received = helper
    assert run([line(kind, status=status)], url).returncode == 0
    assert helper_posts(received) == [("/playing", {"playing": playing})]


def test_play_then_pause_posts_true_then_false(helper):
    url, received = helper
    lines = [
        line("auth_state", logged_in=True, is_active=True, device_name="Home Board"),
        line("playback_changed", status="buffering"),
        line("playback_changed", status="playing"),
        line("position_sync", position_ms=1000),
        line("playback_changed", status="playing"),
        line("playback_changed", status="paused"),
    ]
    assert run(lines, url).returncode == 0
    assert helper_posts(received) == [
        ("/playing", {"playing": True}),
        ("/playing", {"playing": False}),
    ]


def test_other_events_post_nothing(helper):
    url, received = helper
    lines = [
        "connected to ws://127.0.0.1:40527",
        line("auth_state", logged_in=False, is_active=False, device_name="Home Board"),
        line("volume_changed", volume=50),
        line("playback_changed", status="buffering"),
        "End of file",
    ]
    assert run(lines, url).returncode == 0
    assert helper_posts(received) == []


def test_skip_posts_to_api_and_helper(helper):
    url, received = helper
    lines = [
        line("auth_state", logged_in=True, is_active=True, device_name="Home Board"),
        line("track_changed", item={"uri": "spotify:track:x"}),
        line("playback_changed", status="buffering"),
        line("queue_changed", previous=[]),
        line("playback_state", status="buffering"),
        line("position_sync", position={"position_ms": 0}),
        line("playback_changed", status="playing"),
        line("playback_state", status="playing"),
        line("volume_changed", volume=69),
    ]
    assert run(lines, url).returncode == 0
    assert api_posts(received) == [
        {"type": "track_changed"},
        {"type": "playback_changed", "status": "buffering"},
        {"type": "playback_changed", "status": "playing"},
        {"type": "volume_changed"},
    ]
    assert helper_posts(received) == [("/playing", {"playing": True})]


def test_pause_posts_status_to_api(helper):
    url, received = helper
    assert run([line("playback_changed", status="paused")], url).returncode == 0
    assert api_posts(received) == [{"type": "playback_changed", "status": "paused"}]
    assert helper_posts(received) == [("/playing", {"playing": False})]


def test_api_down_still_posts_helper(helper):
    url, received = helper
    lines = [line("playback_changed", status="playing")]
    assert run(lines, url, api="http://127.0.0.1:9").returncode == 0
    assert received == [("/playing", {"playing": True})]


def test_helper_down_does_not_fail():
    assert run([line("playback_changed", status="playing")], "http://127.0.0.1:9").returncode == 0
