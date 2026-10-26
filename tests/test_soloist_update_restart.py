import os
import subprocess

import pytest

from tests.test_soloist_update_partial import BASH, STUBS, UPDATE

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not installed")

SYSTEMCTL = (
    'echo "$*" >> "$CALLS"\n'
    'case "$1" in\n'
    '  is-enabled) [ "$ENABLED" = 1 ];;\n'
    '  is-active) [ "$ACTIVE" = 1 ];;\n'
    "  *) exit 0;;\n"
    "esac\n"
)
DATE = 'if [ "$1" = +%F ]; then echo "$TODAY"; else exec /usr/bin/date "$@"; fi\n'


@pytest.fixture
def update(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stubs = {**STUBS, "systemctl": SYSTEMCTL, "date": DATE}
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text("#!/usr/bin/env bash\n" + body, newline="\n")
        stub.chmod(0o755)
    soloist = tmp_path / "soloist"
    calls = tmp_path / "calls"

    def run(enabled=True, active=False, today="2026-10-25"):
        calls.write_text("")
        env = {
            **os.environ,
            "SOLOIST_DIR": soloist.as_posix(),
            "FAIL_COPY": "",
            "STUB_BIN": bin_dir.as_posix(),
            "CALLS": calls.as_posix(),
            "ENABLED": "1" if enabled else "0",
            "ACTIVE": "1" if active else "0",
            "TODAY": today,
        }
        script = f'export PATH="$(cd "$STUB_BIN" && pwd):$PATH"\nexec bash "{UPDATE.as_posix()}"\n'
        out = subprocess.run(
            [BASH, "-c", script], check=False, capture_output=True, text=True, env=env
        )
        assert out.returncode == 0, out.stderr
        return [c for c in calls.read_text().splitlines() if not c.startswith("is-")]

    run.dir = soloist
    return run


def test_first_install_starts_a_stopped_configured_service(update):
    assert update() == [
        "try-restart homeboard-soloist.service",
        "restart homeboard-soloist.service",
    ]
    assert (update.dir / "expires").read_text().strip() == "2027-01-18"
    assert (update.dir / "soloist").exists()


def test_up_to_date_run_starts_a_stopped_configured_service(update):
    update(active=True)
    assert update() == ["restart homeboard-soloist.service"]


def test_running_service_is_left_alone(update):
    update(active=True)
    assert update(active=True) == []


def test_service_that_is_not_configured_is_not_started(update):
    assert update(enabled=False) == ["try-restart homeboard-soloist.service"]
    assert update(enabled=False) == []


def test_build_past_its_expiry_is_not_started(update):
    update(active=True)
    assert update(today="2027-01-18") == []
    assert update(today="2027-01-17") == ["restart homeboard-soloist.service"]
