import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.chrome import needs_node

SCREEN_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "tv_screen.js"


def _node(body):
    script = f"const T = require({json.dumps(str(SCREEN_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@pytest.fixture
def screens(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c.get("/api/screens").json()


@needs_node
def test_needs_for_all_screens_is_the_union_of_their_keys(screens):
    r = _node(
        f"const s = {json.dumps(screens)};\n"
        "console.log(JSON.stringify({all: T.needs(s), each: s.map((x) => T.needs(x))}));"
    )
    union = set()
    for keys in r["each"]:
        union |= set(keys)
    assert len(screens) == 3
    assert sorted(r["all"]) == sorted(union) == ["events", "lists", "weather"]
    assert len(r["all"]) == len(set(r["all"]))


@needs_node
def test_neighbour_side_and_transform_strings():
    r = _node(
        "console.log(JSON.stringify({left: T.neighbourSide(-300), right: T.neighbourSide(300),"
        " none: T.neighbourSide(0), tx: T.slideX(-150), zero: T.slideX(0)}));"
    )
    assert r == {
        "left": "right",
        "right": "left",
        "none": None,
        "tx": "translateX(-150px)",
        "zero": "translateX(0px)",
    }
