import json
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

from tests.test_helper import call, helperd


@pytest.fixture
def fake_ctl(tmp_path, monkeypatch):
    out = tmp_path / "now.json"
    log = tmp_path / "calls.txt"
    script = tmp_path / "soloist.py"
    script.write_text(
        "import sys, pathlib\n"
        "here = pathlib.Path(__file__).parent\n"
        "with open(here / 'calls.txt', 'a') as f:\n"
        "    print(' '.join(sys.argv[1:]), file=f)\n"
        "p = here / 'now.json'\n"
        "sys.stdout.write(p.read_text() if p.exists() else '')\n"
        "sys.exit(0 if p.exists() else 1)\n"
    )
    monkeypatch.setattr(
        helperd, "SOLOIST_NOW", [sys.executable, str(script), *helperd.SOLOIST_NOW[1:]]
    )

    def put(state):
        if state is None:
            out.unlink(missing_ok=True)
        else:
            out.write_text(json.dumps(state))

    put.calls = lambda: log.read_text().splitlines() if log.exists() else []
    return put


@pytest.fixture
def helper(monkeypatch):
    clock = [1000.0]
    connects = []
    monkeypatch.setattr(helperd.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(helperd, "idle_setting", lambda: 1)
    h = helperd.Helper("AA:BB:CC:DD:EE:FF")
    monkeypatch.setattr(h, "connect", lambda: connects.append(True) or True)
    monkeypatch.setattr(h, "disconnect", lambda: connects.append(False) or False)
    h.clock = clock
    h.connects = connects
    return h


def test_soloist_playing_reads_ctl_now(fake_ctl):
    fake_ctl({"type": "playback_state", "status": "playing", "is_active": True})
    assert helperd.soloist_playing() is True
    assert fake_ctl.calls() == ["ctl -D /var/lib/soloist now --json"]
    fake_ctl({"status": "playing", "is_active": False})
    assert helperd.soloist_playing() is False
    fake_ctl({"status": "paused"})
    assert helperd.soloist_playing() is False
    fake_ctl({"status": "buffering"})
    assert helperd.soloist_playing() is None
    fake_ctl(None)
    assert helperd.soloist_playing() is None


def test_restart_while_playing_reports_playing_without_an_event(fake_ctl, helper):
    fake_ctl({"status": "playing", "is_active": True})
    helper.reconcile(startup=True)
    assert helper.status()["playing"] is True
    assert helper.state.deadline is None
    assert helper.connects == [True]
    helper.clock[0] += 3600
    helper.tick()
    assert helper.connects == [True]


def test_startup_without_soloist_starts_the_idle_timer(fake_ctl, helper):
    fake_ctl(None)
    helper.reconcile(startup=True)
    assert helper.state.playing is False
    assert helper.state.deadline == helper.clock[0] + 60
    assert helper.connects == []


def test_periodic_reconcile_follows_soloist_and_only_reads_it(fake_ctl, helper):
    fake_ctl({"status": "paused"})
    helper.reconcile(startup=True)
    fake_ctl({"status": "playing", "is_active": True})
    helper.reconcile()
    assert helper.state.playing is True and helper.connects == [True]
    helper.reconcile()
    assert helper.connects == [True]
    fake_ctl(None)
    helper.reconcile()
    assert helper.state.playing is True
    fake_ctl({"status": "idle"})
    helper.reconcile()
    assert helper.state.playing is False and helper.state.deadline is not None
    assert all(c.endswith("now --json") for c in fake_ctl.calls())


def test_run_timer_reconciles_every_minute(monkeypatch, helper):
    seen = []
    monkeypatch.setattr(helper, "reconcile", lambda: seen.append(helper.clock[0]))

    def sleep(s):
        if helper.clock[0] >= 1000 + 125:
            raise SystemExit
        helper.clock[0] += s

    monkeypatch.setattr(helperd.time, "sleep", sleep)
    with pytest.raises(SystemExit):
        helper.run_timer()
    assert seen == [1060.0, 1120.0]


def test_main_reconciles_from_soloist(fake_ctl, monkeypatch):
    class FakeServer:
        def __init__(self, addr, handler):
            pass

        def serve_forever(self):
            pass

    fake_ctl({"status": "playing", "is_active": True})
    monkeypatch.setenv("SPEAKER_MAC", "aa:bb:cc:dd:ee:ff")
    monkeypatch.setattr(helperd, "ThreadingHTTPServer", FakeServer)
    monkeypatch.setattr(helperd.Helper, "run_timer", lambda self: None)
    monkeypatch.setattr(helperd.Helper, "connect", lambda self: True)
    created = []
    real = helperd.Helper
    monkeypatch.setattr(helperd, "Helper", lambda mac: created.append(real(mac)) or created[-1])
    helperd.main()
    assert created[0].state.playing is True and created[0].connect_ok is True


@pytest.fixture
def server(helper):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), helperd.make_handler(helper))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", helper
    srv.shutdown()
    srv.server_close()


def test_failed_connect_is_a_503_and_shows_in_status(monkeypatch, server):
    url, helper = server
    monkeypatch.setattr(helperd, "bluetoothctl", lambda *a: "Connected: no\n")
    monkeypatch.setattr(helper, "connect", lambda: False)
    code, body = call(url + "/playing", "POST", {"playing": True})
    assert code == 503
    assert body["playing"] is True and body["connect_ok"] is False
    assert call(url + "/status")[1]["connect_ok"] is False
    monkeypatch.setattr(helper, "connect", lambda: True)
    code, body = call(url + "/playing", "POST", {"playing": True})
    assert code == 200 and body["connect_ok"] is True
    code, body = call(url + "/playing", "POST", {"playing": False})
    assert code == 200
