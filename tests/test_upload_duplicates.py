import hashlib
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import db
from app.main import create_app


def _jpeg(color):
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def _post(client, name, data):
    return client.post("/api/photos", files=[("files", (name, data, "image/jpeg"))])


def _names(path):
    return sorted(p.name for p in path.iterdir())


def test_same_bytes_twice_creates_one_photo(client):
    state = client.app.state
    data = _jpeg((10, 120, 200))
    assert _post(client, "a.jpg", data).status_code == 201
    photos, thumbs = _names(state.photos_dir), _names(state.thumbs_dir)
    r = _post(client, "again.jpg", data)
    assert r.status_code == 400
    assert r.json() == {
        "created": [],
        "errors": [{"name": "again.jpg", "error": "already on the board"}],
    }
    assert len(client.get("/api/photos?all=true").json()) == 1
    assert _names(state.photos_dir) == photos
    assert _names(state.thumbs_dir) == thumbs


def test_same_bytes_in_one_batch_creates_one_photo(client):
    data = _jpeg((10, 120, 200))
    files = [("files", (n, data, "image/jpeg")) for n in ("a.jpg", "b.jpg")]
    body = client.post("/api/photos", files=files).json()
    assert len(body["created"]) == 1
    assert body["errors"] == [{"name": "b.jpg", "error": "already on the board"}]


def test_different_photos_both_upload(client):
    assert _post(client, "a.jpg", _jpeg((255, 0, 0))).status_code == 201
    assert _post(client, "b.jpg", _jpeg((0, 0, 255))).status_code == 201
    assert len(client.get("/api/photos?all=true").json()) == 2


def test_hash_is_stored(client):
    data = _jpeg((1, 2, 3))
    _post(client, "a.jpg", data)
    conn = db.connect(client.app.state.db_path)
    try:
        row = conn.execute("SELECT source_sha256 FROM photos").fetchone()
    finally:
        conn.close()
    assert row["source_sha256"] == hashlib.sha256(data).hexdigest()


def test_migration_0003_on_existing_database(tmp_path):
    conn = db.connect(tmp_path / "old.db")
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        for v in (1, 2):
            sql = next(db.MIGRATIONS_DIR.glob(f"000{v}_*.sql"))
            conn.executescript(sql.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (v,))
        conn.execute(
            "INSERT INTO photos (filename, thumb, sort_order, width, height, uploaded_at)"
            " VALUES ('old.jpg', 'old_t.jpg', 10, 800, 600, '2026-01-01T00:00:00+00:00')"
        )
        conn.commit()
        assert db.migrate(conn) == 16
        row = conn.execute("SELECT * FROM photos").fetchone()
        assert row["filename"] == "old.jpg"
        assert row["source_sha256"] is None
        indexes = [r["name"] for r in conn.execute("PRAGMA index_list(photos)")]
        assert "photos_source_sha256" in indexes
    finally:
        conn.close()


def test_null_hashes_are_not_matched(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    try:
        db.migrate(conn)
        db.insert_photo(conn, "old.jpg", "old_t.jpg", 800, 600)
        assert not db.has_source_sha256(conn, "x" * 64)
    finally:
        conn.close()
