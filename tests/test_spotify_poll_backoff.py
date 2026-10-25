import json
import subprocess

from tests.chrome import needs_chrome, needs_node
from tests.test_caption_lifetime import DISPLAY_JS
from tests.test_review_spotify import _open_surface, _publish, surface_player  # noqa: F401
from tests.test_tv_spotify import _playing


@needs_node
def test_poll_delay_doubles_and_caps():
    script = (
        f"const S = require({json.dumps(str(DISPLAY_JS))});"
        "console.log(JSON.stringify([0, 1, 2, 3, 9].map(n => S.pollDelay(15000, n))))"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    assert json.loads(out.stdout) == [15000, 15000, 30000, 60000, 120000]


@needs_chrome
def test_control_panel_backs_off_while_stale_and_resets(
    chrome,
    server,
    surface_player,  # noqa: F811
):
    _open_surface(chrome, server, "panel")
    chrome.eval(
        "window.pollDelays = []; window.pollTimeout = window.setTimeout;"
        "window.setTimeout = function(fn, ms) {"
        "if (fn && fn.name === 'loadNow') pollDelays.push(ms);"
        "return pollTimeout.apply(this, arguments); };"
    )
    surface_player.now = _playing(stale=True, reason="rate limited")
    for _ in range(3):
        _publish(chrome)
    assert chrome.eval("pollDelays") == [15000, 30000, 60000]
    assert "too many requests" in chrome.eval("document.getElementById('control-panel').innerText")
    surface_player.now = _playing()
    _publish(chrome)
    assert chrome.eval("pollDelays.slice(-1)[0]") == 15000
