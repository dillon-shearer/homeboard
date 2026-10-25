import itertools
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import create_app

DISPLAY_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "display.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _node(body):
    script = f"const S = require({json.dumps(str(DISPLAY_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


@needs_node
def test_loads_under_node_and_sets_window_slideshow():
    result = _node(
        "console.log(JSON.stringify({same: globalThis.Slideshow === S,"
        " fns: Object.keys(S).filter(k => typeof S[k] === 'function')}))"
    )
    assert result["same"] is True
    assert {"nextIndex", "shuffleIds", "applySettings", "mergeList"} <= set(result["fns"])


@needs_node
def test_shuffle_never_starts_with_last_id_of_previous_cycle():
    result = _node(
        """
        const ids = [1, 2, 3, 4, 5];
        let order = [], pos = -1, cycles = [], cur = [];
        while (cycles.length < 201) {
          const n = S.nextIndex(order, pos, ids, true);
          if (n.pos === 0 && cur.length) { cycles.push(cur); cur = []; }
          order = n.order; pos = n.pos;
          cur.push(order[pos]);
        }
        console.log(JSON.stringify(cycles));
        """
    )
    assert len(result) == 201
    for prev, cycle in itertools.pairwise(result):
        assert sorted(cycle) == [1, 2, 3, 4, 5]
        assert cycle[0] != prev[-1]


@needs_node
def test_merge_after_current_removed_shows_next_in_order():
    result = _node(
        """
        const old = [1, 2, 3, 4, 5];
        const out = [];
        for (const [current, next] of [[3, [1, 2, 4, 5]], [5, [1, 2, 3, 4]], [2, [1, 2, 3]]]) {
          const m = S.mergeList(old, next, current);
          let o = S.orderAround(next, m.id, false);
          let pos = m.removed ? o.pos - 1 : o.pos;
          const n = S.nextIndex(o.order, pos, next, false);
          out.push({m, shown: n.order[n.pos]});
        }
        console.log(JSON.stringify(out));
        """
    )
    assert result[0] == {"m": {"id": 4, "removed": True}, "shown": 4}
    assert result[1] == {"m": {"id": 1, "removed": True}, "shown": 1}
    assert result[2] == {"m": {"id": 2, "removed": False}, "shown": 3}


@needs_node
def test_apply_settings_and_backoff():
    result = _node(
        """
        const a = S.applySettings(null, {interval_s: 30, fit: 'cover', shuffle: 1});
        const b = S.applySettings(a, {fit: 'stretch'});
        let s = 1, seq = [1];
        for (let i = 0; i < 7; i++) { s = S.nextBackoff(s); seq.push(s); }
        console.log(JSON.stringify({a, b, seq, clock: S.formatClock(new Date(2026, 0, 1, 13, 5))}));
        """
    )
    assert result["a"] == {
        "interval_s": 30,
        "transition": True,
        "fit": "cover",
        "shuffle": True,
        "captions": False,
        "channel": "photos",
        "screen_id": None,
        "channel_timeout_min": 5,
        "clock_size": 5,
        "caption_size": 5,
        "clock_pos": "bottom-right",
        "caption_pos": "bottom-left",
        "clock_on_top": True,
        "date_size": 3,
        "date_pos": "bottom-right",
        "date_on_top": False,
        "date_show": True,
        "brightness": 100,
    }
    assert result["b"]["fit"] == "contain" and result["b"]["interval_s"] == 30
    assert result["seq"] == [1, 2, 4, 8, 16, 30, 30, 30]
    assert result["clock"] == "1:05 pm"


def test_display_empty_state_has_qr(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert '<div id="empty">' in r.text
    assert "<svg" in r.text
    assert "/admin" in r.text
    assert "/static/display.js" in r.text


def test_display_with_photo_has_no_empty_state(client):
    conn = db.connect(client.app.state.db_path)
    db.insert_photo(conn, "a.jpg", "a.jpg", 640, 480)
    conn.close()
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="empty"' not in r.text
    assert "<svg" not in r.text
    assert 'id="layer-a"' in r.text and 'id="layer-b"' in r.text


def test_static_assets_served(client):
    assert client.get("/static/display.js").status_code == 200
    assert client.get("/static/display.css").status_code == 200
