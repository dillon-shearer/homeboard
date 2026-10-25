import sqlite3

import pytest

from app import db


def _columns(conn):
    return [r["name"] for r in conn.execute("PRAGMA table_info(lists)")]


def _db_at_version_10(path):
    conn = db.connect(path)
    conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
    for v in range(1, 11):
        sql = next(db.MIGRATIONS_DIR.glob(f"{v:04d}_*.sql"))
        conn.executescript(sql.read_text(encoding="utf-8"))
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (v,))
    conn.commit()
    return conn


def test_fresh_database_has_no_old_tv_columns(tmp_path):
    conn = db.connect(tmp_path / "fresh.db")
    try:
        db.migrate(conn)
        cols = _columns(conn)
        assert "show_on_tv" not in cols and "screen_id" not in cols
        assert "tv_open" in cols
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_upgrade_keeps_lists_items_and_widgets(tmp_path):
    conn = _db_at_version_10(tmp_path / "old.db")
    try:
        assert "screen_id" in _columns(conn)
        notes = db.create_list(conn, "Notes", "notes")
        conn.execute("DELETE FROM lists WHERE name = 'Publix'")
        conn.execute("UPDATE lists SET tv_open = 0 WHERE id = ?", (notes["id"],))
        conn.commit()
        bjs = conn.execute("SELECT * FROM lists WHERE name = 'BJs'").fetchone()
        milk = db.add_item(conn, bjs["id"], "milk")
        eggs = db.add_item(conn, notes["id"], "eggs")
        brief = db.list_screens(conn)[0]
        with conn:
            widget_id = conn.execute(
                "INSERT INTO widgets (screen_id, kind, list_id, w, h, sort_order, created_at)"
                " VALUES (?, 'lists', ?, 1, 1, 40, 'x')",
                (brief["id"], notes["id"]),
            ).lastrowid
        lists_before = [dict(r) for r in conn.execute("SELECT * FROM lists ORDER BY id")]
        items_before = [dict(r) for r in conn.execute("SELECT * FROM list_items ORDER BY id")]
        widgets_before = [dict(r) for r in conn.execute("SELECT * FROM widgets ORDER BY id")]

        db.migrate(conn)

        cols = _columns(conn)
        assert "show_on_tv" not in cols and "screen_id" not in cols
        for row in lists_before:
            del row["show_on_tv"], row["screen_id"]
        assert [dict(r) for r in conn.execute("SELECT * FROM lists ORDER BY id")] == lists_before
        assert [dict(r) for r in conn.execute("SELECT * FROM list_items ORDER BY id")] == (
            items_before
        )
        widgets_after = [dict(r) for r in conn.execute("SELECT * FROM widgets ORDER BY id")]
        for row in widgets_after:
            del row["col"], row["row"]
        assert widgets_after == widgets_before
        assert {milk["id"], eggs["id"]} <= {i["id"] for i in items_before}
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

        db.delete_list(conn, notes["id"])
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM list_items WHERE list_id = ?", (notes["id"],)
            ).fetchone()[0]
            == 0
        )
        assert db.get_widget(conn, widget_id) is None
        db.delete_screen(conn, brief["id"])
        assert db.get_screen(conn, brief["id"]) is None
    finally:
        conn.close()


def test_failed_migration_restores_foreign_keys(tmp_path, monkeypatch):
    bad = tmp_path / "migrations"
    bad.mkdir()
    (bad / "0001_bad.sql").write_text("CREATE TABLE a (id INTEGER PRIMARY KEY);\nnot sql;")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", bad)
    conn = db.connect(tmp_path / "bad.db")
    try:
        with pytest.raises(sqlite3.OperationalError):
            db.migrate(conn)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'a'").fetchone() is None
    finally:
        conn.close()
