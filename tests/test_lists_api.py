import pytest
from fastapi.testclient import TestClient

from app import db, events
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def _lists(client):
    return client.get("/api/lists").json()


def _list_id(client, name):
    return next(x["id"] for x in _lists(client) if x["name"] == name)


def _texts(client, list_id):
    lst = next(x for x in _lists(client) if x["id"] == list_id)
    return [i["text"] for i in lst["items"]]


def test_seed_lists_exist(client):
    body = _lists(client)
    assert [x["name"] for x in body] == ["Publix", "BJs"]
    assert all(x["kind"] == "checklist" for x in body)
    assert all(x["items"] == [] for x in body)
    assert all(x["show_on_tv"] is True for x in body)


def test_list_crud(client):
    events.last_event = None
    r = client.post("/api/lists", json={"name": "  To do  "})
    assert r.status_code == 201
    created = r.json()
    assert (created["name"], created["kind"]) == ("To do", "checklist")
    assert events.last_event == "lists_changed"

    lid = created["id"]
    r = client.patch(
        f"/api/lists/{lid}", json={"name": "Chores", "kind": "notes", "show_on_tv": False}
    )
    assert r.status_code == 200
    assert (r.json()["name"], r.json()["kind"], r.json()["show_on_tv"]) == (
        "Chores",
        "notes",
        False,
    )

    assert client.patch(f"/api/lists/{lid}", json={"kind": "other"}).status_code == 422
    assert client.patch(f"/api/lists/{lid}", json={"name": "x" * 61}).status_code == 422
    assert client.post("/api/lists", json={"name": ""}).status_code == 422
    assert client.patch("/api/lists/999", json={"name": "a"}).status_code == 404

    ids = [x["id"] for x in _lists(client)]
    r = client.put("/api/lists/order", json={"ids": list(reversed(ids))})
    assert r.status_code == 200
    assert [x["id"] for x in r.json()] == list(reversed(ids))
    assert client.put("/api/lists/order", json={"ids": [999]}).status_code == 422

    assert client.delete(f"/api/lists/{lid}").status_code == 204
    assert client.delete(f"/api/lists/{lid}").status_code == 404
    assert [x["name"] for x in _lists(client)] == ["BJs", "Publix"]


def test_item_crud(client):
    lid = _list_id(client, "Publix")
    r = client.post(f"/api/lists/{lid}/items", json={"text": "Milk"})
    assert r.status_code == 201
    item = r.json()
    assert (item["text"], item["done"], item["pinned"]) == ("Milk", False, False)
    assert _texts(client, lid) == ["Milk"]

    r = client.patch(f"/api/items/{item['id']}", json={"text": " Oat milk ", "done": True})
    assert r.status_code == 200
    assert (r.json()["text"], r.json()["done"]) == ("Oat milk", True)
    assert client.patch(f"/api/items/{item['id']}", json={"text": "x" * 201}).status_code == 422
    assert client.patch("/api/items/999", json={"done": True}).status_code == 404
    assert client.post("/api/lists/999/items", json={"text": "Eggs"}).status_code == 404

    assert client.delete(f"/api/items/{item['id']}").status_code == 204
    assert client.delete(f"/api/items/{item['id']}").status_code == 404
    assert _texts(client, lid) == []


def test_empty_text_is_422(client):
    lid = _list_id(client, "Publix")
    r = client.post(f"/api/lists/{lid}/items", json={"text": "  "})
    assert r.status_code == 422
    assert r.json()["detail"] == "text cannot be empty"
    assert _texts(client, lid) == []


def test_pinned_item_comes_first(client):
    lid = _list_id(client, "Publix")
    ids = [client.post(f"/api/lists/{lid}/items", json={"text": t}).json()["id"] for t in "abc"]
    assert _texts(client, lid) == ["a", "b", "c"]
    client.patch(f"/api/items/{ids[2]}", json={"pinned": True})
    assert _texts(client, lid) == ["c", "a", "b"]

    r = client.put(f"/api/lists/{lid}/items/order", json={"ids": [ids[1], ids[0]]})
    assert r.status_code == 200
    assert [i["text"] for i in r.json()["items"]] == ["c", "b", "a"]
    assert client.put(f"/api/lists/{lid}/items/order", json={"ids": [999]}).status_code == 422


def test_delete_list_cascades_items(client):
    lid = _list_id(client, "BJs")
    for t in ("Rice", "Beans"):
        client.post(f"/api/lists/{lid}/items", json={"text": t})
    assert client.delete(f"/api/lists/{lid}").status_code == 204
    conn = db.connect(client.app.state.db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM list_items WHERE list_id = ?", (lid,))
        assert count.fetchone()[0] == 0
    finally:
        conn.close()


def test_clear_done(client):
    lid = _list_id(client, "Publix")
    ids = [client.post(f"/api/lists/{lid}/items", json={"text": t}).json()["id"] for t in "abc"]
    client.patch(f"/api/items/{ids[0]}", json={"done": True})
    client.patch(f"/api/items/{ids[2]}", json={"done": True})
    r = client.post(f"/api/lists/{lid}/clear-done")
    assert r.status_code == 200
    assert [i["text"] for i in r.json()["items"]] == ["b"]
    assert client.post("/api/lists/999/clear-done").status_code == 404


def test_add_item_emits_lists_changed(client):
    lid = _list_id(client, "Publix")
    client.app.state.sse_limit = 1
    with client.stream("GET", "/api/events", headers={"Accept": "text/event-stream"}) as r:
        client.post(f"/api/lists/{lid}/items", json={"text": "Milk"})
        body = "".join(r.iter_text())
    assert "event: lists_changed" in body
