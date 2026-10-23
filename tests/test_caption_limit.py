import io
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.routes_photos import CAPTION_MAX

APP = Path(__file__).resolve().parent.parent / "app"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def _photo_id(client):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (255, 0, 0)).save(buf, "JPEG")
    r = client.post("/api/photos", files=[("files", ("a.jpg", buf.getvalue(), "image/jpeg"))])
    return r.json()["created"][0]["id"]


def _caption(client, pid):
    return next(p["caption"] for p in client.get("/api/photos?all=1").json() if p["id"] == pid)


def test_caption_at_limit_saved(client):
    pid = _photo_id(client)
    r = client.patch(f"/api/photos/{pid}", json={"caption": "a" * CAPTION_MAX})
    assert r.status_code == 200
    assert _caption(client, pid) == "a" * CAPTION_MAX


def test_long_caption_rejected_and_unchanged(client):
    pid = _photo_id(client)
    client.patch(f"/api/photos/{pid}", json={"caption": "keep"})
    r = client.patch(f"/api/photos/{pid}", json={"caption": "a" * (CAPTION_MAX + 1)})
    assert r.status_code == 422
    assert _caption(client, pid) == "keep"


def test_caption_input_maxlength_matches_api():
    html = (APP / "templates" / "admin.html").read_text(encoding="utf-8")
    tag = re.search(r'<input id="caption-input"[^>]*>', html).group(0)
    assert f'maxlength="{CAPTION_MAX}"' in tag


def test_display_caption_clamped_to_two_lines():
    css = (APP / "static" / "display.css").read_text(encoding="utf-8")
    rule = re.search(r"#caption \{([^}]*)\}", css).group(1)
    for decl in ("display: -webkit-box", "-webkit-line-clamp: 2", "-webkit-box-orient: vertical", "overflow: hidden"):
        assert decl in rule
