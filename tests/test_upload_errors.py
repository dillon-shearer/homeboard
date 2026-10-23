import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import events, images
from app.main import create_app


def _jpeg(color):
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def _files(*names):
    return [("files", (n, _jpeg((255, i * 40, 0)), "image/jpeg")) for i, n in enumerate(names)]


def _dir_files(path):
    return sorted(p.name for p in path.iterdir())


def test_unexpected_error_reports_processing_failed_and_keeps_others(client, monkeypatch):
    real = images.process_upload
    calls = []

    def flaky(data, photos_dir, thumbs_dir):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("odd exif")
        return real(data, photos_dir, thumbs_dir)

    monkeypatch.setattr(images, "process_upload", flaky)
    events.last_event = None
    r = client.post("/api/photos", files=_files("a.jpg", "b.jpg"))
    assert r.status_code == 201
    body = r.json()
    assert len(body["created"]) == 1
    assert body["errors"] == [{"name": "b.jpg", "error": "processing failed"}]
    assert events.last_event == "photos_changed"
    assert len(client.get("/api/photos").json()) == 1


def test_unexpected_error_after_save_still_publishes(client, monkeypatch):
    real = images.process_upload
    calls = []

    def flaky(data, photos_dir, thumbs_dir):
        calls.append(1)
        if len(calls) == 2:
            raise TypeError("bad tag")
        return real(data, photos_dir, thumbs_dir)

    monkeypatch.setattr(images, "process_upload", flaky)
    events.last_event = None
    r = client.post("/api/photos", files=_files("a.jpg", "b.jpg", "c.jpg"))
    assert r.status_code == 201
    assert len(r.json()["created"]) == 2
    assert events.last_event == "photos_changed"


def test_only_failed_files_do_not_publish(client, monkeypatch):
    def broken(data, photos_dir, thumbs_dir):
        raise ValueError("boom")

    monkeypatch.setattr(images, "process_upload", broken)
    events.last_event = None
    r = client.post("/api/photos", files=_files("a.jpg"))
    assert r.status_code == 400
    assert r.json()["errors"] == [{"name": "a.jpg", "error": "processing failed"}]
    assert events.last_event is None


def test_thumb_save_failure_leaves_no_files(client, monkeypatch):
    real = images._save
    calls = []

    def failing(img, path, *args, **kwargs):
        calls.append(path)
        if len(calls) == 2:
            path.write_bytes(b"half")
            raise OSError(28, "No space left on device")
        return real(img, path, *args, **kwargs)

    monkeypatch.setattr(images, "_save", failing)
    state = client.app.state
    r = client.post("/api/photos", files=_files("a.jpg"))
    assert r.status_code == 400
    assert r.json()["errors"] == [{"name": "a.jpg", "error": "processing failed"}]
    assert len(calls) == 2
    assert _dir_files(state.photos_dir) == []
    assert _dir_files(state.thumbs_dir) == []


def test_saved_files_have_final_names_only(client):
    state = client.app.state
    p = client.post("/api/photos", files=_files("a.jpg")).json()["created"][0]
    assert _dir_files(state.photos_dir) == [p["url"].rsplit("/", 1)[1]]
    assert _dir_files(state.thumbs_dir) == [p["thumb_url"].rsplit("/", 1)[1]]
