import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_api_docs_are_off(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/admin", "/admin/photos", "/admin/about"])
def test_admin_pages_have_no_docs_link(client, path):
    page = client.get(path).text
    assert "/docs" not in page and "/redoc" not in page and "/openapi.json" not in page
