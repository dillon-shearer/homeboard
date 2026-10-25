import threading
from http.server import ThreadingHTTPServer

import pytest

from tests.test_helper import call, helperd


@pytest.fixture
def helper(monkeypatch):
    monkeypatch.setattr(helperd, "idle_setting", lambda: 1)
    return helperd.Helper("AA:BB:CC:DD:EE:FF")


@pytest.fixture
def server(helper):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), helperd.make_handler(helper))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", helper
    srv.shutdown()
    srv.server_close()


def test_connect_stops_at_the_budget(monkeypatch):
    clock = [0.0]
    calls = []

    def fake(*args):
        calls.append(args[0])
        clock[0] += 6
        return "Connected: no"

    monkeypatch.setattr(helperd.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(helperd.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(helperd, "bluetoothctl", fake)
    assert helperd.Helper("AA:BB:CC:DD:EE:FF").connect() is False
    assert calls == ["info", "connect", "info"]
    assert clock[0] - helperd.CONNECT_BUDGET_S < helperd.BLUETOOTHCTL_TIMEOUT_S


def test_worst_case_connect_fits_the_api_timeout():
    from app import routes_sound

    worst = helperd.CONNECT_BUDGET_S + helperd.BLUETOOTHCTL_TIMEOUT_S
    assert worst < routes_sound.CONNECT_TIMEOUT_S
    assert helperd.PLAYING_WAIT_S < 3


def test_connect_gives_up_when_another_holds_the_lock(monkeypatch):
    monkeypatch.setattr(helperd, "CONNECT_BUDGET_S", 0.1)
    h = helperd.Helper("AA:BB:CC:DD:EE:FF")
    h.bt_lock.acquire()
    try:
        assert h.connect() is False
    finally:
        h.bt_lock.release()


def test_slow_connect_answers_202_and_reports_connecting(monkeypatch, server):
    url, helper = server
    gate = threading.Event()
    monkeypatch.setattr(helperd, "PLAYING_WAIT_S", 0.1)
    monkeypatch.setattr(helperd, "bluetoothctl", lambda *a: "Connected: no\n")
    monkeypatch.setattr(helper, "connect", lambda: gate.wait(5))
    code, body = call(url + "/playing", "POST", {"playing": True})
    assert code == 202
    assert body["playing"] is True and body["connecting"] is True
    assert body["connect_ok"] is None
    code, body = call(url + "/playing", "POST", {"playing": True})
    assert code == 202
    gate.set()
    helper.connecting.join(2)
    body = call(url + "/status")[1]
    assert body["connecting"] is False and body["connect_ok"] is True


def test_one_connect_runs_at_a_time(monkeypatch, helper):
    gate = threading.Event()
    runs = []
    monkeypatch.setattr(helperd, "PLAYING_WAIT_S", 0.05)
    monkeypatch.setattr(helper, "connect", lambda: runs.append(1) or gate.wait(5))
    assert helper.connect_async() is None
    assert helper.connect_async() is None
    gate.set()
    helper.connecting.join(2)
    assert runs == [1] and helper.connect_ok is True


def test_status_reports_the_bluetooth_sink(monkeypatch):
    monkeypatch.setattr(helperd, "bluetoothctl", lambda *a: "Connected: yes\n")
    monkeypatch.setattr(helperd, "speaker_sink", lambda mac: 57)
    assert helperd.Helper("AA:BB:CC:DD:EE:FF").status()["sink"] == 57
    monkeypatch.setattr(helperd, "bluetoothctl", lambda *a: "Connected: no\n")
    assert helperd.Helper("AA:BB:CC:DD:EE:FF").status()["sink"] is None
