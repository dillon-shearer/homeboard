import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


@pytest.fixture
def slow_layout_read(monkeypatch):
    real = db.list_widgets

    def slow(conn, screen_id):
        rows = real(conn, screen_id)
        time.sleep(0.2)
        return rows

    monkeypatch.setattr(db, "list_widgets", slow)


def _empty(client):
    return client.post("/api/screens", json={"name": "Spare"}).json()["id"]


def _widgets(client, sid):
    screen = next(s for s in client.get("/api/screens").json() if s["id"] == sid)
    return screen["widgets"]


def test_two_adds_onto_the_same_cell_end_with_one_widget_and_one_422(client, slow_layout_read):
    sid = _empty(client)
    body = {"kind": "calendar", "col": 0, "row": 0, "w": 1, "h": 1}
    with ThreadPoolExecutor(2) as pool:
        jobs = [
            pool.submit(client.post, f"/api/screens/{sid}/widgets", json=body) for _ in range(2)
        ]
    assert sorted(j.result().status_code for j in jobs) == [201, 422]
    assert len(_widgets(client, sid)) == 1


def test_two_moves_onto_the_same_cell_never_overlap(client, slow_layout_read):
    sid = _empty(client)
    ids = []
    for col in (0, 1):
        body = {"kind": "calendar", "col": col, "row": 0, "w": 1, "h": 1}
        r = client.post(f"/api/screens/{sid}/widgets", json=body)
        ids.append(r.json()["widgets"][-1]["id"])
    with ThreadPoolExecutor(2) as pool:
        jobs = [
            pool.submit(client.patch, f"/api/widgets/{wid}", json={"col": 0, "row": 1})
            for wid in ids
        ]
    assert [j.result().status_code for j in jobs] == [200, 200]
    widgets = _widgets(client, sid)
    assert len(widgets) == 2
    db._check_layout(widgets)
