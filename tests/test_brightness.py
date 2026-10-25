import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from app import events
from app.main import create_app
from tests.chrome import needs_node
from tests.test_caption_lifetime import DISPLAY_JS


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_brightness_defaults_to_100_and_saves(client):
    assert client.get("/api/settings").json()["brightness"] == 100
    events.last_event = None
    r = client.put("/api/settings", json={"brightness": 65})
    assert r.status_code == 200 and r.json()["brightness"] == 65
    assert events.last_event == "settings_changed"
    assert client.get("/api/settings").json()["brightness"] == 65


@pytest.mark.parametrize("value", [5, 9, 101, 65.5, True, "bright"])
def test_brightness_out_of_range_is_422(client, value):
    assert client.put("/api/settings", json={"brightness": value}).status_code == 422
    assert client.get("/api/settings").json()["brightness"] == 100


def test_brightness_limits_are_accepted(client):
    assert client.put("/api/settings", json={"brightness": 10}).json()["brightness"] == 10
    assert client.put("/api/settings", json={"brightness": 100}).json()["brightness"] == 100


@needs_node
def test_brightness_helpers():
    script = f"""
    const S = require({json.dumps(str(DISPLAY_JS))});
    console.log(JSON.stringify({{
      clampLow: S.clampBrightness(3),
      clampHigh: S.clampBrightness(140),
      dim65: S.dimOpacity(65),
      dim100: S.dimOpacity(100),
      dimFloor: S.dimOpacity(0),
      setLow: S.applySettings(null, {{brightness: 2}}).brightness,
      setDefault: S.applySettings(null, {{}}).brightness,
    }}));
    """
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    r = json.loads(out.stdout)
    assert r["clampLow"] == 10 and r["clampHigh"] == 100
    assert r["dim65"] == pytest.approx(0.35)
    assert r["dim100"] == 0 and r["dimFloor"] == pytest.approx(0.9)
    assert r["setLow"] == 10 and r["setDefault"] == 100
