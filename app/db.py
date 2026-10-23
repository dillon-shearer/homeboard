import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app import settings

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
SETTINGS_FIELDS = ("interval_s", "transition", "fit", "shuffle", "captions")
FITS = ("contain", "cover")
_UNSET = object()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or settings.DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migration_files():
    files = []
    for f in MIGRATIONS_DIR.glob("*.sql"):
        m = re.match(r"(\d+)_", f.name)
        if m:
            files.append((int(m.group(1)), f))
    return sorted(files)


def migrate(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
    conn.commit()
    current = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
    for version, f in _migration_files():
        if version <= current:
            continue
        sql = f.read_text(encoding="utf-8")
        try:
            conn.executescript(
                f"BEGIN;\n{sql}\nINSERT INTO schema_version (version) VALUES ({version});\nCOMMIT;"
            )
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        current = version
    return current


def list_photos(conn: sqlite3.Connection, enabled_only=False) -> list[sqlite3.Row]:
    sql = "SELECT * FROM photos"
    if enabled_only:
        sql += " WHERE enabled = 1"
    return conn.execute(sql + " ORDER BY sort_order, id").fetchall()


def get_photo(conn: sqlite3.Connection, id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM photos WHERE id = ?", (id,)).fetchone()


def insert_photo(
    conn: sqlite3.Connection,
    filename: str,
    thumb: str,
    width: int,
    height: int,
    caption: str | None = None,
    uploaded_by: str | None = None,
    enabled: bool = True,
) -> sqlite3.Row:
    with conn:
        cur = conn.execute(
            "INSERT INTO photos (filename, thumb, caption, enabled, sort_order, width, height,"
            " uploaded_at, uploaded_by)"
            " VALUES (?, ?, ?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 10 FROM photos),"
            " ?, ?, ?, ?)",
            (filename, thumb, caption, int(enabled), width, height, _now(), uploaded_by),
        )
    return get_photo(conn, cur.lastrowid)


def update_photo(
    conn: sqlite3.Connection, id: int, caption=_UNSET, enabled=_UNSET
) -> sqlite3.Row | None:
    fields = {}
    if caption is not _UNSET:
        fields["caption"] = caption
    if enabled is not _UNSET:
        fields["enabled"] = int(enabled)
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with conn:
            conn.execute(f"UPDATE photos SET {sets} WHERE id = ?", (*fields.values(), id))
    return get_photo(conn, id)


def delete_photo(conn: sqlite3.Connection, id: int) -> sqlite3.Row | None:
    row = get_photo(conn, id)
    if row is not None:
        with conn:
            conn.execute("DELETE FROM photos WHERE id = ?", (id,))
    return row


def set_order(conn: sqlite3.Connection, ids) -> None:
    ordered = list(dict.fromkeys(int(i) for i in ids))
    given = set(ordered)
    rest = [r["id"] for r in list_photos(conn) if r["id"] not in given]
    with conn:
        for n, pid in enumerate(ordered + rest, start=1):
            conn.execute("UPDATE photos SET sort_order = ? WHERE id = ?", (n * 10, pid))


def get_settings(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()


def update_settings(conn: sqlite3.Connection, **fields) -> sqlite3.Row:
    unknown = set(fields) - set(SETTINGS_FIELDS)
    if unknown:
        raise ValueError(f"unknown settings: {', '.join(sorted(unknown))}")
    if "interval_s" in fields:
        v = fields["interval_s"]
        if isinstance(v, bool) or not isinstance(v, int) or not 5 <= v <= 600:
            raise ValueError("interval_s must be an integer from 5 to 600")
    if "fit" in fields and fields["fit"] not in FITS:
        raise ValueError("fit must be 'contain' or 'cover'")
    for k in ("transition", "shuffle", "captions"):
        if k in fields:
            fields[k] = int(fields[k])
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with conn:
        conn.execute(f"UPDATE settings SET {sets} WHERE id = 1", tuple(fields.values()))
    return get_settings(conn)
