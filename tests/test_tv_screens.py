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


def test_list_items_keep_fields_and_tv_open(client):
    for x in _lists(client).values():
        assert set(x) == {
            "id",
            "name",
            "kind",
            "tv_open",
            "sort_order",
            "created_at",
            "items",
        }
        assert x["tv_open"] is True


def test_patch_list_tv_open(client):
    lid = _lists(client)["Publix"]["id"]
    events.last_event = None
    r = client.patch(f"/api/lists/{lid}", json={"tv_open": False})
    assert (r.status_code, r.json()["tv_open"]) == (200, False)
    assert "show_on_tv" not in r.json() and "screen_id" not in r.json()
    assert events.last_event == "lists_changed"
    assert _lists(client)["Publix"]["tv_open"] is False
    assert client.patch(f"/api/lists/{lid}", json={"tv_open": True}).json()["tv_open"] is True


def test_screens_crud_and_order(client):
    first = _screens(client)[0]
    events.last_event = None
    r = client.post("/api/screens", json={"name": "  To dos  "})
    assert r.status_code == 201
    created = r.json()
    assert set(created) == {"id", "name", "sort_order", "widgets"}
    assert (created["name"], created["widgets"]) == ("To dos", [])
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
    assert [s["id"] for s in r.json()][:2] == [created["id"], first["id"]]
    assert events.last_event == "lists_changed"
    assert client.put("/api/screens/order", json={"ids": [999]}).status_code == 422

    events.last_event = None
    assert client.delete(f"/api/screens/{created['id']}").status_code == 204
    assert events.last_event == "lists_changed"
    assert client.delete(f"/api/screens/{created['id']}").status_code == 404
    assert created["id"] not in [s["id"] for s in _screens(client)]


def test_new_list_is_not_placed_on_a_screen(client):
    before = _screens(client)
    r = client.post("/api/lists", json={"name": "Chores"})
    assert r.status_code == 201
    assert "screen_id" not in r.json()
    assert _screens(client) == before


def test_settings_screen_id(client):
    assert client.get("/api/settings").json()["screen_id"] is None
    assert client.get("/api/status").json()["screen_id"] is None
    sid = client.post("/api/screens", json={"name": "Notes"}).json()["id"]

    events.last_event = None
    r = client.put("/api/settings", json={"channel": "screen", "screen_id": sid})
    assert (r.status_code, r.json()["channel"], r.json()["screen_id"]) == (200, "screen", sid)
    assert events.last_event == "settings_changed"
    assert client.get("/api/settings").json()["screen_id"] == sid
    assert client.get("/api/status").json()["screen_id"] == sid

    assert client.put("/api/settings", json={"screen_id": 9999}).status_code == 422
    assert client.get("/api/settings").json()["screen_id"] == sid

    r = client.put("/api/settings", json={"screen_id": None})
    assert r.json()["screen_id"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"channel": "lists"},
        {"channel": "calendar"},
        {"lists_screen_id": 1},
    ],
)
def test_old_channel_settings_are_rejected(client, body):
    assert client.put("/api/settings", json=body).status_code == 422
    assert client.get("/api/settings").json()["channel"] == "photos"


def test_deleting_showing_screen_returns_to_photos(client):
    sid = client.post("/api/screens", json={"name": "Spare"}).json()["id"]
    client.post(f"/api/screens/{sid}/widgets", json={"kind": "clock"})
    client.put("/api/settings", json={"channel": "screen", "screen_id": sid})
    events.last_event = None
    seen = []
    publish = events.publish
    events.publish = lambda name: (seen.append(name), publish(name))
    try:
        assert client.delete(f"/api/screens/{sid}").status_code == 204
    finally:
        events.publish = publish
    assert seen == ["settings_changed", "lists_changed"]
    body = client.get("/api/settings").json()
    assert (body["channel"], body["screen_id"]) == ("photos", None)


def test_deleting_first_screen_while_showing_default_returns_to_photos(client):
    first = _screens(client)[0]["id"]
    client.put("/api/settings", json={"channel": "screen"})
    assert client.delete(f"/api/screens/{first}").status_code == 204
    assert client.get("/api/settings").json()["channel"] == "photos"


def test_deleting_other_screen_keeps_channel(client):
    screens = _screens(client)
    client.put("/api/settings", json={"channel": "screen", "screen_id": screens[0]["id"]})
    assert client.delete(f"/api/screens/{screens[1]['id']}").status_code == 204
    body = client.get("/api/settings").json()
    assert (body["channel"], body["screen_id"]) == ("screen", screens[0]["id"])


def test_db_screen_helpers(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    try:
        db.migrate(conn)
        a = db.list_screens(conn)[-1]
        b = db.create_screen(conn, "Spare")
        assert b["sort_order"] == a["sort_order"] + 10
        assert db.update_screen(conn, b["id"], name=" Spare 2 ")["name"] == "Spare 2"
        db.reorder_screens(conn, [b["id"]])
        assert db.list_screens(conn)[0]["id"] == b["id"]
        db.add_widget(conn, b["id"], "clock")
        assert db.delete_screen(conn, b["id"])["id"] == b["id"]
        assert db.get_screen(conn, b["id"]) is None
        assert (
            conn.execute("SELECT COUNT(*) FROM widgets WHERE screen_id = ?", (b["id"],)).fetchone()[
                0
            ]
            == 0
        )
    finally:
        conn.close()
