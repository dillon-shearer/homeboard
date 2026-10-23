from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent / "app"
CSS = (ROOT / "static" / "admin.css").read_text()
JS = (ROOT / "static" / "admin.js").read_text()


@pytest.fixture
def page(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c.get("/admin/photos").text


def test_status_dot_is_gone(page):
    assert "status-dot" not in page
    assert "status-dot" not in JS
    assert ".dot" not in CSS
    assert 'id="offline" class="offline" hidden' in page


def test_one_primary_add_button(page):
    assert page.count('type="file"') == 1
    assert "Add Photos" in page


def test_in_page_delete_confirm(page):
    assert 'id="sheet-confirm"' in page and 'id="confirm-delete"' in page
    assert 'del.className = "tile-delete"' in JS
    assert 'openConfirm(tile, btn)' in JS


def test_grid_pages_photos():
    assert "photos.slice(0, shown)" in JS
    assert 'img.loading = "lazy"' in JS
    assert '"show-more"' in JS or "show-more" in JS


def test_system_font_and_dark_mode():
    assert "-apple-system" in CSS
    assert "@media (prefers-color-scheme: dark)" in CSS


def test_settings_controls(page):
    for key in ("transition", "shuffle", "captions"):
        assert f'class="switch" role="switch" data-key="{key}"' in page
    assert 'class="seg" data-key="fit"' in page
    assert 'id="interval"' in page
