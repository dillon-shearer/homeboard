import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "raspotify-event.sh"
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


def run(event, url):
    env = dict(os.environ, PLAYER_EVENT=event, HOMEBOARD_HELPER_URL=url)
    return subprocess.run([BASH, SCRIPT.as_posix()], env=env, timeout=10, check=False)


@pytest.mark.parametrize(
    "event, playing",
    [
        ("playing", True),
        ("session_connected", True),
        ("paused", False),
        ("stopped", False),
        ("session_disconnected", False),
    ],
)
def test_event_posts_playing_state(helper, event, playing):
    url, received = helper
    assert run(event, url).returncode == 0
    assert received == [("/playing", {"playing": playing})]


def test_other_events_post_nothing(helper):
    url, received = helper
    assert run("volume_changed", url).returncode == 0
    assert received == []


def test_helper_down_does_not_fail():
    assert run("playing", "http://127.0.0.1:9").returncode == 0
