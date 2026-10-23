import os
import platform
from pathlib import Path


def _default_data_dir() -> str:
    if platform.system() == "Linux" and platform.machine().startswith(("arm", "aarch64")):
        return "/var/lib/homeboard"
    return "./data"


DATA_DIR = Path(os.environ.get("HOMEBOARD_DATA", _default_data_dir()))
DB_PATH = DATA_DIR / "homeboard.db"
PHOTOS_DIR = DATA_DIR / "photos"
THUMBS_DIR = DATA_DIR / "thumbs"
LONG_EDGE = int(os.environ.get("HOMEBOARD_LONG_EDGE", "1920"))

for _d in (DATA_DIR, PHOTOS_DIR, THUMBS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
