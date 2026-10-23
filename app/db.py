import re
import sqlite3
from datetime import UTC, date, datetime, time
from pathlib import Path

from app import settings

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
SETTINGS_FIELDS = (
    "interval_s",
    "transition",
    "fit",
    "shuffle",
    "captions",
    "channel",
    "channel_timeout_min",
    "lists_screen_id",
)
FITS = ("contain", "cover")
CHANNELS = ("photos", "calendar", "lists")
CHANNEL_TIMEOUTS = (0, 2, 5, 10, 30)
LIST_KINDS = ("checklist", "notes")
LIST_NAME_MAX = 60
SCREEN_NAME_MAX = 60
DEFAULT_SCREEN_NAME = "Lists"
ITEM_TEXT_MAX = 200
EVENT_TITLE_MAX = 120
REPEATS = ("never", "weekly", "monthly", "yearly")
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
    source_sha256: str | None = None,
) -> sqlite3.Row:
    with conn:
        cur = conn.execute(
            "INSERT INTO photos (filename, thumb, caption, enabled, sort_order, width, height,"
            " uploaded_at, uploaded_by, source_sha256)"
            " VALUES (?, ?, ?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 10 FROM photos),"
            " ?, ?, ?, ?, ?)",
            (
                filename,
                thumb,
                caption,
                int(enabled),
                width,
                height,
                _now(),
                uploaded_by,
                source_sha256,
            ),
        )
    return get_photo(conn, cur.lastrowid)


def has_source_sha256(conn: sqlite3.Connection, source_sha256: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM photos WHERE source_sha256 = ? LIMIT 1", (source_sha256,)
    ).fetchone()
    return row is not None


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
    if "channel" in fields and fields["channel"] not in CHANNELS:
        raise ValueError("channel must be one of photos, calendar, lists")
    if "channel_timeout_min" in fields:
        v = fields["channel_timeout_min"]
        if isinstance(v, bool) or v not in CHANNEL_TIMEOUTS:
            raise ValueError("channel_timeout_min must be one of 0, 2, 5, 10, 30")
    if "lists_screen_id" in fields:
        fields["lists_screen_id"] = _check_screen_id(conn, fields["lists_screen_id"])
    for k in ("transition", "shuffle", "captions"):
        if k in fields:
            fields[k] = int(fields[k])
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with conn:
        conn.execute(f"UPDATE settings SET {sets} WHERE id = 1", tuple(fields.values()))
    return get_settings(conn)


def get_current_photo_id(conn: sqlite3.Connection) -> int | None:
    return get_settings(conn)["current_photo_id"]


def set_current_photo_id(conn: sqlite3.Connection, id: int | None) -> None:
    with conn:
        conn.execute(
            "UPDATE settings SET current_photo_id = ? WHERE id = 1 AND current_photo_id IS NOT ?",
            (id, id),
        )


def _clean_text(value, limit: int, label: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{label} cannot be empty")
    if len(value) > limit:
        raise ValueError(f"{label} must be at most {limit} characters")
    return value


def _check_kind(kind) -> str:
    if kind not in LIST_KINDS:
        raise ValueError("kind must be 'checklist' or 'notes'")
    return kind


def list_lists(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM lists ORDER BY sort_order, id").fetchall()


def get_list(conn: sqlite3.Connection, id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM lists WHERE id = ?", (id,)).fetchone()


def create_list(conn: sqlite3.Connection, name, kind="checklist") -> sqlite3.Row:
    name = _clean_text(name, LIST_NAME_MAX, "name")
    kind = _check_kind(kind)
    first = conn.execute("SELECT id FROM tv_screens ORDER BY sort_order, id LIMIT 1").fetchone()
    with conn:
        cur = conn.execute(
            "INSERT INTO lists (name, kind, screen_id, sort_order, created_at)"
            " VALUES (?, ?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 10 FROM lists), ?)",
            (name, kind, first["id"] if first else None, _now()),
        )
    return get_list(conn, cur.lastrowid)


def update_list(
    conn: sqlite3.Connection,
    id: int,
    name=_UNSET,
    kind=_UNSET,
    show_on_tv=_UNSET,
    screen_id=_UNSET,
) -> sqlite3.Row | None:
    fields = {}
    if name is not _UNSET:
        fields["name"] = _clean_text(name, LIST_NAME_MAX, "name")
    if kind is not _UNSET:
        fields["kind"] = _check_kind(kind)
    if screen_id is not _UNSET:
        fields["screen_id"] = _check_screen_id(conn, screen_id)
    elif show_on_tv is not _UNSET:
        fields["screen_id"] = _first_screen_id(conn) if show_on_tv else None
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with conn:
            conn.execute(f"UPDATE lists SET {sets} WHERE id = ?", (*fields.values(), id))
    return get_list(conn, id)


def delete_list(conn: sqlite3.Connection, id: int) -> sqlite3.Row | None:
    row = get_list(conn, id)
    if row is not None:
        with conn:
            conn.execute("DELETE FROM lists WHERE id = ?", (id,))
    return row


def reorder_lists(conn: sqlite3.Connection, ids) -> None:
    ordered = list(dict.fromkeys(int(i) for i in ids))
    known = [r["id"] for r in list_lists(conn)]
    unknown = [i for i in ordered if i not in known]
    if unknown:
        raise ValueError(f"unknown list ids: {unknown}")
    rest = [i for i in known if i not in set(ordered)]
    with conn:
        for n, lid in enumerate(ordered + rest, start=1):
            conn.execute("UPDATE lists SET sort_order = ? WHERE id = ?", (n * 10, lid))


def _check_screen_id(conn: sqlite3.Connection, screen_id) -> int | None:
    if screen_id is None:
        return None
    if get_screen(conn, screen_id) is None:
        raise ValueError(f"unknown screen id: {screen_id}")
    return screen_id


def _first_screen_id(conn: sqlite3.Connection) -> int:
    screens = list_screens(conn)
    if screens:
        return screens[0]["id"]
    return create_screen(conn, DEFAULT_SCREEN_NAME)["id"]


def list_screens(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM tv_screens ORDER BY sort_order, id").fetchall()


def get_screen(conn: sqlite3.Connection, id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tv_screens WHERE id = ?", (id,)).fetchone()


def screen_list_ids(conn: sqlite3.Connection, id: int) -> list[int]:
    return [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM lists WHERE screen_id = ? ORDER BY sort_order, id", (id,)
        )
    ]


def create_screen(conn: sqlite3.Connection, name) -> sqlite3.Row:
    name = _clean_text(name, SCREEN_NAME_MAX, "name")
    with conn:
        cur = conn.execute(
            "INSERT INTO tv_screens (name, sort_order, created_at)"
            " VALUES (?, (SELECT COALESCE(MAX(sort_order), 0) + 10 FROM tv_screens), ?)",
            (name, _now()),
        )
    return get_screen(conn, cur.lastrowid)


def update_screen(conn: sqlite3.Connection, id: int, name=_UNSET) -> sqlite3.Row | None:
    if name is not _UNSET:
        name = _clean_text(name, SCREEN_NAME_MAX, "name")
        with conn:
            conn.execute("UPDATE tv_screens SET name = ? WHERE id = ?", (name, id))
    return get_screen(conn, id)


def delete_screen(conn: sqlite3.Connection, id: int) -> sqlite3.Row | None:
    row = get_screen(conn, id)
    if row is not None:
        with conn:
            conn.execute("UPDATE lists SET screen_id = NULL WHERE screen_id = ?", (id,))
            conn.execute(
                "UPDATE settings SET lists_screen_id = NULL WHERE lists_screen_id = ?", (id,)
            )
            conn.execute("DELETE FROM tv_screens WHERE id = ?", (id,))
    return row


def reorder_screens(conn: sqlite3.Connection, ids) -> None:
    ordered = list(dict.fromkeys(int(i) for i in ids))
    known = [r["id"] for r in list_screens(conn)]
    unknown = [i for i in ordered if i not in known]
    if unknown:
        raise ValueError(f"unknown screen ids: {unknown}")
    rest = [i for i in known if i not in set(ordered)]
    with conn:
        for n, sid in enumerate(ordered + rest, start=1):
            conn.execute("UPDATE tv_screens SET sort_order = ? WHERE id = ?", (n * 10, sid))


def list_items(conn: sqlite3.Connection, list_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM list_items WHERE list_id = ? ORDER BY pinned DESC, sort_order, id",
        (list_id,),
    ).fetchall()


def get_item(conn: sqlite3.Connection, id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM list_items WHERE id = ?", (id,)).fetchone()


def add_item(conn: sqlite3.Connection, list_id: int, text) -> sqlite3.Row:
    text = _clean_text(text, ITEM_TEXT_MAX, "text")
    with conn:
        cur = conn.execute(
            "INSERT INTO list_items (list_id, text, sort_order, created_at)"
            " VALUES (?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 10 FROM list_items"
            " WHERE list_id = ?), ?)",
            (list_id, text, list_id, _now()),
        )
    return get_item(conn, cur.lastrowid)


def update_item(
    conn: sqlite3.Connection, id: int, text=_UNSET, done=_UNSET, pinned=_UNSET
) -> sqlite3.Row | None:
    fields = {}
    if text is not _UNSET:
        fields["text"] = _clean_text(text, ITEM_TEXT_MAX, "text")
    if done is not _UNSET:
        fields["done"] = int(done)
    if pinned is not _UNSET:
        fields["pinned"] = int(pinned)
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with conn:
            conn.execute(f"UPDATE list_items SET {sets} WHERE id = ?", (*fields.values(), id))
    return get_item(conn, id)


def delete_item(conn: sqlite3.Connection, id: int) -> sqlite3.Row | None:
    row = get_item(conn, id)
    if row is not None:
        with conn:
            conn.execute("DELETE FROM list_items WHERE id = ?", (id,))
    return row


def reorder_items(conn: sqlite3.Connection, list_id: int, ids) -> None:
    ordered = list(dict.fromkeys(int(i) for i in ids))
    known = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM list_items WHERE list_id = ? ORDER BY sort_order, id", (list_id,)
        )
    ]
    unknown = [i for i in ordered if i not in known]
    if unknown:
        raise ValueError(f"unknown item ids: {unknown}")
    rest = [i for i in known if i not in set(ordered)]
    with conn:
        for n, iid in enumerate(ordered + rest, start=1):
            conn.execute("UPDATE list_items SET sort_order = ? WHERE id = ?", (n * 10, iid))


def clear_done(conn: sqlite3.Connection, list_id: int) -> int:
    with conn:
        cur = conn.execute("DELETE FROM list_items WHERE list_id = ? AND done = 1", (list_id,))
    return cur.rowcount


def _check_date(value) -> str:
    return date.fromisoformat(value).isoformat()


def _check_time(value) -> str | None:
    if value is None:
        return None
    return time.fromisoformat(value).isoformat(timespec="minutes")


def _check_repeat(repeat) -> str:
    if repeat not in REPEATS:
        raise ValueError("repeat must be one of never, weekly, monthly, yearly")
    return repeat


def list_events(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM events ORDER BY date, time, id").fetchall()


def get_event(conn: sqlite3.Connection, id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM events WHERE id = ?", (id,)).fetchone()


def create_event(conn: sqlite3.Connection, title, day, at=None, repeat="never") -> sqlite3.Row:
    values = (
        _clean_text(title, EVENT_TITLE_MAX, "title"),
        _check_date(day),
        _check_time(at),
        _check_repeat(repeat),
        _now(),
    )
    with conn:
        cur = conn.execute(
            "INSERT INTO events (title, date, time, repeat, created_at) VALUES (?, ?, ?, ?, ?)",
            values,
        )
    return get_event(conn, cur.lastrowid)


def update_event(conn: sqlite3.Connection, id: int, **changes) -> sqlite3.Row | None:
    checks = {
        "title": lambda v: _clean_text(v, EVENT_TITLE_MAX, "title"),
        "date": _check_date,
        "time": _check_time,
        "repeat": _check_repeat,
    }
    unknown = set(changes) - set(checks)
    if unknown:
        raise ValueError(f"unknown event fields: {', '.join(sorted(unknown))}")
    fields = {k: checks[k](v) for k, v in changes.items()}
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with conn:
            conn.execute(f"UPDATE events SET {sets} WHERE id = ?", (*fields.values(), id))
    return get_event(conn, id)


def delete_event(conn: sqlite3.Connection, id: int) -> sqlite3.Row | None:
    row = get_event(conn, id)
    if row is not None:
        with conn:
            conn.execute("DELETE FROM events WHERE id = ?", (id,))
    return row
