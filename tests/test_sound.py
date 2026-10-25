import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app import routes_sound
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize(
    "method,path",
    [("get", "status"), ("post", "connect"), ("post", "disconnect"), ("post", "reboot")],
)
def test_helper_down_is_503(client, monkeypatch, method, path):
    monkeypatch.setattr(routes_sound, "HELPER_URL", f"http://127.0.0.1:{free_port()}")
    r = getattr(client, method)(f"/api/sound/{path}")
    assert r.status_code == 503
    assert r.json() == {"detail": "helper down"}


def test_proxies_status_and_errors(client, monkeypatch):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(("GET", self.path))
            self.send(200, {"speaker": {"mac": None, "connected": False, "name": None}})

        def do_POST(self):
            seen.append(("POST", self.path))
            self.send(409, {"detail": "no speaker paired"})

        def send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
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
        r = client.get("/api/sound/status")
        assert r.status_code == 200 and r.json()["speaker"]["connected"] is False
        r = client.post("/api/sound/connect")
        assert r.status_code == 409 and r.json() == {"detail": "no speaker paired"}
    finally:
        srv.shutdown()
        srv.server_close()
    assert seen == [("GET", "/status"), ("POST", "/connect")]


def test_speaker_idle_minutes_setting(client):
    assert client.get("/api/settings").json()["speaker_idle_minutes"] == 10
    for bad in (0, 121, 5.5, "ten"):
        assert client.put("/api/settings", json={"speaker_idle_minutes": bad}).status_code == 422
    r = client.put("/api/settings", json={"speaker_idle_minutes": 10})
    assert r.status_code == 200 and r.json()["speaker_idle_minutes"] == 10
    assert client.put("/api/settings", json={"speaker_idle_minutes": 1}).status_code == 200
    assert client.put("/api/settings", json={"speaker_idle_minutes": 120}).status_code == 200
    client.put("/api/settings", json={"speaker_idle_minutes": 10})
    assert client.get("/api/settings").json()["speaker_idle_minutes"] == 10
