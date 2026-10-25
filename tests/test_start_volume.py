from fastapi.testclient import TestClient

from app import spotify
from app.main import create_app
from tests.chrome import needs_chrome
from tests.test_tv_screen import _sp_until, fake  # noqa: F401
from tests.test_tv_spotify import _playing

SHOWN = "!document.getElementById('volume-group').hidden"


def test_start_has_volume_between_icons_and_tv_section(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        page = c.get("/admin").text
    icons = page.index('<ul class="w95-icons">')
    volume = page.index('id="volume-group"')
    showing = page.index('id="group-showing"')
    assert icons < volume < showing
    assert 'id="volume-group" class="w95-group" aria-labelledby="group-volume" hidden' in page
    assert "start_volume.js" in page


@needs_chrome
def test_volume_shows_only_while_playing_and_sends_changes(chrome, server, fake):  # noqa: F811
    fake.now = _playing(volume=40)
    chrome.size(400, 800)
    chrome.send("Page.navigate", {"url": f"{server}/admin"})
    chrome.wait(SHOWN)
    assert chrome.eval("document.getElementById('volume-value').textContent") == "40%"
    assert chrome.eval("document.getElementById('volume-slider').value") == "40"
    wide = "document.documentElement.scrollWidth <= window.innerWidth"
    assert chrome.eval(wide)

    chrome.eval("document.getElementById('volume-up').click()")
    _sp_until(lambda: fake.sent("PUT", "/api/spotify/volume"), "the volume call")
    assert fake.sent("PUT", "/api/spotify/volume")[0][2] == {"volume_percent": 45}
    assert chrome.eval("document.getElementById('volume-value').textContent") == "45%"

    chrome.eval(
        "const s = document.getElementById('volume-slider'); s.value = '20';"
        "s.dispatchEvent(new Event('input')); s.dispatchEvent(new Event('change'))"
    )
    _sp_until(lambda: len(fake.sent("PUT", "/api/spotify/volume")) >= 2, "the slider call")
    assert fake.sent("PUT", "/api/spotify/volume")[-1][2] == {"volume_percent": 20}

    fake.now = _playing(playing=False)
    chrome.wait("!" + SHOWN)
    fake.now = spotify._empty()
    chrome.send("Page.navigate", {"url": f"{server}/admin"})
    chrome.wait("document.readyState === 'complete'")
    _sp_until(lambda: len(fake.sent("GET", "/api/spotify/now")) >= 3, "a poll")
    assert not chrome.eval(SHOWN)
    fake.now = _playing()
    chrome.wait(SHOWN)
