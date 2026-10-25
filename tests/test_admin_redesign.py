from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent / "app"
JS = (ROOT / "static" / "admin.js").read_text()


@pytest.fixture
def page(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        r = c.get("/admin/photos")
        assert r.status_code == 200
        yield r.text


def test_photos_window_with_start_link_and_status_bar(page):
    assert '<body class="w95-desktop">' in page
    assert '<main class="w95-window">' in page
    assert '<span class="w95-title-text">Photos</span>' in page
    assert '<a class="w95-back" href="/admin">Start</a>' in page
    assert '<div class="w95-statusbar" role="status">' in page
    assert '"Display online"' in JS and '"Display offline"' in JS
    assert "status-dot" not in page and "status-dot" not in JS


def test_one_add_photos_button(page):
    assert page.count('type="file"') == 1
    assert '<label class="w95-btn upload-btn">' in page
    assert "Add Photos" in page


def test_grid_pages_photos(page):
    assert "photos.slice(0, shown)" in JS
    assert 'img.loading = "lazy"' in JS


def test_slideshow_settings_left_photos_for_settings_page(page, tmp_path):
    assert 'id="interval"' not in page and ">Slideshow<" not in page
    assert 'name="fit"' not in page and 'data-key="transition"' not in page
    with TestClient(create_app(tmp_path / "settings")) as c:
        page = c.get("/admin/settings").text
    page = page[page.index('aria-labelledby="group-slideshow"') : page.index("</section>")]
    assert 'class="w95-legend">Photos</h2>' in page
    for value in ("contain", "cover"):
        assert f'<input type="radio" name="fit" value="{value}">' in page
    for key in ("transition", "shuffle"):
        assert f'<input type="checkbox" data-key="{key}">' in page
    assert 'data-key="captions"' not in page
    for old in ('role="switch"', 'class="switch"', 'class="seg"'):
        assert old not in page
    for text in (
        "Whole photo",
        "Fill screen",
        "Fade between photos",
        "Random order",
    ):
        assert text in page
    for old in ("> Transition<", "> Shuffle<", "> Captions<", "> Contain<", "> Cover<"):
        assert old not in page
    assert 'class="hint">Off = hard cut<' in page


def test_photo_window_markup(page):
    dialog = page[page.index('id="photo-dialog"') : page.index('id="upload-dialog"')]
    assert 'role="dialog"' in dialog
    assert ">Photo</span>" in dialog
    assert 'type="checkbox"> Show on the board' in dialog
    assert "Delete this photo from the board and the Pi?" in dialog


def test_upload_progress_window(page):
    dialog = page[page.index('id="upload-dialog"') :]
    assert ">Uploading</span>" in dialog
    assert 'frame.className = "w95-progress"' in JS
    assert 'bar.className = "w95-progress-bar"' in JS
    assert '$("upload-close").disabled = false' in JS


def test_toast_is_a_message_box(page):
    assert 'role="alert"' in page
    assert "function showError(msg)" in JS
