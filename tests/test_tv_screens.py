import pytest
from fastapi.testclient import TestClient

from app import db, events
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def _lists(client):
    return {x["name"]: x for x in client.get("/api/lists").json()}


def _screens(client):
    return client.get("/api/screens").json()


def test_migration_seeds_lists_screen_from_show_on_tv(tmp_path):
    conn = db.connect(tmp_path / "homeboard.db")
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        for v in range(1, 7):
            sql = next(db.MIGRATIONS_DIR.glob(f"000{v}_*.sql"))
            conn.executescript(sql.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (v,))
        conn.execute("UPDATE lists SET show_on_tv = 0 WHERE name = 'BJs'")
        conn.commit()
    finally:
        conn.close()
    with TestClient(create_app(tmp_path)) as c:
        lists = _lists(c)
        screens = _screens(c)
        assert [(s["name"], s["list_ids"]) for s in screens] == [("Lists", [lists["Publix"]["id"]])]
        assert lists["Publix"]["screen_id"] == screens[0]["id"]
        assert lists["Publix"]["show_on_tv"] is True
        assert lists["BJs"]["screen_id"] is None
        assert lists["BJs"]["show_on_tv"] is False


def test_list_items_keep_fields_and_derive_show_on_tv(client):
    for x in _lists(client).values():
        assert set(x) == {
            "id",
            "name",
            "kind",
            "show_on_tv",
            "screen_id",
            "sort_order",
            "created_at",
            "items",
        }
        assert x["show_on_tv"] == (x["screen_id"] is not None)


def test_screens_crud_and_order(client):
    first = _screens(client)[0]
    events.last_event = None
    r = client.post("/api/screens", json={"name": "  To dos  "})
    assert r.status_code == 201
    created = r.json()
    assert set(created) == {"id", "name", "sort_order", "list_ids"}
    assert (created["name"], created["list_ids"]) == ("To dos", [])
    assert events.last_event == "lists_changed"
    assert client.post("/api/screens", json={"name": " "}).status_code == 422
    assert client.post("/api/screens", json={"name": "x" * 61}).status_code == 422

    events.last_event = None
    r = client.patch(f"/api/screens/{created['id']}", json={"name": "Chores"})
    assert (r.status_code, r.json()["name"]) == (200, "Chores")
    assert events.last_event == "lists_changed"
    assert client.patch("/api/screens/999", json={"name": "a"}).status_code == 404

    events.last_event = None
    r = client.put("/api/screens/order", json={"ids": [created["id"]]})
    assert r.status_code == 200
    assert [s["id"] for s in r.json()] == [created["id"], first["id"]]
    assert events.last_event == "lists_changed"
    assert client.put("/api/screens/order", json={"ids": [999]}).status_code == 422

    lid = _lists(client)["Publix"]["id"]
    client.patch(f"/api/lists/{lid}", json={"screen_id": created["id"]})
    events.last_event = None
    assert client.delete(f"/api/screens/{created['id']}").status_code == 204
    assert events.last_event == "lists_changed"
    assert client.delete(f"/api/screens/{created['id']}").status_code == 404
    assert _lists(client)["Publix"]["screen_id"] is None
    assert [s["id"] for s in _screens(client)] == [first["id"]]


def test_patch_list_screen_id(client):
    lid = _lists(client)["Publix"]["id"]
    other = client.post("/api/screens", json={"name": "Notes"}).json()

    events.last_event = None
    r = client.patch(f"/api/lists/{lid}", json={"screen_id": other["id"]})
    assert (r.status_code, r.json()["screen_id"]) == (200, other["id"])
    assert events.last_event == "lists_changed"
    assert lid in next(s for s in _screens(client) if s["id"] == other["id"])["list_ids"]

    assert client.patch(f"/api/lists/{lid}", json={"screen_id": 999}).status_code == 422
    assert _lists(client)["Publix"]["screen_id"] == other["id"]

    r = client.patch(f"/api/lists/{lid}", json={"screen_id": None})
    assert (r.json()["screen_id"], r.json()["show_on_tv"]) == (None, False)

    r = client.patch(f"/api/lists/{lid}", json={"show_on_tv": True})
    assert (r.json()["screen_id"], r.json()["show_on_tv"]) == (_screens(client)[0]["id"], True)
    r = client.patch(f"/api/lists/{lid}", json={"show_on_tv": False})
    assert r.json()["screen_id"] is None


def test_show_on_tv_true_creates_lists_screen_when_none(client):
    lid = _lists(client)["Publix"]["id"]
    for s in _screens(client):
        client.delete(f"/api/screens/{s['id']}")
    r = client.patch(f"/api/lists/{lid}", json={"show_on_tv": True})
    screens = _screens(client)
    assert [(s["name"], s["list_ids"]) for s in screens] == [("Lists", [lid])]
    assert r.json()["screen_id"] == screens[0]["id"]


def test_new_list_goes_on_first_screen(client):
    first = _screens(client)[0]
    r = client.post("/api/lists", json={"name": "Chores"})
    assert r.json()["screen_id"] == first["id"]


def test_settings_lists_screen_id(client):
    assert client.get("/api/settings").json()["lists_screen_id"] is None
    assert client.get("/api/status").json()["lists_screen_id"] is None
    sid = client.post("/api/screens", json={"name": "Notes"}).json()["id"]

    events.last_event = None
    r = client.put("/api/settings", json={"lists_screen_id": sid})
    assert (r.status_code, r.json()["lists_screen_id"]) == (200, sid)
    assert events.last_event == "settings_changed"
    assert client.get("/api/settings").json()["lists_screen_id"] == sid
    assert client.get("/api/status").json()["lists_screen_id"] == sid

    assert client.put("/api/settings", json={"lists_screen_id": 999}).status_code == 422
    assert client.get("/api/settings").json()["lists_screen_id"] == sid

    r = client.put("/api/settings", json={"lists_screen_id": None})
    assert r.json()["lists_screen_id"] is None


def test_deleting_current_screen_clears_settings_pointer(client):
    sid = client.post("/api/screens", json={"name": "Notes"}).json()["id"]
    client.put("/api/settings", json={"lists_screen_id": sid})
    assert client.delete(f"/api/screens/{sid}").status_code == 204
    assert client.get("/api/settings").json()["lists_screen_id"] is None


def test_db_screen_helpers(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    try:
        db.migrate(conn)
        a = db.list_screens(conn)[0]
        b = db.create_screen(conn, "Notes")
        assert b["sort_order"] == a["sort_order"] + 10
        assert db.update_screen(conn, b["id"], name=" Notes 2 ")["name"] == "Notes 2"
        db.reorder_screens(conn, [b["id"]])
        assert [s["id"] for s in db.list_screens(conn)] == [b["id"], a["id"]]
        lid = db.list_lists(conn)[0]["id"]
        db.update_list(conn, lid, screen_id=b["id"])
        assert db.screen_list_ids(conn, b["id"]) == [lid]
        with pytest.raises(ValueError):
            db.update_list(conn, lid, screen_id=999)
        assert db.delete_screen(conn, b["id"])["id"] == b["id"]
        assert db.get_list(conn, lid)["screen_id"] is None
        assert db.get_screen(conn, b["id"]) is None
    finally:
        conn.close()
