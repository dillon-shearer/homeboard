import sqlite3

import pytest

from app import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    yield c
    c.close()


def _add(conn, n):
    return [db.insert_photo(conn, f"p{i}.jpg", f"t{i}.jpg", 800, 600) for i in range(n)]


def test_connect_pragmas(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.row_factory is sqlite3.Row


def test_migrate_twice_is_idempotent(conn):
    assert db.migrate(conn) == 11
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 11
    assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 1


def test_settings_row_exists_after_migrate(conn):
    s = db.get_settings(conn)
    assert s["id"] == 1
    assert s["interval_s"] == 20
    assert s["transition"] == 1
    assert s["fit"] == "contain"
    assert s["shuffle"] == 0
    assert s["captions"] == 0


def test_insert_photo_sort_order_gaps_of_10(conn):
    rows = _add(conn, 3)
    assert [r["sort_order"] for r in rows] == [10, 20, 30]
    assert rows[0]["enabled"] == 1
    assert rows[0]["uploaded_at"].endswith("+00:00")


def test_set_order_reverses(conn):
    ids = [r["id"] for r in _add(conn, 3)]
    db.set_order(conn, list(reversed(ids)))
    photos = db.list_photos(conn)
    assert [p["id"] for p in photos] == list(reversed(ids))
    assert [p["sort_order"] for p in photos] == [10, 20, 30]


def test_set_order_leaves_missing_ids_after(conn):
    a, b, c = [r["id"] for r in _add(conn, 3)]
    db.set_order(conn, [c])
    assert [p["id"] for p in db.list_photos(conn)] == [c, a, b]


def test_update_photo_only_given_fields(conn):
    p = _add(conn, 1)[0]
    db.update_photo(conn, p["id"], caption="hi")
    r = db.update_photo(conn, p["id"], enabled=False)
    assert r["caption"] == "hi"
    assert r["enabled"] == 0
    assert db.list_photos(conn, enabled_only=True) == []


def test_delete_returns_row(conn):
    p = _add(conn, 1)[0]
    row = db.delete_photo(conn, p["id"])
    assert row["filename"] == "p0.jpg"
    assert row["thumb"] == "t0.jpg"
    assert db.get_photo(conn, p["id"]) is None
    assert db.delete_photo(conn, p["id"]) is None


@pytest.mark.parametrize("bad", [4, 601])
def test_update_settings_rejects_interval(conn, bad):
    with pytest.raises(ValueError):
        db.update_settings(conn, interval_s=bad)
    assert db.get_settings(conn)["interval_s"] == 20


@pytest.mark.parametrize("good", [5, 600])
def test_update_settings_accepts_interval(conn, good):
    s = db.update_settings(conn, interval_s=good)
    assert s["interval_s"] == good
    assert s["updated_at"] is not None


def test_update_settings_validates_fit(conn):
    with pytest.raises(ValueError):
        db.update_settings(conn, fit="stretch")
    assert db.update_settings(conn, fit="cover")["fit"] == "cover"
