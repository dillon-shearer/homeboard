from fastapi.testclient import TestClient

from app.main import create_app


def test_healthz(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_healthz_allows_the_file_page_to_read_it(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        r = client.get("/healthz", headers={"Origin": "null"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "*"
