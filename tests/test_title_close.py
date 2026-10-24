import re

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

SUB_PAGES = ["/admin/photos", "/admin/calendar", "/admin/lists", "/admin/tv", "/admin/about"]


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def titlebar(page):
    return re.search(r'<main class="w95-window">\s*<div class="w95-titlebar">(.*?)</div>', page, re.S).group(1)


@pytest.mark.parametrize("path", SUB_PAGES)
def test_sub_page_title_close_links_to_start(client, path):
    bar = titlebar(client.get(path).text)
    assert '<a class="title-close" href="/admin" aria-label="Close"><span class="w95-close" aria-hidden="true"></span></a>' in bar
    assert bar.count("w95-close") == 1


def test_start_screen_has_no_title_close(client):
    bar = titlebar(client.get("/admin").text)
    assert "w95-close" not in bar
    assert "title-close" not in bar
