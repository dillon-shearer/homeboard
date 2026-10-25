import re

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.test_tv_admin import SETTINGS_JS, _tv, needs_node


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_settings_page_groups_in_order(client):
    r = client.get("/admin/settings")
    assert r.status_code == 200
    legends = re.findall(r'class="w95-legend">([^<]+)</h2>', r.text)
    assert legends == ["Photos", "Clock, date and caption", "Screens", "Sound", "Board"]
    assert '<span class="w95-title-text">Settings</span>' in r.text


def test_old_tv_page_redirects_to_settings(client):
    r = client.get("/admin/tv", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/admin/settings"


@needs_node
def test_brightness_slider_puts_brightness():
    r = _tv(
        """
        await settle();
        const slider = all["brightness"];
        slider.value = "40";
        slider.fire("input");
        const label = all["brightness-value"].textContent;
        slider.fire("change");
        await settle();
        console.log(JSON.stringify({ label, calls }));
        """,
        SETTINGS_JS,
    )
    assert r["label"] == "40%"
    assert r["calls"] == [{"method": "PUT", "url": "/api/settings", "body": {"brightness": 40}}]


@needs_node
def test_restart_asks_first_then_posts_and_explains_a_missing_helper():
    r = _tv(
        """
        await settle();
        const realFetch = globalThis.fetch;
        let status = 503;
        globalThis.fetch = (url, opts) => {
          if (url !== "/api/sound/reboot") return realFetch(url, opts);
          calls.push({ method: opts.method, url });
          return Promise.resolve({ ok: status === 200, status, json: () => Promise.resolve({}) });
        };
        all["restart-dialog"].hidden = true;
        all["restart-error"].hidden = true;
        all["restart"].fire("click");
        await settle();
        const asked = { open: !all["restart-dialog"].hidden, calls: calls.length };
        all["restart-no"].fire("click");
        const cancelled = { open: !all["restart-dialog"].hidden, calls: calls.length };
        all["restart"].fire("click");
        all["restart-yes"].fire("click");
        await settle();
        const failed = { open: !all["restart-dialog"].hidden, error: all["restart-error"].textContent };
        status = 200;
        all["restart-yes"].fire("click");
        await settle();
        const done = { open: !all["restart-dialog"].hidden, state: all["display-state"].textContent };
        console.log(JSON.stringify({ asked, cancelled, failed, done, calls }));
        """,
        SETTINGS_JS,
    )
    assert r["asked"] == {"open": True, "calls": 0}
    assert r["cancelled"] == {"open": False, "calls": 0}
    assert r["failed"] == {
        "open": True,
        "error": "Helper not running. The board was not restarted.",
    }
    assert r["done"] == {"open": False, "state": "Home Board is restarting..."}
    assert r["calls"] == [{"method": "POST", "url": "/api/sound/reboot"}] * 2
