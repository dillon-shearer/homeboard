import io
import json
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, JpegImagePlugin

from app import images
from app.main import create_app

ADMIN_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "admin.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _jpeg(size, mode="RGB", exif=None):
    buf = io.BytesIO()
    extra = {"exif": exif} if exif else {}
    Image.new(mode, size, 128).save(buf, "JPEG", quality=50, **extra)
    return buf.getvalue()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


@needs_node
def test_run_limited_keeps_at_most_two_open_in_order():
    script = f"""
    const A = require({json.dumps(str(ADMIN_JS))});
    const open = [];
    let peak = 0;
    const started = [];
    const finished = [];
    const items = [0, 1, 2, 3, 4, 5, 6];
    A.runLimited(items, 2, function (i) {{
      started.push(i);
      open.push(i);
      peak = Math.max(peak, open.length);
      return new Promise(function (resolve, reject) {{
        setTimeout(function () {{
          open.splice(open.indexOf(i), 1);
          finished.push(i);
          if (i === 3) reject(new Error("x")); else resolve();
        }}, (7 - i) * 3);
      }});
    }}).then(function () {{
      console.log(JSON.stringify({{ peak: peak, started: started, finished: finished.length, open: open.length }}));
    }});
    """
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    result = json.loads(out.stdout)
    assert result["peak"] == 2
    assert result["started"] == [0, 1, 2, 3, 4, 5, 6]
    assert result["finished"] == 7
    assert result["open"] == 0


@needs_node
def test_run_limited_resolves_on_empty_list():
    script = f"""
    const A = require({json.dumps(str(ADMIN_JS))});
    A.runLimited([], 2, function () {{ throw new Error("no"); }}).then(function () {{
      console.log(JSON.stringify({{ ok: true }}));
    }});
    """
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    assert json.loads(out.stdout) == {"ok": True}


def test_on_files_uses_limited_queue():
    js = ADMIN_JS.read_text()
    assert "var UPLOAD_LIMIT = 2;" in js
    assert "runLimited(jobs, UPLOAD_LIMIT, function (job) {" in js
    assert "Promise.all(jobs)" not in js


@pytest.mark.parametrize("extra", [1, 5000])
def test_upload_over_limit_reads_at_most_limit_plus_one(client, monkeypatch, extra):
    limit = 1000
    monkeypatch.setattr(images, "MAX_UPLOAD_BYTES", limit)
    read_bytes = []
    real_read = tempfile.SpooledTemporaryFile.read

    def counting_read(self, *args):
        data = real_read(self, *args)
        read_bytes.append(len(data))
        return data

    monkeypatch.setattr(tempfile.SpooledTemporaryFile, "read", counting_read)
    called = []
    monkeypatch.setattr(images, "process_upload", lambda *a: called.append(a))
    r = client.post(
        "/api/photos", files=[("files", ("big.jpg", b"x" * (limit + extra), "image/jpeg"))]
    )
    assert r.status_code == 400
    assert r.json()["errors"] == [{"name": "big.jpg", "error": "too large"}]
    assert sum(read_bytes) <= limit + 1
    assert called == []


def test_upload_at_limit_still_processed(client, monkeypatch):
    data = _jpeg((640, 480))
    monkeypatch.setattr(images, "MAX_UPLOAD_BYTES", len(data))
    r = client.post("/api/photos", files=[("files", ("a.jpg", data, "image/jpeg"))])
    assert r.status_code == 201
    assert len(r.json()["created"]) == 1


def test_process_upload_runs_one_decode_at_a_time(tmp_path, monkeypatch):
    lock = threading.Lock()
    state = {"open": 0, "peak": 0}
    real_cover = images._cover

    def slow_cover(img, w, h):
        with lock:
            state["open"] += 1
            state["peak"] = max(state["peak"], state["open"])
        time.sleep(0.05)
        with lock:
            state["open"] -= 1
        return real_cover(img, w, h)

    monkeypatch.setattr(images, "_cover", slow_cover)
    data = _jpeg((64, 48))
    with ThreadPoolExecutor(6) as pool:
        jobs = [pool.submit(images.process_upload, data, tmp_path, tmp_path) for _ in range(6)]
    for job in jobs:
        job.result()
    assert state["peak"] == images.MAX_CONCURRENT
    assert images.MAX_CONCURRENT <= 2
    assert len(list(tmp_path.glob("*_t.jpg"))) == 6


def test_200mp_jpeg_is_accepted_at_cover_size(tmp_path, monkeypatch):
    drafts = []
    real_draft = JpegImagePlugin.JpegImageFile.draft

    def spy(self, mode, size):
        drafts.append(size)
        return real_draft(self, mode, size)

    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "draft", spy)
    data = _jpeg((16320, 12240), mode="L")
    p = images.process_upload(data, tmp_path, tmp_path)
    w, h = images.SCREEN_WIDTH, images.SCREEN_HEIGHT
    scale = max(w / 16320, h / 12240)
    assert (p.width, p.height) == (round(16320 * scale), round(12240 * scale))
    assert drafts == [(w, h)]
    with Image.open(tmp_path / p.filename) as out:
        assert out.size == (p.width, p.height)


def test_rotated_jpeg_draft_keeps_cover_size(tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 6
    data = _jpeg((4000, 3000), exif=exif.tobytes())
    p = images.process_upload(data, tmp_path, tmp_path)
    w, h = images.SCREEN_WIDTH, images.SCREEN_HEIGHT
    scale = max(w / 3000, h / 4000)
    assert (p.width, p.height) == (round(3000 * scale), round(4000 * scale))


def test_over_pixel_limit_is_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "MAX_PIXELS", 1000)
    with pytest.raises(images.ImageError, match="too large"):
        images.process_upload(_jpeg((40, 30)), tmp_path, tmp_path)


def test_non_jpeg_keeps_full_decode_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(images, "MAX_FULL_DECODE_PIXELS", 1000)
    buf = io.BytesIO()
    Image.new("RGB", (40, 30)).save(buf, "PNG")
    with pytest.raises(images.ImageError, match="too large"):
        images.process_upload(buf.getvalue(), tmp_path, tmp_path)
    p = images.process_upload(_jpeg((40, 30)), tmp_path, tmp_path)
    assert (p.width, p.height) == (40, 30)
