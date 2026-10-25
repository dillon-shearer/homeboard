import pytest
from fastapi.responses import JSONResponse

from app import routes_sound
from tests.chrome import api, needs_chrome, needs_node
from tests.test_control_panel import DOWN, OPEN, _load, _node, _sent, _swipe

KIOSK = "document.getElementById('cp-kiosk')"
SHOWN = f"!{KIOSK}.hidden"
PANEL_OPEN = "!document.getElementById('control-panel').hidden"


@needs_node
def test_preset_stepping_helpers():
    r = _node(
        """
        const I = S.INTERVAL_PRESETS, T = S.TIMEOUT_PRESETS, D = S.IDLE_PRESETS;
        console.log(JSON.stringify({
          top: S.stepPreset(I, 600, 1),
          bottom: S.stepPreset(I, 5, -1),
          up: S.stepPreset(I, 10, 1),
          down: S.stepPreset(I, 10, -1),
          snapUp: S.stepPreset(I, 12, 1),
          snap: S.nearestPreset(I, 25),
          odd: S.nearestPreset(I, 7),
          missing: S.nearestPreset(I, undefined),
          never: S.stepPreset(T, 0, -1),
          timeout: S.stepPreset(T, 5, -1),
          idleTop: S.stepPreset(D, 120, 1),
          idleBottom: S.stepPreset(D, 1, -1),
          idleSnap: S.stepPreset(D, 7, 1),
        }));
        """
    )
    assert r["top"] == 600
    assert r["bottom"] == 5
    assert r["up"] == 15
    assert r["down"] == 5
    assert r["snapUp"] == 15
    assert r["snap"] == 20
    assert r["odd"] == 5
    assert r["missing"] == 5
    assert r["never"] == 0
    assert r["timeout"] == 2
    assert r["idleTop"] == 120
    assert r["idleBottom"] == 1
    assert r["idleSnap"] == 10


@pytest.fixture
def helper(monkeypatch):
    def fake(method, path, timeout=5):
        return JSONResponse({"speaker": {"connected": False, "name": "Bar"}})

    monkeypatch.setattr(routes_sound, "helper", fake)


def _puts(chrome):
    return [s for s in _sent(chrome) if s["method"] == "PUT" and s["url"] == "/api/settings"]


@needs_chrome
def test_kiosk_settings_dialog(chrome, server, helper):
    api.put(
        f"{server}/api/settings",
        json={"interval_s": 10, "channel_timeout_min": 5, "shuffle": False},
    )
    _load(chrome, server)
    _swipe(chrome, 500, DOWN)
    chrome.wait(OPEN)
    label = chrome.eval("document.getElementById('cp-settings').textContent")
    under_restart = chrome.eval(
        "document.getElementById('cp-restart').closest('section') === "
        "document.getElementById('cp-settings').closest('section')"
    )
    chrome.eval("document.getElementById('cp-settings').click()")
    chrome.wait(f"{SHOWN} && document.getElementById('cp-kiosk-interval_s').textContent === '10 s'")
    panel_open = chrome.eval(PANEL_OPEN)
    legends = chrome.eval(f"[...{KIOSK}.querySelectorAll('.w95-legend')].map(n => n.textContent)")
    typed = chrome.eval(f"{KIOSK}.querySelectorAll('input[type=text], input[type=number]').length")
    fit = chrome.eval(f"{KIOSK}.querySelector('input[name=cp-kiosk-fit][value=contain]').checked")
    box = chrome.eval(
        f"(d => ({{sh: d.scrollHeight, ch: d.clientHeight, top: d.getBoundingClientRect().top,"
        f" bottom: d.getBoundingClientRect().bottom}}))({KIOSK}.querySelector('.cp-dialog'))"
    )
    heights = chrome.eval(
        f"[...{KIOSK}.querySelectorAll('.cp-rocker, .w95-check, #cp-kiosk-done')]"
        ".map(n => n.getBoundingClientRect().height)"
    )

    chrome.eval(f"{KIOSK}.querySelector('[data-field=interval_s][data-step=\"1\"]').click()")
    chrome.wait("document.getElementById('cp-kiosk-interval_s').textContent === '15 s'")
    chrome.wait("window.__inflight === 0")
    chrome.eval(
        f"{KIOSK}.querySelector('[data-field=channel_timeout_min][data-step=\"-1\"]').click()"
    )
    chrome.wait("document.getElementById('cp-kiosk-channel_timeout_min').textContent === '2 min'")
    chrome.wait("window.__inflight === 0")
    chrome.eval(f"{KIOSK}.querySelector('input[data-field=shuffle]').click()")
    chrome.wait("window.__inflight === 0")
    shuffle_box = chrome.eval(f"{KIOSK}.querySelector('input[data-field=shuffle]').checked")
    puts = _puts(chrome)
    settings = api.get(f"{server}/api/settings").json()

    chrome.eval("document.getElementById('cp-kiosk-done').click()")
    closed = chrome.eval(f"{KIOSK}.hidden")
    still_open = chrome.eval(OPEN)
    errors = chrome.eval("window.__errors")

    assert label == "Settings..."
    assert under_restart is True
    assert panel_open is True
    assert legends == ["Photos", "Screens", "Sound", "Show"]
    assert typed == 0
    assert fit is True
    assert box["sh"] <= box["ch"]
    assert box["top"] >= 0 and box["bottom"] <= 600
    assert min(heights) >= 48
    assert [p["body"] for p in puts] == [
        {"interval_s": 15},
        {"channel_timeout_min": 2},
        {"shuffle": True},
    ]
    assert shuffle_box is True
    assert settings["interval_s"] == 15
    assert settings["channel_timeout_min"] == 2
    assert settings["shuffle"] is True
    assert closed is True
    assert still_open is True
    assert errors == []


@needs_chrome
def test_kiosk_settings_failed_put_and_idle_close(chrome, server, helper):
    _load(chrome, server)
    _swipe(chrome, 500, DOWN)
    chrome.wait(OPEN)
    chrome.eval("document.getElementById('cp-settings').click()")
    chrome.wait(f"{SHOWN} && document.getElementById('cp-kiosk-interval_s').textContent !== ''")
    chrome.eval(
        "(() => { const real = window.fetch; window.fetch = function (url, opts) {"
        " if (opts && opts.method === 'PUT') return Promise.resolve(new Response('', {status: 500}));"
        " return real.apply(this, arguments); }; })()"
    )
    chrome.eval(f"{KIOSK}.querySelector('input[data-field=captions]').click()")
    chrome.wait("!document.getElementById('cp-kiosk-error').hidden")
    chrome.wait("window.__inflight === 0")
    error = chrome.eval("document.getElementById('cp-kiosk-error').textContent")
    captions = chrome.eval(f"{KIOSK}.querySelector('input[data-field=captions]').checked")
    server_captions = api.get(f"{server}/api/settings").json()["captions"]

    chrome.eval("__test.skip(6 * 60000)")
    chrome.run("__test.tick(15000)")
    chrome.wait("document.getElementById('control-panel').hidden")

    assert error == "Could not save that setting. Try again."
    assert captions is server_captions
    assert chrome.eval(f"{KIOSK}.hidden") is True
