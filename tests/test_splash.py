import json
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.main import BOOT_HTML, create_app
from tests.test_caption_lifetime import DISPLAY_JS, HARNESS, needs_node


def _run(setup, body):
    script = (
        f"{HARNESS}\n{setup}\nrequire({json.dumps(str(DISPLAY_JS))});\n"
        f"(async () => {{\n{body}\n}})();"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_display_page_carries_boot_splash(client):
    r = client.get("/")
    assert r.status_code == 200
    assert '<div class="flag"><i></i><i></i><i></i><i></i></div>' in r.text
    assert "<b>Home Board</b>" in r.text
    assert '<div class="bar"><div class="blocks"></div></div>' in r.text
    assert ".splash .blocks {" in r.text
    assert "window.location.href" not in r.text
    assert r.text.index('<div class="flag">') > r.text.index("<iframe")


def test_boot_page_keeps_shared_parts_standalone():
    text = BOOT_HTML.read_text(encoding="ascii")
    assert '<body class="splash">' in text
    assert ">Starting Home Board...</span>" in text


@needs_node
def test_splash_fades_after_first_photo_and_stays_gone():
    r = _run(
        "",
        """
        const sp = els.splash;
        const snap = () => ({ hidden: sp.hidden, fade: sp.classList.contains("fade") });
        const start = snap();
        await advance(100);
        const painted = ["layer-a", "layer-b"].some((k) => els[k].classList.contains("front"));
        const early = snap();
        await advance(2000);
        const after = snap();
        server.photos = server.photos.concat([{ id: 4, url: "/p/4.jpg", caption: "" }]);
        sources[0].emit("photos_changed");
        await advance(25000);
        const later = snap();
        console.log(JSON.stringify({ start, painted, early, after, later }));
        """,
    )
    assert r["start"] == {"hidden": False, "fade": False}
    assert r["painted"] is True
    assert r["early"]["hidden"] is False
    assert r["after"]["hidden"] is True
    assert r["later"]["hidden"] is True


@needs_node
def test_splash_hides_when_empty_qr_shows():
    r = _run(
        'server.photos = []; els.empty = el("empty"); els.empty.hidden = true;',
        """
        const start = els.splash.hidden;
        await advance(2000);
        console.log(JSON.stringify({
          start, empty: els.empty.hidden, splash: els.splash.hidden,
        }));
        """,
    )
    assert r == {"start": False, "empty": False, "splash": True}
