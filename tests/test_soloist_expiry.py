import datetime

import pytest
from fastapi.responses import JSONResponse

from app import routes_sound
from tests.chrome import needs_chrome, server  # noqa: F401
from tests.test_helper import helperd
from tests.test_lists_page import _wait
from tests.test_sound_page import _fake, _open


@pytest.mark.parametrize(
    "text, expected",
    [("2027-01-21\n", "2027-01-21"), ("", None), ("soon\n", None)],
)
def test_helper_reads_the_expiry_file(tmp_path, monkeypatch, text, expected):
    path = tmp_path / "expires"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(helperd, "SOLOIST_EXPIRES", str(path))
    assert helperd.soloist_expires() == expected


def test_helper_without_expiry_file_says_none(tmp_path, monkeypatch):
    monkeypatch.setattr(helperd, "SOLOIST_EXPIRES", str(tmp_path / "missing"))
    assert helperd.soloist_expires() is None


def _expires_in(monkeypatch, days):
    date = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()

    def helper(method, path, timeout=5):
        return JSONResponse(
            {"speaker": {"connected": True, "name": "Soundbar"}, "soloist_expires": date}
        )

    monkeypatch.setattr(routes_sound, "helper", helper)
    return date


@needs_chrome
def test_sound_page_warns_before_the_build_runs_out(server, tmp_path, monkeypatch):  # noqa: F811
    _fake(monkeypatch)
    date = _expires_in(monkeypatch, 5)
    chrome = _open(server, tmp_path)
    try:
        text = _wait(
            chrome,
            "(w => !w.hidden && w.textContent)(document.getElementById('soloist-warning'))",
        )
    finally:
        chrome.close()
    assert date in text
    assert "sudo bash /opt/homeboard/deploy/install.sh --update-soloist" in text


@needs_chrome
def test_sound_page_has_no_warning_far_from_expiry(server, tmp_path, monkeypatch):  # noqa: F811
    _fake(monkeypatch)
    _expires_in(monkeypatch, 60)
    chrome = _open(server, tmp_path)
    try:
        _wait(
            chrome,
            "(t => t === 'Connected to Soundbar' && t)"
            "(document.getElementById('speaker-state').textContent)",
        )
        hidden = chrome.eval("document.getElementById('soloist-warning').hidden")
    finally:
        chrome.close()
    assert hidden is True
