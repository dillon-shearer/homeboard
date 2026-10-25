import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

HELPERD = Path(__file__).resolve().parent.parent / "deploy" / "helper" / "helperd.py"
spec = importlib.util.spec_from_file_location("helperd", HELPERD)
helperd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helperd)


def test_playing_cancels_the_timer_and_asks_to_connect():
    s = helperd.IdleState()
    assert s.set_playing(False, 0, 10) is None
    assert s.set_playing(True, 60) == "connect"
    assert s.deadline is None
    assert s.tick(10_000) is None


def test_not_playing_starts_the_timer():
    s = helperd.IdleState()
    s.set_playing(True, 0)
    assert s.set_playing(False, 100, 10) is None
    assert s.deadline == 100 + 600
    assert s.tick(100 + 599) is None
    assert s.idle_minutes(100 + 180) == 3


def test_timer_firing_asks_to_disconnect_once():
    s = helperd.IdleState()
    s.set_playing(False, 0, 2)
    assert s.tick(119) is None
    assert s.tick(120) == "disconnect"
    assert s.tick(500) is None


def test_new_playing_event_before_the_timer_fires_cancels_it():
    s = helperd.IdleState()
    s.set_playing(False, 0, 1)
    assert s.set_playing(True, 30) == "connect"
    assert s.tick(61) is None
    assert s.idle_minutes(1000) == 0


def test_speaker_info_reads_connected_from_bluetoothctl(monkeypatch):
    out = "Device AA:BB:CC:DD:EE:FF (public)\n\tName: [AV] Samsung Soundbar\n\tConnected: yes\n"
    monkeypatch.setattr(helperd, "bluetoothctl", lambda *a: out)
    assert helperd.speaker_info("AA:BB:CC:DD:EE:FF") == {
        "mac": "AA:BB:CC:DD:EE:FF",
        "connected": True,
        "name": "[AV] Samsung Soundbar",
    }
    monkeypatch.setattr(helperd, "bluetoothctl", lambda *a: out.replace("yes", "no"))
    assert helperd.speaker_info("AA:BB:CC:DD:EE:FF")["connected"] is False
    assert helperd.speaker_info("") == {"mac": None, "connected": False, "name": None}


def test_connect_retries_until_connected(monkeypatch):
    calls = []
    connected = iter([False, False, False, True])

    def fake(*args):
        calls.append(args[0])
        if args[0] == "info":
            return "Connected: yes" if next(connected) else "Connected: no"
        return ""

    monkeypatch.setattr(helperd, "bluetoothctl", fake)
    monkeypatch.setattr(helperd.time, "sleep", lambda s: None)
    assert helperd.Helper("AA:BB:CC:DD:EE:FF").connect() is True
    assert calls == ["info", "connect", "info", "info", "connect", "info"]


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(helperd, "bluetoothctl", lambda *a: "Name: Bar\nConnected: no\n")
    monkeypatch.setattr(helperd, "idle_setting", lambda: 7)
    helper = helperd.Helper("AA:BB:CC:DD:EE:FF")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), helperd.make_handler(helper))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", helper
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


def test_status_endpoint_shape(server):
    url, _ = server
    code, body = call(url + "/status")
    assert code == 200
    assert body == {
        "speaker": {"mac": "AA:BB:CC:DD:EE:FF", "connected": False, "name": "Bar"},
        "playing": False,
        "idle_minutes": 0,
        "soloist_expires": None,
        "connect_ok": None,
        "connecting": False,
        "sink": None,
    }


def test_playing_false_uses_the_idle_setting(server):
    url, helper = server
    code, body = call(url + "/playing", "POST", {"playing": False})
    assert code == 200 and body["playing"] is False
    assert helper.state.deadline - helper.state.stopped_at == 7 * 60
    assert call(url + "/playing", "POST", {"playing": "yes"})[0] == 400


def test_no_speaker_paired_is_409(server):
    url, helper = server
    helper.mac = ""
    assert call(url + "/connect", "POST", {}) == (409, {"detail": "no speaker paired"})


def test_main_starts_the_idle_timer_so_a_boot_connection_is_let_go(monkeypatch):
    started = []

    class FakeServer:
        def __init__(self, addr, handler):
            pass

        def serve_forever(self):
            started.append(True)

    monkeypatch.setenv("SPEAKER_MAC", "aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(helperd, "idle_setting", lambda: 3)
    monkeypatch.setattr(helperd, "soloist_playing", lambda: None)
    monkeypatch.setattr(helperd, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(
        helperd.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})()
    )
    created = []
    real = helperd.Helper
    monkeypatch.setattr(helperd, "Helper", lambda mac: created.append(real(mac)) or created[-1])
    helperd.main()
    assert started == [True]
    assert created[0].mac == "AA:BB:CC:DD:EE:FF"
    assert created[0].state.deadline - created[0].state.stopped_at == 3 * 60
