import json
import shutil
import subprocess
from pathlib import Path

import pytest

ADMIN_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "admin.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _node(body):
    script = f"const A = require({json.dumps(str(ADMIN_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@needs_node
def test_loads_under_node_and_sets_window_admin():
    result = _node("console.log(JSON.stringify({same: globalThis.Admin === A}))")
    assert result["same"] is True


@needs_node
def test_move_id():
    result = _node(
        """
        const ids = [1, 2, 3, 4];
        console.log(JSON.stringify({
          fwd: A.moveId(ids, 0, 2),
          back: A.moveId(ids, 3, 0),
          same: A.moveId(ids, 1, 1),
          out: A.moveId(ids, 0, 9),
          orig: ids,
        }));
        """
    )
    assert result["fwd"] == [2, 3, 1, 4]
    assert result["back"] == [4, 1, 2, 3]
    assert result["same"] == [1, 2, 3, 4]
    assert result["out"] == [1, 2, 3, 4]
    assert result["orig"] == [1, 2, 3, 4]


def test_admin_js_tile_actions():
    js = ADMIN_JS.read_text()
    assert 'var patch = photoPatch(p, $("caption-input").value, $("photo-enabled").checked);' in js
    assert 'send("PATCH", "/api/photos/" + tileId(tile), patch)' in js
    assert js.count('send("PATCH"') == 1
    assert 'send("PUT", "/api/photos/order", { ids: ids })' in js
    assert '"pointerdown"' in js and '"pointermove"' in js and '"pointerup"' in js
    assert "window.confirm(" not in js
    assert '$("confirm-delete").addEventListener("click", deletePhoto)' in js
