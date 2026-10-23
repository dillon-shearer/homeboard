import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app

ADMIN_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "admin.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _mpo():
    buf = io.BytesIO()
    frames = [Image.new("RGB", (640, 480), (0, 128, 255)), Image.new("RGB", (640, 480), (0, 0, 0))]
    frames[0].save(buf, "MPO", save_all=True, append_images=frames[1:])
    return buf.getvalue()


def _gif():
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (255, 0, 0)).save(buf, "GIF")
    return buf.getvalue()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_mpo_fixture_opens_as_mpo():
    with Image.open(io.BytesIO(_mpo())) as img:
        assert img.format == "MPO"


def test_mpo_upload_is_served_as_jpeg(client):
    files = [("files", ("PXL_0001.jpg", _mpo(), "image/jpeg"))]
    r = client.post("/api/photos", files=files)
    assert r.status_code == 201
    body = r.json()
    assert body["errors"] == []
    p = body["created"][0]
    assert (p["width"], p["height"]) == (640, 480)
    assert [x["id"] for x in client.get("/api/photos").json()] == [p["id"]]
    shown = client.get(p["url"])
    assert shown.status_code == 200
    with Image.open(io.BytesIO(shown.content)) as out:
        assert out.format == "JPEG"


def test_server_rejects_non_photos_sent_as_octet_stream(client):
    files = [
        ("files", ("notes", b"not an image", "application/octet-stream")),
        ("files", ("anim", _gif(), "application/octet-stream")),
    ]
    r = client.post("/api/photos", files=files)
    assert r.status_code == 400
    assert r.json()["errors"] == [
        {"name": "notes", "error": "cannot open"},
        {"name": "anim", "error": "unsupported format"},
    ]


@needs_node
def test_admin_is_supported_lets_server_decide_unknown_types():
    script = f"""
    const A = require({json.dumps(str(ADMIN_JS))});
    const cases = [
      ["photo.jpg", "image/jpeg"],
      ["photo.jpg", "image/jpg"],
      ["photo.jpg", "image/pjpeg"],
      ["IMG_1.HEIC", ""],
      ["blob", ""],
      ["photo", "application/octet-stream"],
      ["photo.jpg", "application/octet-stream"],
      ["photo.avif", "image/avif"],
      ["notes.txt", "text/plain"],
      ["clip.mp4", "video/mp4"],
      ["doc.pdf", "application/pdf"],
    ];
    console.log(JSON.stringify(cases.map(([name, type]) => A.isSupported({{ name, type }}))));
    """
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    assert json.loads(out.stdout) == [True] * 8 + [False] * 3
