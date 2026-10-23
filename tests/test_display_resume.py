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


def test_current_photo_survives_restart(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        assert c.get("/api/display/current").json() == {"id": None}
        assert c.put("/api/display/current", json={"id": 7}).json() == {"id": 7}
    with TestClient(create_app(tmp_path)) as c:
        assert c.get("/api/display/current").json() == {"id": 7}


def test_current_photo_rejects_bad_id(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        assert c.put("/api/display/current", json={"id": "x"}).status_code == 422


def test_set_same_current_photo_skips_write(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.migrate(conn)
    db.set_current_photo_id(conn, 3)
    before = conn.total_changes
    db.set_current_photo_id(conn, 3)
    assert conn.total_changes == before
    db.set_current_photo_id(conn, 4)
    assert conn.total_changes == before + 1
    assert db.get_current_photo_id(conn) == 4
    conn.close()


@needs_node
def test_resume_starts_on_saved_photo_in_order():
    result = _node(
        """
        const ids = [1, 2, 3, 4, 5];
        const o = S.resumeOrder(ids, 3, false);
        const shown = [];
        let order = o.order, pos = o.pos;
        for (let i = 0; i < 4; i++) {
          const n = S.nextIndex(order, pos, ids, false);
          order = n.order; pos = n.pos; shown.push(order[pos]);
        }
        console.log(JSON.stringify(shown));
        """
    )
    assert result == [3, 4, 5, 1]


@needs_node
def test_resume_missing_photo_starts_on_first():
    result = _node(
        """
        const out = [];
        for (const r of [9, null, undefined]) {
          const o = S.resumeOrder([2, 4, 6], r, false);
          const n = S.nextIndex(o.order, o.pos, [2, 4, 6], false);
          out.push(n.order[n.pos]);
        }
        console.log(JSON.stringify(out));
        """
    )
    assert result == [2, 2, 2]


@needs_node
def test_resume_with_shuffle_shows_saved_first_and_never_twice_in_row():
    result = _node(
        """
        const ids = [1, 2, 3, 4, 5];
        const runs = [];
        for (let t = 0; t < 100; t++) {
          const o = S.resumeOrder(ids, 4, true);
          let order = o.order, pos = o.pos;
          const shown = [];
          for (let i = 0; i < 15; i++) {
            const n = S.nextIndex(order, pos, ids, true);
            order = n.order; pos = n.pos; shown.push(order[pos]);
          }
          runs.push(shown);
        }
        console.log(JSON.stringify(runs));
        """
    )
    for shown in result:
        assert shown[0] == 4
        assert sorted(shown[:5]) == [1, 2, 3, 4, 5]
        assert all(a != b for a, b in itertools.pairwise(shown))
