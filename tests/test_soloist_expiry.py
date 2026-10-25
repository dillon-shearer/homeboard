import pytest

from tests.test_helper import helperd


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
