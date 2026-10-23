from fastapi.testclient import TestClient

from app.main import create_app


def test_healthz(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}
