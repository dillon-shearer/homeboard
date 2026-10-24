import re
import sqlite3
from datetime import UTC, date, datetime, time
from itertools import permutations
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
    "screen_id",
    "clock_size",
    "caption_size",
    "clock_pos",
    "caption_pos",
    "clock_on_top",
    "brightness",
    "weather_location",
)
FITS = ("contain", "cover")
CHANNELS = ("photos", "screen")
CHANNEL_TIMEOUTS = (0, 2, 5, 10, 30)
POSITIONS = (
    "top-left",
    "top-center",
    "top-right",
    "middle-left",
    "middle-center",
    "middle-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
)
OVERLAY_SIZE_MIN = 2
OVERLAY_SIZE_MAX = 12
LIST_KINDS = ("checklist", "notes")
LIST_NAME_MAX = 60
SCREEN_NAME_MAX = 60
WIDGET_KINDS = ("clock", "weather", "calendar", "lists")
WIDGET_SIZES = (1, 2)
GRID_COLS = 2
GRID_ROWS = 2
WEATHER_LOCATION_MAX = 40
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
    foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        for version, f in _migration_files():
            if version <= current:
                continue
            sql = f.read_text(encoding="utf-8")
            try:
                conn.executescript(
                    f"BEGIN;\n{sql}\nINSERT INTO schema_version (version) VALUES ({version});"
                )
                if foreign_keys and conn.execute("PRAGMA foreign_key_check").fetchone():
                    raise sqlite3.IntegrityError(f"migration {version} broke a foreign key")
                conn.commit()
            except Exception:
                if conn.in_transaction:
                    conn.rollback()
                raise
            current = version
    finally:
        conn.execute(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")
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
    conn: sqlite3.Connection,
    id: int,
    caption=_UNSET,
    enabled=_UNSET,
    focus_x=_UNSET,
    focus_y=_UNSET,
) -> sqlite3.Row | None:
    fields = {}
    if caption is not _UNSET:
        fields["caption"] = caption
    if enabled is not _UNSET:
        fields["enabled"] = int(enabled)
    if focus_x is not _UNSET:
        fields["focus_x"] = int(focus_x)
    if focus_y is not _UNSET:
        fields["focus_y"] = int(focus_y)
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
        raise ValueError("channel must be 'photos' or 'screen'")
    if "channel_timeout_min" in fields:
        v = fields["channel_timeout_min"]
        if isinstance(v, bool) or v not in CHANNEL_TIMEOUTS:
            raise ValueError("channel_timeout_min must be one of 0, 2, 5, 10, 30")
    for k in ("clock_size", "caption_size"):
        if k in fields:
            v = fields[k]
            if isinstance(v, bool) or not isinstance(v, int):
                raise ValueError(f"{k} must be an integer")
            if not OVERLAY_SIZE_MIN <= v <= OVERLAY_SIZE_MAX:
                raise ValueError(f"{k} must be from {OVERLAY_SIZE_MIN} to {OVERLAY_SIZE_MAX}")
    for k in ("clock_pos", "caption_pos"):
        if k in fields and fields[k] not in POSITIONS:
            raise ValueError(f"{k} must be one of {', '.join(POSITIONS)}")
    if "brightness" in fields:
        v = fields["brightness"]
        if isinstance(v, bool) or not isinstance(v, int) or not 10 <= v <= 100:
            raise ValueError("brightness must be an integer from 10 to 100")
    if "screen_id" in fields:
        fields["screen_id"] = _check_screen_id(conn, fields["screen_id"])
    if "weather_location" in fields:
        v = fields["weather_location"]
        if not isinstance(v, str):
            raise ValueError("weather_location must be text")
        fields["weather_location"] = _clean_text(v, WEATHER_LOCATION_MAX, "weather_location")
        fields.update(weather_lat=None, weather_lon=None, weather_name=None)
    for k in ("transition", "shuffle", "captions", "clock_on_top"):
        if k in fields:
            fields[k] = int(fields[k])
    fields["updated_at"] = _now()
    sets = ", ".join(f"{k} = ?" for k in fields)
    with conn:
        conn.execute(f"UPDATE settings SET {sets} WHERE id = 1", tuple(fields.values()))
    return get_settings(conn)


def set_weather_place(conn: sqlite3.Connection, location: str, lat, lon, name) -> bool:
    with conn:
        cur = conn.execute(
            "UPDATE settings SET weather_lat = ?, weather_lon = ?, weather_name = ?"
            " WHERE id = 1 AND weather_location = ?",
            (lat, lon, name, location),
        )
    return cur.rowcount == 1


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
    with conn:
        cur = conn.execute(
            "INSERT INTO lists (name, kind, sort_order, created_at)"
            " VALUES (?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 10 FROM lists), ?)",
            (name, kind, _now()),
        )
    return get_list(conn, cur.lastrowid)


def update_list(
    conn: sqlite3.Connection,
    id: int,
    name=_UNSET,
    kind=_UNSET,
    tv_open=_UNSET,
) -> sqlite3.Row | None:
    fields = {}
    if name is not _UNSET:
        fields["name"] = _clean_text(name, LIST_NAME_MAX, "name")
    if kind is not _UNSET:
        fields["kind"] = _check_kind(kind)
    if tv_open is not _UNSET:
        fields["tv_open"] = int(tv_open)
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with conn:
            conn.execute(f"UPDATE lists SET {sets} WHERE id = ?", (*fields.values(), id))
    return get_list(conn, id)


def delete_list(conn: sqlite3.Connection, id: int) -> sqlite3.Row | None:
    row = get_list(conn, id)
    if row is not None:
        screens = [
            r["screen_id"]
            for r in conn.execute("SELECT DISTINCT screen_id FROM widgets WHERE list_id = ?", (id,))
        ]
        with conn:
            conn.execute("DELETE FROM lists WHERE id = ?", (id,))
            for sid in screens:
                _repack(conn, sid)
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


def list_screens(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM tv_screens ORDER BY sort_order, id").fetchall()


def get_screen(conn: sqlite3.Connection, id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tv_screens WHERE id = ?", (id,)).fetchone()


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
        s = get_settings(conn)
        showing = s["screen_id"] if s["screen_id"] is not None else list_screens(conn)[0]["id"]
        with conn:
            if s["channel"] == "screen" and showing == id:
                conn.execute("UPDATE settings SET channel = 'photos' WHERE id = 1")
            conn.execute("UPDATE settings SET screen_id = NULL WHERE screen_id = ?", (id,))
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


def pack_widgets(widgets) -> list[dict]:
    taken = set()
    placed = []
    for wd in widgets:
        w, h = wd["w"], wd["h"]
        free = [(c, r) for r in range(GRID_ROWS) for c in range(GRID_COLS) if (c, r) not in taken]
        if not free:
            raise ValueError("the screen is full")
        col, row = free[0]
        cells = {(col + dc, row + dr) for dc in range(w) for dr in range(h)}
        if col + w > GRID_COLS or row + h > GRID_ROWS or cells & taken:
            raise ValueError(f"a {w}x{h} widget does not fit on this screen")
        taken |= cells
        placed.append({"id": wd["id"], "col": col, "row": row, "w": w, "h": h})
    return placed


def list_widgets(conn: sqlite3.Connection, screen_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM widgets WHERE screen_id = ? ORDER BY sort_order, id", (screen_id,)
    ).fetchall()


def get_widget(conn: sqlite3.Connection, id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM widgets WHERE id = ?", (id,)).fetchone()


def _check_size(v, label: str) -> int:
    if isinstance(v, bool) or v not in WIDGET_SIZES:
        raise ValueError(f"{label} must be 1 or 2")
    return v


def _check_widget_list(conn: sqlite3.Connection, kind: str, list_id) -> int | None:
    if list_id is None:
        return None
    if kind != "lists":
        raise ValueError("list_id is only for lists widgets")
    if get_list(conn, list_id) is None:
        raise ValueError(f"unknown list id: {list_id}")
    return list_id


def add_widget(
    conn: sqlite3.Connection, screen_id: int, kind, w=None, h=None, list_id=None
) -> sqlite3.Row:
    if kind not in WIDGET_KINDS:
        raise ValueError(f"kind must be one of {', '.join(WIDGET_KINDS)}")
    list_id = _check_widget_list(conn, kind, list_id)
    current = [dict(r) for r in list_widgets(conn, screen_id)]
    if w is None and h is None:
        sizes = [(2, 2), (2, 1), (1, 2), (1, 1)]
    else:
        sizes = [(_check_size(1 if w is None else w, "w"), _check_size(1 if h is None else h, "h"))]
    error = None
    for sw, sh in sizes:
        try:
            pack_widgets([*current, {"id": None, "w": sw, "h": sh}])
        except ValueError as e:
            error = error or e
            continue
        with conn:
            cur = conn.execute(
                "INSERT INTO widgets (screen_id, kind, list_id, w, h, sort_order, created_at)"
                " VALUES (?, ?, ?, ?, ?, (SELECT COALESCE(MAX(sort_order), 0) + 10 FROM widgets"
                " WHERE screen_id = ?), ?)",
                (screen_id, kind, list_id, sw, sh, screen_id, _now()),
            )
        return get_widget(conn, cur.lastrowid)
    raise error


def update_widget(
    conn: sqlite3.Connection, id: int, w=_UNSET, h=_UNSET, list_id=_UNSET
) -> sqlite3.Row | None:
    row = get_widget(conn, id)
    if row is None:
        return None
    fields = {}
    if w is not _UNSET:
        fields["w"] = _check_size(w, "w")
    if h is not _UNSET:
        fields["h"] = _check_size(h, "h")
    if list_id is not _UNSET:
        fields["list_id"] = _check_widget_list(conn, row["kind"], list_id)
    if "w" in fields or "h" in fields:
        layout = [dict(r) for r in list_widgets(conn, row["screen_id"])]
        for wd in layout:
            if wd["id"] == id:
                wd.update(w=fields.get("w", wd["w"]), h=fields.get("h", wd["h"]))
        pack_widgets(layout)
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        with conn:
            conn.execute(f"UPDATE widgets SET {sets} WHERE id = ?", (*fields.values(), id))
    return get_widget(conn, id)


def _repack(conn: sqlite3.Connection, screen_id: int) -> None:
    layout = [dict(r) for r in list_widgets(conn, screen_id)]
    for order in permutations(layout):
        try:
            pack_widgets(order)
        except ValueError:
            continue
        for n, wd in enumerate(order, start=1):
            conn.execute("UPDATE widgets SET sort_order = ? WHERE id = ?", (n * 10, wd["id"]))
        return


def delete_widget(conn: sqlite3.Connection, id: int) -> sqlite3.Row | None:
    row = get_widget(conn, id)
    if row is not None:
        with conn:
            conn.execute("DELETE FROM widgets WHERE id = ?", (id,))
            _repack(conn, row["screen_id"])
    return row


def reorder_widgets(conn: sqlite3.Connection, screen_id: int, ids) -> None:
    ordered = list(dict.fromkeys(int(i) for i in ids))
    layout = {r["id"]: dict(r) for r in list_widgets(conn, screen_id)}
    unknown = [i for i in ordered if i not in layout]
    if unknown:
        raise ValueError(f"unknown widget ids: {unknown}")
    new = ordered + [i for i in layout if i not in set(ordered)]
    pack_widgets([layout[i] for i in new])
    with conn:
        for n, wid in enumerate(new, start=1):
            conn.execute("UPDATE widgets SET sort_order = ? WHERE id = ?", (n * 10, wid))


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
