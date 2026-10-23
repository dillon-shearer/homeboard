import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent / "app"
CSS = (ROOT / "static" / "admin.css").read_text()
JS = (ROOT / "static" / "admin.js").read_text()
HTML = (ROOT / "templates" / "admin.html").read_text()


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
    assert 'id="photo-count"' in page and 'id="display-state"' in page
    assert '"Display online"' in JS and '"Display offline"' in JS
    assert "status-dot" not in page and "status-dot" not in JS


def test_one_add_photos_button(page):
    assert page.count('type="file"') == 1
    assert '<label class="w95-btn upload-btn">' in page
    assert "Add Photos" in page


def test_grid_pages_photos(page):
    assert 'id="grid"' in page and 'id="show-more"' in page
    assert "photos.slice(0, shown)" in JS
    assert 'img.loading = "lazy"' in JS


def test_settings_are_radios_and_checkboxes(page):
    assert 'class="w95-group settings"' in page
    assert 'id="interval" type="number"' in page and 'class="w95-input"' in page
    assert 'id="interval-minus"' in page and 'id="interval-plus"' in page
    for value in ("contain", "cover"):
        assert f'<input type="radio" name="fit" value="{value}">' in page
    for key in ("transition", "shuffle", "captions"):
        assert f'<input type="checkbox" data-key="{key}">' in page
    for old in ('role="switch"', 'class="switch"', 'class="seg"'):
        assert old not in page
    for text in ("Whole photo", "Fill screen", "Fade between photos", "Random order", "Show captions on the TV"):
        assert text in page
    for old in ("> Transition<", "> Shuffle<", "> Captions<", "> Contain<", "> Cover<"):
        assert old not in page
    assert 'class="hint">Off = hard cut<' in page
    assert page.index('id="settings-error"') > page.index('id="group-slideshow"')


def test_settings_form_is_two_column_grid():
    assert "min-width: 120px" not in CSS
    assert "grid-template-columns: max-content 1fr" in CSS
    assert ".settings .row" not in CSS and ".settings .label" not in CSS


def test_sheet_is_gone():
    assert 'id="sheet' not in HTML
    assert ".sheet" not in CSS
    assert "sheet" not in JS


def test_photo_window_markup(page):
    dialog = page[page.index('id="photo-dialog"') : page.index('id="upload-dialog"')]
    assert 'role="dialog"' in dialog
    assert '<span id="photo-title" class="w95-title-text">Photo</span>' in dialog
    assert 'id="photo-close"' in dialog
    assert 'id="photo-thumb"' in dialog
    assert 'id="caption-input" class="w95-input" type="text"' in dialog
    assert '<input id="photo-enabled" type="checkbox"> Show on the board' in dialog
    for btn in ("photo-save", "photo-cancel", "photo-delete"):
        assert f'id="{btn}"' in dialog
    assert "Delete this photo from the board and the Pi?" in dialog
    assert 'id="confirm-delete"' in dialog and 'id="confirm-cancel"' in dialog


def test_photo_window_wiring():
    assert '$("photo-close").addEventListener("click", closeDialog)' in JS
    assert '$("photo-cancel").addEventListener("click", closeDialog)' in JS
    assert '$("photo-delete").addEventListener("click", openConfirm)' in JS
    assert '$("confirm-delete").addEventListener("click", deletePhoto)' in JS
    assert 'if (!$("photo-dialog").hidden) closeDialog()' in JS
    assert 'tile.querySelector(".tile-open").focus()' in JS
    assert "window.confirm(" not in JS


def test_upload_progress_window(page):
    dialog = page[page.index('id="upload-dialog"') :]
    assert '<span id="upload-title" class="w95-title-text">Uploading</span>' in dialog
    assert 'id="upload-list"' in dialog
    assert 'id="upload-close" class="w95-btn default" disabled' in dialog
    assert 'frame.className = "w95-progress"' in JS
    assert 'bar.className = "w95-progress-bar"' in JS
    assert '$("upload-close").disabled = false' in JS


def test_toast_is_a_message_box(page):
    assert 'id="toast" class="toast w95-window" role="alert" hidden' in page
    assert 'id="toast-text"' in page
    assert "function showError(msg)" in JS


def test_no_dark_mode_or_rounded_corners():
    for text in (CSS, HTML):
        assert "prefers-color-scheme" not in text
        assert "border-radius" not in text
        assert "-apple-system" not in text
    assert not re.search(r"--(bg|card|accent|danger)\b", CSS)
