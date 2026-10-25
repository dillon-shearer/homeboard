import os
import shutil
import subprocess
from pathlib import Path

import pytest

INSTALL = Path(__file__).resolve().parent.parent / "deploy" / "install.sh"
GIT_BASH = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git" / "bin" / "bash.exe"
BASH = str(GIT_BASH) if GIT_BASH.exists() else shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not installed")


def _block():
    text = INSTALL.read_text(encoding="utf-8")
    start = text.index('if [ -s "$SOLOIST_ENV" ]; then\n    SOLOIST_SUM=')
    return text[start : text.index("\nfi\n", start) + 4]


@pytest.fixture
def pi(tmp_path):
    root = tmp_path.as_posix()
    for d in ("units", "opt", "app/deploy", "etc"):
        (tmp_path / d).mkdir(parents=True)
    files = {
        "units/homeboard-soloist.service": "[Service]\n",
        "units/homeboard-soloist-events.service": "[Service]\n",
        "app/deploy/soloist.sh": "run\n",
        "app/deploy/soloist-event.sh": "post\n",
        "opt/soloist": "binary\n",
        "etc/soloist.env": "SOLOIST_API_KEY=k\n",
    }
    for name, body in files.items():
        (tmp_path / name).write_text(body)
    (tmp_path / "active").write_text("")
    script = (
        "set -euo pipefail\n"
        f'APP_DIR="{root}/app"\nSOLOIST_ENV="{root}/etc/soloist.env"\n'
        'WP_RESTARTED="${WP_RESTARTED:-0}"\n'
        "systemctl() {\n"
        f'  if [ "$1" = is-active ]; then grep -qx "$3" "{root}/active"; return; fi\n'
        f'  echo "$*" >> "{root}/calls"; echo "$2" >> "{root}/active"\n'
        "}\n"
        + _block()
        .replace("/etc/systemd/system/", f"{root}/units/")
        .replace("/opt/soloist/", f"{root}/opt/")
    )
    (tmp_path / "run.sh").write_text(script, newline="\n")

    def run(wp_restarted="0"):
        calls = tmp_path / "calls"
        calls.write_text("")
        env = {**os.environ, "WP_RESTARTED": wp_restarted}
        subprocess.run([BASH, str(tmp_path / "run.sh")], check=True, capture_output=True, env=env)
        return calls.read_text().splitlines()

    run.path = tmp_path
    return run


def test_first_run_restarts_then_unchanged_runs_do_not(pi):
    assert pi() == ["restart homeboard-soloist.service"]
    (pi.path / "active").write_text("homeboard-soloist.service\nhomeboard-soloist-events.service\n")
    assert pi() == []
    assert pi() == []


@pytest.mark.parametrize(
    "name",
    ["units/homeboard-soloist.service", "etc/soloist.env", "app/deploy/soloist.sh", "opt/soloist"],
)
def test_soloist_file_change_restarts_once(pi, name):
    pi()
    (pi.path / "active").write_text("homeboard-soloist.service\nhomeboard-soloist-events.service\n")
    with open(pi.path / name, "a") as f:
        f.write("changed\n")
    assert pi() == ["restart homeboard-soloist.service"]
    assert pi() == []


@pytest.mark.parametrize(
    "name", ["units/homeboard-soloist-events.service", "app/deploy/soloist-event.sh"]
)
def test_events_file_change_restarts_only_events(pi, name):
    pi()
    (pi.path / "active").write_text("homeboard-soloist.service\nhomeboard-soloist-events.service\n")
    with open(pi.path / name, "a") as f:
        f.write("changed\n")
    assert pi() == ["restart homeboard-soloist-events.service"]
    assert pi() == []


def test_stopped_soloist_is_restarted(pi):
    pi()
    (pi.path / "active").write_text("homeboard-soloist-events.service\n")
    assert pi() == ["restart homeboard-soloist.service"]


def test_wireplumber_restart_restarts_soloist(pi):
    pi()
    (pi.path / "active").write_text("homeboard-soloist.service\nhomeboard-soloist-events.service\n")
    assert pi(wp_restarted="1") == ["restart homeboard-soloist.service"]
    assert pi() == []
