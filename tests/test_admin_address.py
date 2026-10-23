import socket
import types

import pytest
import segno
from fastapi.testclient import TestClient

import app.main
import app.routes_settings
from app.main import create_app


class FakeUdp:
    def __init__(self, addr):
        self.addr = addr
        self.connected = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def connect(self, target):
        if self.addr is None:
            raise OSError("Network is unreachable")
        self.connected = target

    def getsockname(self):
        return (self.addr, 54321)


def fake_socket_module(lan_ip):
    def getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_DGRAM, 0, "", ("127.0.1.1", 0))]

    return types.SimpleNamespace(
        AF_INET=socket.AF_INET,
        SOCK_DGRAM=socket.SOCK_DGRAM,
        gethostname=lambda: "homeboard",
        getaddrinfo=getaddrinfo,
        socket=lambda *args, **kwargs: FakeUdp(lan_ip),
    )


@pytest.fixture
def pi(monkeypatch, tmp_path):
    def make(lan_ip):
        fake = fake_socket_module(lan_ip)
        monkeypatch.setattr(app.main, "socket", fake)
        monkeypatch.setattr(app.routes_settings, "socket", fake)
        return TestClient(create_app(tmp_path))

    return make


def test_empty_screen_shows_local_name_and_qr_encodes_it(pi, monkeypatch):
    encoded = []
    real_make = segno.make

    def spy(content, *args, **kwargs):
        encoded.append(content)
        return real_make(content, *args, **kwargs)

    monkeypatch.setattr(app.main.segno, "make", spy)
    with pi("192.168.1.235") as client:
        r = client.get("/")
    assert '<div class="url">http://homeboard.local/admin</div>' in r.text
    assert encoded == ["http://homeboard.local/admin"]


def test_empty_screen_shows_lan_ip_when_hosts_maps_hostname_to_loopback(pi):
    with pi("192.168.1.235") as client:
        r = client.get("/")
    assert '<div class="url ip">http://192.168.1.235/admin</div>' in r.text


def test_status_reports_lan_ip_when_hosts_maps_hostname_to_loopback(pi):
    with pi("192.168.1.235") as client:
        body = client.get("/api/status").json()
    assert body["hostname"] == "homeboard"
    assert body["ip"] == "192.168.1.235"


def test_no_network_gives_empty_ip_and_no_ip_line(pi):
    with pi(None) as client:
        assert client.get("/api/status").json()["ip"] == ""
        r = client.get("/")
    assert 'class="url ip"' not in r.text
    assert "http://homeboard.local/admin" in r.text


def test_ip_never_sends_a_packet_and_skips_loopback(monkeypatch):
    sock = FakeUdp("127.0.0.1")
    fake = fake_socket_module(None)
    fake.socket = lambda *args, **kwargs: sock
    monkeypatch.setattr(app.routes_settings, "socket", fake)
    assert app.routes_settings._ip() == ""
    assert sock.connected == ("192.0.2.1", 80)
