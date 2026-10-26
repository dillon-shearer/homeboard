import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / "deploy" / "install.sh"
GIT_BASH = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git" / "bin" / "bash.exe"
BASH = str(GIT_BASH) if GIT_BASH.exists() else shutil.which("bash")

pytestmark = pytest.mark.skipif(
    BASH is None or shutil.which("git") is None, reason="bash or git not installed"
)

STUBS = {
    "id": '[ "$1" = -u ] && [ $# -eq 1 ] && { echo 0; exit 0; }\n[ "$1" = homeboard ]\n',
    "systemctl": (
        'echo "$*" >> "$CALLS"\n'
        'case "$1" in list-unit-files) exit 1 ;; is-active) exit 0 ;; esac\n'
        'if [ "$FAIL" = restart ] && [ "$1 $2" = "restart homeboard-api.service" ]; then exit 1; fi\n'
        "exit 0\n"
    ),
    "nginx": '[ "$FAIL" = nginx ] && exit 1\nexit 0\n',
    "udevadm": '[ "$FAIL" = term ] && kill -TERM "$PPID"\nexit 0\n',
    "cc": (
        'while [ $# -gt 0 ]; do [ "$1" = -o ] && : > "$2"; shift; done\n'
        "cat >/dev/null 2>&1 || true\n"
    ),
    "apt-get": "",
    "dpkg": "exit 1\n",
    "useradd": "",
    "usermod": "",
    "chown": "",
    "loginctl": "",
    "curl": "exit 1\n",
    "rfkill": "",
    "python3": "exit 1\n",
}

PYTHON = '[ "$1" = -c ] && [ "$FAIL" = import ] && exit 1\nexit 0\n'
PIP = (
    'venv="$(cd "$(dirname "$0")/.." && pwd)"\n'
    '[ "$1 $2" = "install -r" ] || exit 0\n'
    'echo partial > "$venv/installed"\n'
    '[ "$FAIL" = pip ] && exit 1\n'
    'cp "$3" "$venv/installed"\n'
)


def msys(path: Path) -> str:
    p = path.as_posix()
    if re.match(r"^[A-Za-z]:/", p):
        p = "/" + p[0].lower() + p[2:]
    return p


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def write(path: Path, body: str, mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, newline="\n")
    if mode:
        path.chmod(mode)


@pytest.fixture
def pi(tmp_path):
    root = tmp_path / "root"
    origin = tmp_path / "origin"
    shutil.copytree(REPO / "deploy", origin / "deploy")
    shutil.copy(REPO / "requirements.txt", origin / "requirements.txt")
    write(origin / ".gitignore", ".venv/\n.venv.prev/\n")
    git(tmp_path, "init", "-q", str(origin))
    git(origin, "add", "-A")
    git(origin, "commit", "-qm", "v1")
    app = root / "opt" / "homeboard"
    git(tmp_path, "clone", "-q", str(origin), str(app))
    write(app / ".venv" / "bin" / "python", "#!/usr/bin/env bash\n" + PYTHON, 0o755)
    write(app / ".venv" / "bin" / "pip", "#!/usr/bin/env bash\n" + PIP, 0o755)
    write(root / "opt" / "soloist" / "soloist", "#!/usr/bin/env bash\n", 0o755)
    write(root / "opt" / "soloist" / "lib" / ".complete", "sum\n")
    write(root / "etc" / "homeboard" / "soloist.env", "SOLOIST_API_KEY=k\n")
    write(root / "etc" / "bluetooth" / "main.conf", "[Policy]\nAutoEnable=true\n")
    write(root / "etc" / "fstab", "/dev/root / ext4 defaults,noatime 0 1\n")
    for d in (
        "etc/nginx/snippets",
        "etc/nginx/conf.d",
        "etc/nginx/sites-available",
        "etc/nginx/sites-enabled",
        "etc/systemd/system",
        "etc/udev/rules.d",
        "var/tmp",
    ):
        (root / d).mkdir(parents=True, exist_ok=True)
    write(root / "etc" / "nginx" / "sites-enabled" / "default", "server {}\n")
    bin_dir = tmp_path / "bin"
    for name, body in STUBS.items():
        write(bin_dir / name, "#!/usr/bin/env bash\n" + body, 0o755)
    text = re.sub(
        r"(?<![\w.$])/(etc|opt|var|boot|usr/share)/",
        lambda m: f"{msys(root)}/{m.group(1)}/",
        INSTALL.read_text(encoding="utf-8"),
    )
    write(tmp_path / "install.sh", text)
    calls = tmp_path / "calls"

    def run(fail=""):
        calls.write_text("")
        env = {**os.environ, "FAIL": fail, "CALLS": msys(calls), "STUB_BIN": msys(bin_dir)}
        script = (
            'export PATH="$STUB_BIN:$PATH"\n'
            f'exec bash "{msys(tmp_path / "install.sh")}" "{msys(origin)}" </dev/null\n'
        )
        done = subprocess.run(
            [BASH, "-c", script], check=False, capture_output=True, text=True, env=env
        )
        return done, calls.read_text().splitlines()

    def snapshot():
        files = {}
        for top in (root / "etc", app / ".venv"):
            for p in sorted(top.rglob("*")):
                if p.is_file():
                    files[p.relative_to(root).as_posix()] = p.read_bytes()
        return git(app, "rev-parse", "HEAD"), git(app, "status", "--porcelain"), files

    def release():
        with open(origin / "requirements.txt", "a", newline="\n") as f:
            f.write("extra==1.0\n")
        for name in ("homeboard-api.service", "nginx/homeboard-proxy.conf"):
            with open(origin / "deploy" / name, "a", newline="\n") as f:
                f.write("# v2\n")
        git(origin, "commit", "-qam", "v2")
        return git(origin, "rev-parse", "HEAD")

    run.root, run.app, run.snapshot, run.release = root, app, snapshot, release
    return run


def first_install(pi):
    done, _ = pi()
    assert done.returncode == 0, done.stderr
    return pi.snapshot()


@pytest.mark.parametrize("fail", ["pip", "import", "term", "nginx", "restart"])
def test_interrupted_install_leaves_the_previous_version(pi, fail):
    before = first_install(pi)
    pi.release()
    done, calls = pi(fail)
    assert done.returncode != 0
    assert "putting the previous version back" in done.stderr
    assert pi.snapshot() == before
    assert calls[-1] == "start homeboard-api.service"
    assert not (pi.app / ".venv.prev").exists()
    assert list((pi.root / "var" / "tmp").iterdir()) == []


def test_finished_install_keeps_the_new_version(pi):
    first_install(pi)
    new = pi.release()
    done, _ = pi()
    assert done.returncode == 0, done.stderr
    assert git(pi.app, "rev-parse", "HEAD") == new
    assert "extra==1.0" in (pi.app / ".venv" / "installed").read_text()
    unit = pi.root / "etc" / "systemd" / "system" / "homeboard-api.service"
    assert unit.read_text().endswith("# v2\n")
    assert not (pi.app / ".venv.prev").exists()
    assert list((pi.root / "var" / "tmp").iterdir()) == []


def test_unchanged_requirements_skip_pip(pi):
    first_install(pi)
    (pi.app / ".venv" / "installed").write_text("untouched\n")
    done, _ = pi()
    assert done.returncode == 0, done.stderr
    assert (pi.app / ".venv" / "installed").read_text() == "untouched\n"
