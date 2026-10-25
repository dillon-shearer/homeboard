import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import images
from app.main import create_app

PROXY_CONF = Path(__file__).resolve().parent.parent / "deploy" / "nginx" / "homeboard-proxy.conf"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_waiting_uploads_are_not_buffered(client, monkeypatch):
    lock = threading.Lock()
    state = {"open": 0, "peak": 0, "entered": 0}
    release = threading.Event()
    real_init = tempfile.SpooledTemporaryFile.__init__
    real_close = tempfile.SpooledTemporaryFile.close

    def counting_init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        self._counted = True
        with lock:
            state["open"] += 1
            state["peak"] = max(state["peak"], state["open"])

    def counting_close(self):
        if getattr(self, "_counted", False):
            self._counted = False
            with lock:
                state["open"] -= 1
        real_close(self)

    def blocked(data, *args):
        with lock:
            state["entered"] += 1
        release.wait(10)
        raise images.ImageError("cannot open")

    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "__init__", counting_init)
    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "close", counting_close)
    monkeypatch.setattr(images, "process_upload", blocked)
    n = images.MAX_ADMITTED + 4
    with ThreadPoolExecutor(n) as pool:
        jobs = [
            pool.submit(
                client.post,
                "/api/photos",
                files=[("files", (f"{i}.jpg", bytes([i]) * 4096, "image/jpeg"))],
            )
            for i in range(n)
        ]
        for _ in range(100):
            with lock:
                if state["entered"] == images.MAX_ADMITTED:
                    break
            threading.Event().wait(0.05)
        threading.Event().wait(0.3)
        with lock:
            assert state["entered"] == images.MAX_ADMITTED
            assert state["peak"] == images.MAX_ADMITTED
        release.set()
    assert [j.result().status_code for j in jobs] == [400] * n
    assert state["peak"] == images.MAX_ADMITTED
    assert state["open"] == 0
    assert images.MAX_ADMITTED * images.MAX_REQUEST_BYTES <= 110 * 1024 * 1024


def test_oversized_request_is_413_before_the_body_is_read(client, monkeypatch):
    monkeypatch.setattr(images, "MAX_REQUEST_BYTES", 1000)
    opened = []
    real_init = tempfile.SpooledTemporaryFile.__init__

    def counting_init(self, *args, **kwargs):
        opened.append(1)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "__init__", counting_init)
    r = client.post("/api/photos", files=[("files", ("big.jpg", b"x" * 2000, "image/jpeg"))])
    assert r.status_code == 413
    assert r.json() == {"detail": "too large"}
    assert opened == []


def test_upload_without_length_is_411(client):
    def chunks():
        yield b"x"

    r = client.post(
        "/api/photos",
        content=chunks(),
        headers={"content-type": "multipart/form-data; boundary=x"},
    )
    assert r.status_code == 411


def test_other_routes_are_not_gated(client):
    assert client.get("/api/photos").status_code == 200
    assert client.put("/api/photos/order", json={"ids": []}).status_code == 200


def test_nginx_body_limit_matches_the_app():
    text = PROXY_CONF.read_text()
    assert "client_max_body_size 0;" not in text
    size = re.search(r"client_max_body_size (\d+)m;", text)
    assert size is not None
    assert int(size.group(1)) * 1024 * 1024 == images.MAX_REQUEST_BYTES
    assert images.MAX_REQUEST_BYTES >= images.MAX_UPLOAD_BYTES
