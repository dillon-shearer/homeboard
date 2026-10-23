import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db, events
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def _screens(client):
    return client.get("/api/screens").json()


def _empty(client):
    return client.post("/api/screens", json={"name": "Spare"}).json()["id"]


def _add(client, sid, **body):
    return client.post(f"/api/screens/{sid}/widgets", json={"kind": "clock", **body})


def _shape(screen):
    return [(w["w"], w["h"], w["col"], w["row"]) for w in screen["widgets"]]


def test_fresh_data_dir_seeds_three_screens(client):
    screens = _screens(client)
    assert [s["name"] for s in screens] == ["Daily Brief", "Calendar", "Notes"]
    publix = client.get("/api/lists").json()[0]
    brief, cal, notes = screens
    assert [
        (w["kind"], w["list_id"], w["w"], w["h"], w["col"], w["row"]) for w in brief["widgets"]
    ] == [
        ("clock", None, 1, 1, 0, 0),
        ("weather", None, 1, 1, 1, 0),
        ("calendar", None, 1, 1, 0, 1),
        ("lists", publix["id"], 1, 1, 1, 1),
    ]
    assert [(w["kind"], w["w"], w["h"], w["col"], w["row"]) for w in cal["widgets"]] == [
        ("calendar", 2, 2, 0, 0)
    ]
    assert [(w["kind"], w["w"], w["h"], w["col"], w["row"]) for w in notes["widgets"]] == [
        ("lists", 2, 2, 0, 0)
    ]
    for s in screens:
        for w in s["widgets"]:
            assert set(w) == {"id", "kind", "list_id", "w", "h", "sort_order", "col", "row"}
        assert "list_ids" not in s


@pytest.mark.parametrize(
    "sizes,placed",
    [
        ([(1, 1)] * 4, [(0, 0), (1, 0), (0, 1), (1, 1)]),
        ([(2, 1), (2, 1)], [(0, 0), (0, 1)]),
        ([(1, 2), (1, 2)], [(0, 0), (1, 0)]),
        ([(2, 1), (1, 1), (1, 1)], [(0, 0), (0, 1), (1, 1)]),
        ([(1, 1), (1, 1), (2, 1)], [(0, 0), (1, 0), (0, 1)]),
        ([(1, 2), (1, 1), (1, 1)], [(0, 0), (1, 0), (1, 1)]),
        ([(1, 1), (1, 2), (1, 1)], [(0, 0), (1, 0), (0, 1)]),
        ([(2, 2)], [(0, 0)]),
        ([], []),
    ],
)
def test_pack_widgets_valid(sizes, placed):
    widgets = [{"id": n, "w": w, "h": h} for n, (w, h) in enumerate(sizes)]
    out = db.pack_widgets(widgets)
    assert [(p["col"], p["row"]) for p in out] == placed
    assert [(p["id"], p["w"], p["h"]) for p in out] == [(n, w, h) for n, (w, h) in enumerate(sizes)]


@pytest.mark.parametrize(
    "sizes",
    [
        [(2, 1), (1, 2)],
        [(1, 1), (2, 1)],
        [(1, 1), (1, 1), (1, 2)],
        [(2, 2), (1, 1)],
        [(1, 1)] * 5,
        [(1, 2), (2, 1)],
    ],
)
def test_pack_widgets_invalid(sizes):
    with pytest.raises(ValueError):
        db.pack_widgets([{"id": n, "w": w, "h": h} for n, (w, h) in enumerate(sizes)])


def test_default_size_on_empty_screen_is_full_and_second_is_422(client):
    sid = _empty(client)
    events.last_event = None
    r = _add(client, sid, kind="calendar")
    assert r.status_code == 201
    assert _shape(r.json()) == [(2, 2, 0, 0)]
    assert r.json()["widgets"][0]["kind"] == "calendar"
    assert events.last_event == "lists_changed"
    assert _add(client, sid).status_code == 422
    assert len(next(s for s in _screens(client) if s["id"] == sid)["widgets"]) == 1


def test_default_size_takes_free_row_or_column_else_quarter(client):
    sid = _empty(client)
    _add(client, sid, w=2, h=1)
    assert _shape(_add(client, sid).json())[-1] == (2, 1, 0, 1)

    sid = _empty(client)
    _add(client, sid, w=1, h=1)
    assert _shape(_add(client, sid).json())[-1] == (1, 2, 1, 0)
    assert _shape(_add(client, sid).json())[-1] == (1, 1, 0, 1)
    assert _add(client, sid).status_code == 422


def test_wide_then_two_quarters_packs_and_wide_then_tall_is_422(client):
    sid = _empty(client)
    assert _add(client, sid, w=2, h=1).status_code == 201
    assert _add(client, sid, w=1, h=1).status_code == 201
    r = _add(client, sid, w=1, h=1)
    assert r.status_code == 201
    assert _shape(r.json()) == [(2, 1, 0, 0), (1, 1, 0, 1), (1, 1, 1, 1)]

    sid = _empty(client)
    assert _add(client, sid, w=2, h=1).status_code == 201
    assert _add(client, sid, w=1, h=2).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "radio"},
        {"kind": "clock", "w": 3, "h": 1},
        {"kind": "clock", "w": 0},
        {"kind": "clock", "list_id": 1},
        {"kind": "lists", "list_id": 9999},
    ],
)
def test_add_widget_rejects_bad_input(client, body):
    sid = _empty(client)
    assert client.post(f"/api/screens/{sid}/widgets", json=body).status_code == 422


def test_add_widget_to_missing_screen_is_404(client):
    assert _add(client, 9999).status_code == 404


def test_patch_resize_that_does_not_pack_is_422_and_unchanged(client):
    brief = _screens(client)[0]
    clock = brief["widgets"][0]
    events.last_event = None
    r = client.patch(f"/api/widgets/{clock['id']}", json={"w": 2})
    assert r.status_code == 422
    assert events.last_event is None
    after = next(w for w in _screens(client)[0]["widgets"] if w["id"] == clock["id"])
    assert after == clock


def test_patch_resize_and_list_id(client):
    sid = _empty(client)
    wid = _add(client, sid, kind="lists", w=1, h=1).json()["widgets"][0]["id"]
    lid = client.get("/api/lists").json()[1]["id"]
    r = client.patch(f"/api/widgets/{wid}", json={"w": 2, "h": 2, "list_id": lid})
    assert r.status_code == 200
    assert (r.json()["widgets"][0]["w"], r.json()["widgets"][0]["h"]) == (2, 2)
    assert r.json()["widgets"][0]["list_id"] == lid
    r = client.patch(f"/api/widgets/{wid}", json={"list_id": None})
    assert r.json()["widgets"][0]["list_id"] is None
    assert client.patch(f"/api/widgets/{wid}", json={"list_id": 9999}).status_code == 422
    assert client.patch(f"/api/widgets/{wid}", json={"h": 3}).status_code == 422
    assert client.patch("/api/widgets/9999", json={"w": 1}).status_code == 404


def test_reorder_widgets(client):
    sid = _empty(client)
    a = _add(client, sid, w=2, h=1).json()["widgets"][0]["id"]
    b = _add(client, sid, w=1, h=1).json()["widgets"][1]["id"]
    c = _add(client, sid, w=1, h=1).json()["widgets"][2]["id"]
    events.last_event = None
    r = client.put(f"/api/screens/{sid}/widgets/order", json={"ids": [b, c, a]})
    assert r.status_code == 200
    assert [w["id"] for w in r.json()["widgets"]] == [b, c, a]
    assert _shape(r.json()) == [(1, 1, 0, 0), (1, 1, 1, 0), (2, 1, 0, 1)]
    assert events.last_event == "lists_changed"
    assert client.put(f"/api/screens/{sid}/widgets/order", json={"ids": [b, a]}).status_code == 422
    assert client.put(f"/api/screens/{sid}/widgets/order", json={"ids": [999]}).status_code == 422
    after = next(s for s in _screens(client) if s["id"] == sid)
    assert [w["id"] for w in after["widgets"]] == [b, c, a]


def test_delete_widget_repacks_when_the_rest_would_not_fit(client):
    sid = _empty(client)
    a = _add(client, sid, w=1, h=1).json()["widgets"][0]["id"]
    _add(client, sid, w=1, h=1)
    wide = _add(client, sid, w=2, h=1).json()["widgets"][2]["id"]
    events.last_event = None
    assert client.delete(f"/api/widgets/{a}").status_code == 204
    assert events.last_event == "lists_changed"
    after = next(s for s in _screens(client) if s["id"] == sid)
    assert after["widgets"][0]["id"] == wide
    assert _shape(after) == [(2, 1, 0, 0), (1, 1, 0, 1)]
    assert client.delete(f"/api/widgets/{a}").status_code == 404


def test_deleting_a_list_removes_its_widget_and_keeps_screens_valid(client):
    sid = _empty(client)
    lid = client.post("/api/lists", json={"name": "Chores"}).json()["id"]
    _add(client, sid, kind="lists", w=1, h=1, list_id=lid)
    _add(client, sid, w=1, h=1)
    _add(client, sid, w=2, h=1)
    assert client.delete(f"/api/lists/{lid}").status_code == 204
    after = next(s for s in _screens(client) if s["id"] == sid)
    assert _shape(after) == [(2, 1, 0, 0), (1, 1, 0, 1)]


def test_delete_screen_removes_widgets(client):
    sid = _screens(client)[1]["id"]
    wid = _screens(client)[1]["widgets"][0]["id"]
    assert client.delete(f"/api/screens/{sid}").status_code == 204
    assert client.patch(f"/api/widgets/{wid}", json={"w": 1}).status_code == 404


def test_weather_location_is_trimmed_and_clears_resolved_place(client, tmp_path):
    body = client.get("/api/settings").json()
    assert (body["weather_location"], body["weather_lat"]) == ("30263", None)
    conn = db.connect(tmp_path / "homeboard.db")
    try:
        with conn:
            conn.execute(
                "UPDATE settings SET weather_lat = 33.4, weather_lon = -84.8,"
                " weather_name = 'Newnan'"
            )
    finally:
        conn.close()
    events.last_event = None
    r = client.put("/api/settings", json={"weather_location": " 30263 "})
    assert r.status_code == 200
    body = client.get("/api/settings").json()
    assert body["weather_location"] == "30263"
    assert (body["weather_lat"], body["weather_lon"], body["weather_name"]) == (None, None, None)
    assert events.last_event == "settings_changed"
    assert client.put("/api/settings", json={"weather_location": " "}).status_code == 422
    assert client.put("/api/settings", json={"weather_location": "x" * 41}).status_code == 422
    assert client.put("/api/settings", json={"weather_location": "x" * 40}).status_code == 200


def _db_at_version_8(path):
    conn = db.connect(path)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        for v in range(1, 9):
            sql = next(db.MIGRATIONS_DIR.glob(f"{v:04d}_*.sql"))
            conn.executescript(sql.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (v,))
        conn.execute(
            "UPDATE settings SET interval_s = 45, fit = 'cover', captions = 1,"
            " clock_pos = 'top-left', clock_size = 9, channel = 'calendar',"
            " channel_timeout_min = 10, current_photo_id = 3"
        )
        conn.execute("UPDATE lists SET sort_order = 5 WHERE name = 'BJs'")
        conn.execute(
            "INSERT INTO tv_screens (name, sort_order, created_at) VALUES ('Old', 20, 'x')"
        )
        conn.execute(
            "INSERT INTO list_items (list_id, text, sort_order, created_at)"
            " SELECT id, 'milk', 10, 'x' FROM lists WHERE name = 'Publix'"
        )
        conn.commit()
    finally:
        conn.close()


def test_migration_keeps_settings_and_lists(tmp_path):
    _db_at_version_8(tmp_path / "homeboard.db")
    with TestClient(create_app(tmp_path)) as c:
        s = c.get("/api/settings").json()
        assert (s["interval_s"], s["fit"], s["captions"]) == (45, "cover", True)
        assert (s["clock_pos"], s["clock_size"], s["channel_timeout_min"]) == ("top-left", 9, 10)
        assert c.get("/api/display/current").json()["id"] == 3
        screens = c.get("/api/screens").json()
        assert [x["name"] for x in screens] == ["Daily Brief", "Calendar", "Notes"]
        assert (s["channel"], s["screen_id"]) == ("screen", screens[1]["id"])
        lists = c.get("/api/lists").json()
        assert [x["name"] for x in lists] == ["BJs", "Publix"]
        assert [i["text"] for i in lists[1]["items"]] == ["milk"]
        assert all(x["tv_open"] is True for x in lists)
        assert screens[0]["widgets"][3]["list_id"] == lists[0]["id"]
        assert s["weather_location"] == "30263"


@pytest.mark.parametrize("old,new", [("photos", "photos"), ("lists", "screen")])
def test_migration_maps_old_channels(tmp_path, old, new):
    _db_at_version_8(tmp_path / "homeboard.db")
    conn = db.connect(tmp_path / "homeboard.db")
    try:
        with conn:
            conn.execute("UPDATE settings SET channel = ?", (old,))
        assert db.migrate(conn) == 9
        s = db.get_settings(conn)
        expected = None if old == "photos" else db.list_screens(conn)[2]["id"]
        assert (s["channel"], s["screen_id"]) == (new, expected)
        with pytest.raises(sqlite3.IntegrityError), conn:
            conn.execute("UPDATE settings SET channel = 'lists'")
    finally:
        conn.close()


def test_migration_with_no_lists_seeds_empty_list_widget(tmp_path):
    conn = db.connect(tmp_path / "homeboard.db")
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        for v in range(1, 9):
            sql = next(db.MIGRATIONS_DIR.glob(f"{v:04d}_*.sql"))
            conn.executescript(sql.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (v,))
        conn.execute("DELETE FROM lists")
        conn.commit()
        db.migrate(conn)
        brief = db.list_screens(conn)[0]
        assert db.list_widgets(conn, brief["id"])[3]["list_id"] is None
    finally:
        conn.close()
