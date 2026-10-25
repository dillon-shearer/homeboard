import importlib.util
import json
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import routes_sound
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
HELPERD = ROOT / "deploy" / "helper" / "helperd.py"
spec = importlib.util.spec_from_file_location("helperd_volume", HELPERD)
helperd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helperd)

MAC = "50:FD:D5:0D:1C:1E"
DUMP = [
    {"id": 67, "info": {"props": {"media.class": "Audio/Sink", "node.name": "alsa_output.x"}}},
    {"id": 40, "info": {"props": {"media.class": "Audio/Device", "api.bluez5.address": MAC}}},
    {"id": 72, "info": {"props": {"media.class": "Audio/Sink", "api.bluez5.address": MAC}}},
    {"id": 3, "info": None},
]


class FakeWpctl:
    def __init__(self, dump=DUMP, level=0.4):
        self.dump = dump
        self.level = {72: level}
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        if cmd == ["pw-dump"]:
            return json.dumps(self.dump)
        if cmd[:2] == ["wpctl", "get-volume"]:
            v = self.level.get(int(cmd[2]))
            return None if v is None else f"Volume: {v:.2f}\n"
        if cmd[:2] == ["wpctl", "set-volume"]:
            self.level[int(cmd[2])] = float(cmd[3])
            return ""
        raise AssertionError(cmd)


def test_speaker_sink_is_the_bluez_sink_for_the_mac(monkeypatch):
    monkeypatch.setattr(helperd, "run_out", FakeWpctl())
    assert helperd.speaker_sink(MAC) == 72
    assert helperd.speaker_sink("AA:BB:CC:DD:EE:FF") is None
    assert helperd.speaker_sink("") is None
    monkeypatch.setattr(helperd, "run_out", lambda cmd: None)
    assert helperd.speaker_sink(MAC) is None
    monkeypatch.setattr(helperd, "run_out", lambda cmd: "not json")
    assert helperd.speaker_sink(MAC) is None


def test_get_and_set_volume_through_wpctl(monkeypatch):
    fake = FakeWpctl()
    monkeypatch.setattr(helperd, "run_out", fake)
    assert helperd.get_volume(72) == 40
    assert helperd.set_volume(72, 70) == 70
    assert ["wpctl", "set-volume", "72", "0.70"] in fake.calls
    monkeypatch.setattr(helperd, "run_out", lambda cmd: "Volume: 0.55 [MUTED]\n")
    assert helperd.get_volume(72) == 55


def test_set_volume_without_a_level_uses_the_setting_and_waits_for_the_sink(monkeypatch):
    fake = FakeWpctl(dump=[])
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        if len(sleeps) == 2:
            fake.dump = DUMP

    monkeypatch.setattr(helperd, "run_out", fake)
    monkeypatch.setattr(helperd, "volume_setting", lambda: 65)
    monkeypatch.setattr(helperd.time, "sleep", sleep)
    assert helperd.Helper(MAC).set_volume() == {"volume": 65}
    assert len(sleeps) == 2


def test_set_volume_with_a_level_does_not_wait(monkeypatch):
    monkeypatch.setattr(helperd, "run_out", FakeWpctl(dump=[]))
    monkeypatch.setattr(helperd.time, "sleep", lambda s: pytest.fail("waited"))
    assert helperd.Helper(MAC).set_volume(50) is None


def test_volume_setting_falls_back_to_100(monkeypatch):
    for v, want in ((40, 40), (0, 0), (100, 100), (101, 100), (True, 100), (None, 100), ("x", 100)):
        monkeypatch.setattr(helperd, "read_setting", lambda key, v=v: v)
        assert helperd.volume_setting() == want


@pytest.fixture
def helper_server(monkeypatch):
    fake = FakeWpctl()
    monkeypatch.setattr(helperd, "run_out", fake)
    monkeypatch.setattr(helperd, "volume_setting", lambda: 70)
    monkeypatch.setattr(helperd.time, "sleep", lambda s: None)
    helper = helperd.Helper(MAC)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), helperd.make_handler(helper))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", helper, fake
    srv.shutdown()
    srv.server_close()


def call(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_helper_volume_endpoints(helper_server):
    url, helper, fake = helper_server
    assert call(url + "/volume") == (200, {"volume": 40})
    assert call(url + "/volume", "POST", {"volume": 25}) == (200, {"volume": 25})
    assert call(url + "/volume", "POST", {}) == (200, {"volume": 70})
    assert call(url + "/volume") == (200, {"volume": 70})
    for bad in (101, -1, 5.5, "50", True):
        assert call(url + "/volume", "POST", {"volume": bad})[0] == 400
    fake.dump = []
    assert call(url + "/volume") == (200, {"volume": None})
    assert call(url + "/volume", "POST", {"volume": 30}) == (
        409,
        {"detail": "speaker not connected"},
    )
    helper.mac = ""
    assert call(url + "/volume", "POST", {"volume": 30}) == (409, {"detail": "no speaker paired"})


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_speaker_volume_setting_defaults_to_100(client):
    assert client.get("/api/settings").json()["speaker_volume"] == 100
    for bad in (-1, 101, 5.5, "ten"):
        assert client.put("/api/settings", json={"speaker_volume": bad}).status_code == 422
    r = client.put("/api/settings", json={"speaker_volume": 0})
    assert r.status_code == 200 and r.json()["speaker_volume"] == 0


@pytest.mark.parametrize("method", ["get", "post"])
def test_volume_route_helper_down_is_503(client, monkeypatch, method):
    monkeypatch.setattr(routes_sound, "HELPER_URL", f"http://127.0.0.1:{free_port()}")
    kw = {"json": {"volume": 50}} if method == "post" else {}
    r = getattr(client, method)("/api/sound/volume", **kw)
    assert r.status_code == 503
    assert r.json() == {"detail": "helper down"}


def test_volume_route_proxies_and_saves_the_setting(client, monkeypatch):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(("GET", self.path, None))
            self.send({"volume": 40})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(("POST", self.path, body))
            self.send({"volume": body["volume"]})

        def send(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(routes_sound, "HELPER_URL", f"http://127.0.0.1:{srv.server_address[1]}")
        assert client.get("/api/sound/volume").json() == {"volume": 40}
        r = client.post("/api/sound/volume", json={"volume": 55})
        assert r.status_code == 200 and r.json() == {"volume": 55}
        for bad in ({"volume": 101}, {"volume": "55"}, {}, {"volume": 5, "x": 1}):
            assert client.post("/api/sound/volume", json=bad).status_code == 422
    finally:
        srv.shutdown()
        srv.server_close()
    assert seen == [("GET", "/volume", None), ("POST", "/volume", {"volume": 55})]
    assert client.get("/api/settings").json()["speaker_volume"] == 55


def test_helper_service_reaches_the_pipewire_session():
    text = (ROOT / "deploy" / "homeboard-helper.service").read_text(encoding="utf-8")
    assert "XDG_RUNTIME_DIR=/run/user/1000" in text


def test_pairing_ends_by_applying_the_default_volume():
    text = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
    tail = text.split("systemctl restart homeboard-helper.service\n", 1)[1]
    block = tail.split('if [ "$PAIR_SPEAKER" -eq 1 ]; then\n', 1)[1].split("\nfi\n", 1)[0]
    assert "-d '{}'" in block and "127.0.0.1:8002/volume" in block
