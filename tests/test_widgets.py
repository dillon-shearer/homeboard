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


def _screen(client, sid):
    return next(s for s in _screens(client) if s["id"] == sid)


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


def test_widget_sizes_lists_allowed_sizes_per_kind(client):
    assert client.get("/api/widget-sizes").json() == {
        "clock": [[2, 1], [1, 1]],
        "weather": [[2, 2], [2, 1], [1, 2], [1, 1]],
        "calendar": [[2, 2], [2, 1], [1, 2], [1, 1]],
        "lists": [[2, 2], [2, 1], [1, 2], [1, 1]],
        "spotify": [[2, 2], [2, 1], [1, 2], [1, 1]],
    }


def test_default_size_on_empty_screen_is_full_and_second_is_422(client):
    sid = _empty(client)
    events.last_event = None
    r = _add(client, sid, kind="calendar", col=0, row=0)
    assert r.status_code == 201
    assert _shape(r.json()) == [(2, 2, 0, 0)]
    assert r.json()["widgets"][0]["kind"] == "calendar"
    assert events.last_event == "lists_changed"
    assert _add(client, sid, col=1, row=1).status_code == 422
    assert len(_screen(client, sid)["widgets"]) == 1


@pytest.mark.parametrize(
    "kind,col,row,shape",
    [
        ("weather", 0, 0, (2, 2, 0, 0)),
        ("clock", 0, 0, (2, 1, 0, 0)),
        ("clock", 0, 1, (2, 1, 0, 1)),
        ("lists", 1, 0, (1, 2, 1, 0)),
        ("calendar", 1, 1, (1, 1, 1, 1)),
    ],
)
def test_add_at_a_cell_picks_largest_allowed_size_that_fits(client, kind, col, row, shape):
    sid = _empty(client)
    assert _shape(_add(client, sid, kind=kind, col=col, row=row).json()) == [shape]


def test_wide_on_bottom_row_next_to_a_quarter_in_the_first_cell(client):
    sid = _empty(client)
    assert _add(client, sid, col=0, row=0, w=1, h=1).status_code == 201
    r = _add(client, sid, kind="weather", col=0, row=1)
    assert r.status_code == 201
    assert _shape(r.json()) == [(1, 1, 0, 0), (2, 1, 0, 1)]
    r = _add(client, sid, kind="calendar", col=1, row=0)
    assert _shape(r.json())[-1] == (1, 1, 1, 0)
    assert _add(client, sid, col=1, row=1).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"col": 0, "row": 0, "w": 1, "h": 1},
        {"col": 1, "row": 0, "w": 1, "h": 2},
        {"col": 0, "row": 0, "w": 2, "h": 1},
        {"col": 1, "row": 1},
    ],
)
def test_add_that_overlaps_is_422(client, body):
    sid = _empty(client)
    _add(client, sid, kind="weather", col=0, row=0, w=1, h=2)
    _add(client, sid, kind="weather", col=1, row=1, w=1, h=1)
    events.last_event = None
    assert _add(client, sid, kind="lists", **body).status_code == 422
    assert events.last_event is None
    assert _shape(_screen(client, sid)) == [(1, 2, 0, 0), (1, 1, 1, 1)]


@pytest.mark.parametrize(
    "body",
    [
        {"col": 1, "row": 0, "w": 2, "h": 1},
        {"col": 0, "row": 1, "w": 1, "h": 2},
        {"col": 2, "row": 0},
        {"col": 0, "row": 2},
        {"col": -1, "row": 0},
    ],
)
def test_add_outside_the_grid_is_422(client, body):
    sid = _empty(client)
    assert _add(client, sid, kind="weather", **body).status_code == 422
    assert _screen(client, sid)["widgets"] == []


@pytest.mark.parametrize("size", [(2, 2), (1, 2)])
def test_clock_sizes_not_allowed_are_422_on_add_and_patch(client, size):
    sid = _empty(client)
    w, h = size
    assert _add(client, sid, col=0, row=0, w=w, h=h).status_code == 422
    wid = _add(client, sid, col=0, row=0, w=1, h=1).json()["widgets"][0]["id"]
    assert client.patch(f"/api/widgets/{wid}", json={"w": w, "h": h}).status_code == 422
    assert _shape(_screen(client, sid)) == [(1, 1, 0, 0)]


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "radio", "col": 0, "row": 0},
        {"kind": "clock", "col": 0, "row": 0, "w": 3, "h": 1},
        {"kind": "clock", "col": 0, "row": 0, "w": 0},
        {"kind": "clock", "col": 0, "row": 0, "list_id": 1},
        {"kind": "lists", "col": 0, "row": 0, "list_id": 9999},
        {"kind": "clock"},
        {"kind": "clock", "col": 0},
    ],
)
def test_add_widget_rejects_bad_input(client, body):
    sid = _empty(client)
    assert client.post(f"/api/screens/{sid}/widgets", json=body).status_code == 422


def test_add_widget_to_missing_screen_is_404(client):
    assert _add(client, 9999, col=0, row=0).status_code == 404


def test_patch_resize_that_overlaps_is_422_and_unchanged(client):
    brief = _screens(client)[0]
    clock = brief["widgets"][0]
    events.last_event = None
    r = client.patch(f"/api/widgets/{clock['id']}", json={"w": 2})
    assert r.status_code == 422
    assert events.last_event is None
    after = next(w for w in _screens(client)[0]["widgets"] if w["id"] == clock["id"])
    assert after == clock


def test_patch_resize_keeps_cell_and_must_fit(client):
    sid = _empty(client)
    wid = _add(client, sid, kind="weather", col=1, row=0, w=1, h=1).json()["widgets"][0]["id"]
    r = client.patch(f"/api/widgets/{wid}", json={"h": 2})
    assert _shape(r.json()) == [(1, 2, 1, 0)]
    assert client.patch(f"/api/widgets/{wid}", json={"w": 2}).status_code == 422


def test_patch_resize_and_list_id(client):
    sid = _empty(client)
    wid = _add(client, sid, kind="lists", col=0, row=0, w=1, h=1).json()["widgets"][0]["id"]
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


def test_patch_moves_to_an_empty_area(client):
    sid = _empty(client)
    a = _add(client, sid, col=0, row=0, w=1, h=1).json()["widgets"][0]["id"]
    _add(client, sid, kind="weather", col=1, row=0, w=1, h=1)
    events.last_event = None
    r = client.patch(f"/api/widgets/{a}", json={"col": 0, "row": 1})
    assert r.status_code == 200
    assert _shape(r.json()) == [(1, 1, 0, 1), (1, 1, 1, 0)]
    assert events.last_event == "lists_changed"
    r = client.patch(f"/api/widgets/{a}", json={"col": 1})
    assert _shape(r.json()) == [(1, 1, 1, 1), (1, 1, 1, 0)]
    r = client.patch(f"/api/widgets/{a}", json={"col": 0, "row": 0, "w": 2})
    assert r.status_code == 422
    r = client.patch(f"/api/widgets/{a}", json={"col": 0, "row": 1, "w": 2})
    assert _shape(r.json()) == [(2, 1, 0, 1), (1, 1, 1, 0)]
    assert client.patch(f"/api/widgets/{a}", json={"col": 1, "row": 1}).status_code == 422


def test_patch_onto_another_widget_swaps_the_two(client):
    sid = _empty(client)
    wide = _add(client, sid, kind="weather", col=0, row=0, w=2, h=1).json()["widgets"][0]["id"]
    quarter = _add(client, sid, col=0, row=1, w=1, h=1).json()["widgets"][1]["id"]
    events.last_event = None
    r = client.patch(f"/api/widgets/{quarter}", json={"col": 0, "row": 0})
    assert r.status_code == 200
    assert {w["id"]: (w["col"], w["row"]) for w in r.json()["widgets"]} == {
        wide: (0, 1),
        quarter: (0, 0),
    }
    assert events.last_event == "lists_changed"


def test_patch_swap_that_does_not_fit_is_422_and_unchanged(client):
    sid = _empty(client)
    _add(client, sid, kind="lists", col=0, row=0, w=1, h=2)
    a = _add(client, sid, col=1, row=0, w=1, h=1).json()["widgets"][1]["id"]
    _add(client, sid, col=1, row=1, w=1, h=1)
    before = _screen(client, sid)
    assert client.patch(f"/api/widgets/{a}", json={"col": 0, "row": 0}).status_code == 422
    assert _screen(client, sid) == before


def test_patch_onto_part_of_a_bigger_widget_is_422(client):
    sid = _empty(client)
    _add(client, sid, kind="weather", col=0, row=0, w=2, h=1)
    a = _add(client, sid, col=0, row=1, w=1, h=1).json()["widgets"][1]["id"]
    assert client.patch(f"/api/widgets/{a}", json={"col": 1, "row": 0}).status_code == 422
    assert _shape(_screen(client, sid)) == [(2, 1, 0, 0), (1, 1, 0, 1)]


def test_widgets_order_endpoint_is_gone(client):
    sid = _empty(client)
    r = client.put(f"/api/screens/{sid}/widgets/order", json={"ids": []})
    assert r.status_code in (404, 405)
    assert not hasattr(db, "pack_widgets")
    assert not hasattr(db, "reorder_widgets")


def test_delete_widget_leaves_the_others_in_place(client):
    sid = _empty(client)
    a = _add(client, sid, col=0, row=0, w=1, h=1).json()["widgets"][0]["id"]
    _add(client, sid, kind="weather", col=1, row=0, w=1, h=1)
    wide = _add(client, sid, kind="calendar", col=0, row=1, w=2, h=1).json()["widgets"][2]["id"]
    events.last_event = None
    assert client.delete(f"/api/widgets/{a}").status_code == 204
    assert events.last_event == "lists_changed"
    after = _screen(client, sid)
    assert after["widgets"][1]["id"] == wide
    assert _shape(after) == [(1, 1, 1, 0), (2, 1, 0, 1)]
    assert client.delete(f"/api/widgets/{a}").status_code == 404


def test_deleting_a_list_removes_its_widget_and_leaves_the_others(client):
    sid = _empty(client)
    lid = client.post("/api/lists", json={"name": "Chores"}).json()["id"]
    _add(client, sid, kind="lists", col=0, row=0, w=1, h=1, list_id=lid)
    _add(client, sid, col=1, row=0, w=1, h=1)
    _add(client, sid, kind="weather", col=0, row=1, w=2, h=1)
    assert client.delete(f"/api/lists/{lid}").status_code == 204
    assert _shape(_screen(client, sid)) == [(1, 1, 1, 0), (2, 1, 0, 1)]


def _db_at_version_12(path, layouts):
    conn = db.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        for v in range(1, 13):
            sql = next(db.MIGRATIONS_DIR.glob(f"{v:04d}_*.sql"))
            conn.executescript(sql.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (v,))
        ids = []
        for n, (name, sizes) in enumerate(layouts.items()):
            sid = conn.execute(
                "INSERT INTO tv_screens (name, sort_order, created_at) VALUES (?, ?, 'x')",
                (name, 100 + n),
            ).lastrowid
            ids.append(sid)
            for order, (kind, w, h) in sizes:
                conn.execute(
                    "INSERT INTO widgets (screen_id, kind, w, h, sort_order, created_at)"
                    " VALUES (?, ?, ?, ?, ?, 'x')",
                    (sid, kind, w, h, order),
                )
        conn.commit()
        return ids
    finally:
        conn.close()


def test_migration_keeps_each_packed_layout(tmp_path):
    layouts = {
        "Wide top": [(10, ("clock", 2, 1)), (20, ("weather", 1, 1)), (30, ("lists", 1, 1))],
        "Tall middle": [(30, ("clock", 1, 1)), (10, ("weather", 1, 1)), (20, ("lists", 1, 2))],
        "Tall left": [(10, ("calendar", 1, 2)), (20, ("clock", 1, 1)), (30, ("weather", 1, 1))],
        "Empty": [],
    }
    ids = _db_at_version_12(tmp_path / "homeboard.db", layouts)
    conn = db.connect(tmp_path / "homeboard.db")
    try:
        assert db.migrate(conn) == 17
        cols = {r[1] for r in conn.execute("PRAGMA table_info(widgets)")}
        assert {"col", "row"} <= cols

        def placed(sid):
            return [(w["kind"], w["col"], w["row"]) for w in db.list_widgets(conn, sid)]

        assert placed(ids[0]) == [("clock", 0, 0), ("weather", 0, 1), ("lists", 1, 1)]
        assert placed(ids[1]) == [("weather", 0, 0), ("lists", 1, 0), ("clock", 0, 1)]
        assert placed(ids[2]) == [("calendar", 0, 0), ("clock", 1, 0), ("weather", 1, 1)]
        assert placed(ids[3]) == []
        brief = db.list_screens(conn)[0]
        assert [(w["col"], w["row"]) for w in db.list_widgets(conn, brief["id"])] == [
            (0, 0),
            (1, 0),
            (0, 1),
            (1, 1),
        ]
    finally:
        conn.close()


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
        assert db.migrate(conn) == 17
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
