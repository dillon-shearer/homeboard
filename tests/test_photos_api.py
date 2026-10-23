import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import db, events
from app.main import create_app


def _jpeg(color):
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def _upload(client):
    files = [
        ("files", ("a.jpg", _jpeg((255, 0, 0)), "image/jpeg")),
        ("files", ("b.jpg", _jpeg((0, 0, 255)), "image/jpeg")),
        ("files", ("notes.txt", b"not an image", "text/plain")),
    ]
    return client.post("/api/photos", files=files, headers={"User-Agent": "x" * 300})


def _ids(client, query=""):
    return [p["id"] for p in client.get("/api/photos" + query).json()]


def test_upload_mixed_files(client):
    events.last_event = None
    r = _upload(client)
    assert r.status_code == 201
    body = r.json()
    assert len(body["created"]) == 2
    assert body["errors"] == [{"name": "notes.txt", "error": "cannot open"}]
    assert events.last_event == "photos_changed"
    p = body["created"][0]
    assert p["url"].startswith("/photos/") and p["thumb_url"].startswith("/thumbs/")
    assert (p["width"], p["height"], p["enabled"]) == (640, 480, True)
    conn = db.connect(client.app.state.db_path)
    assert conn.execute("SELECT uploaded_by FROM photos").fetchone()[0] == "x" * 200
    conn.close()


def test_upload_only_bad_files_is_400(client):
    files = [("files", ("notes.txt", b"nope", "text/plain"))]
    r = client.post("/api/photos", files=files)
    assert r.status_code == 400
    assert r.json()["created"] == []


def test_list_in_order_and_static_files(client):
    created = _upload(client).json()["created"]
    assert _ids(client) == [p["id"] for p in created]
    r = client.get(created[0]["url"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert "immutable" in r.headers["cache-control"]
    assert client.get(created[0]["thumb_url"]).status_code == 200


def test_patch_disable_hides_from_default_list(client):
    a, b = [p["id"] for p in _upload(client).json()["created"]]
    r = client.patch(f"/api/photos/{a}", json={"enabled": False, "caption": "hi"})
    assert r.status_code == 200
    assert r.json()["enabled"] is False and r.json()["caption"] == "hi"
    assert _ids(client) == [b]
    assert _ids(client, "?all=1") == [a, b]
    r = client.patch(f"/api/photos/{a}", json={"caption": None})
    assert r.json()["caption"] is None and r.json()["enabled"] is False
    assert client.patch("/api/photos/999", json={"enabled": True}).status_code == 404


def test_put_order_reverses(client):
    ids = [p["id"] for p in _upload(client).json()["created"]]
    r = client.put("/api/photos/order", json={"ids": list(reversed(ids))})
    assert r.status_code == 200
    assert _ids(client) == list(reversed(ids))
    assert client.put("/api/photos/order", json={"ids": [ids[0], 999]}).status_code == 400


def test_delete_removes_row_and_files(client, tmp_path):
    p = _upload(client).json()["created"][0]
    photo = tmp_path / "photos" / p["url"].rsplit("/", 1)[1]
    thumb = tmp_path / "thumbs" / p["thumb_url"].rsplit("/", 1)[1]
    assert photo.exists() and thumb.exists()
    assert client.delete(f"/api/photos/{p['id']}").status_code == 204
    assert p["id"] not in _ids(client, "?all=1")
    assert not photo.exists() and not thumb.exists()
    assert client.delete(f"/api/photos/{p['id']}").status_code == 404


def test_healthz_checks_db_and_photos_dir(client, tmp_path):
    assert client.get("/healthz").status_code == 200
    (tmp_path / "photos").rename(tmp_path / "gone")
    assert client.get("/healthz").status_code == 503
