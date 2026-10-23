import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


@pytest.mark.parametrize(
    "page,names",
    [
        ("/admin", ["win95.css", "start.js"]),
        ("/admin/photos", ["win95.css", "admin.css", "admin.js"]),
        ("/admin/about", ["win95.css", "about.js"]),
        ("/", ["display.css", "display.js"]),
    ],
)
def test_assets_carry_file_version(client, page, names):
    html = client.get(page).text
    for name in names:
        version = int((STATIC / name).stat().st_mtime)
        url = f"/static/{name}?v={version}"
        assert url in html
        assert client.get(url).status_code == 200


def test_version_changes_when_file_changes(client):
    path = STATIC / "admin.css"
    before = path.stat()
    try:
        os.utime(path, (before.st_atime, before.st_mtime + 100))
        html = client.get("/admin/photos").text
        assert re.search(rf"admin\.css\?v={int(before.st_mtime) + 100}\b", html)
    finally:
        os.utime(path, (before.st_atime, before.st_mtime))
