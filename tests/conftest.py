import pytest

from app import spotify
from tests.chrome import browser, chrome, server  # noqa: F401


@pytest.fixture(autouse=True)
def _no_spotify_settle(monkeypatch):
    monkeypatch.setattr(spotify, "SETTLE_S", ())
