import os
import shutil
import subprocess
from pathlib import Path

import pytest

UPDATE = Path(__file__).resolve().parent.parent / "deploy" / "soloist-update.sh"
GIT_BASH = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git" / "bin" / "bash.exe"
BASH = str(GIT_BASH) if GIT_BASH.exists() else shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not installed")

STUBS = {
    "curl": 'while [ $# -gt 1 ]; do [ "$1" = -o ] && : > "$2"; shift; done\n',
    "sha256sum": "cat >/dev/null\n",
    "dpkg-deb": (
        'lib="$3/usr/lib/aarch64-linux-gnu"\nmkdir -p "$lib"\n'
        'for f in ld-linux-aarch64.so.1 libc.so.6 libm.so.6; do echo "$f" > "$lib/$f"; done\n'
    ),
    "tar": (
        'while [ $# -gt 0 ]; do [ "$1" = -C ] && d="$2"; shift; done\n'
        "printf '#!/usr/bin/env bash\\necho \"soloist 1.0 (20261020) arm64\"\\n'"
        ' > "$d/soloist"\nchmod 755 "$d/soloist"\n'
    ),
    "patchelf": "",
    "systemctl": '[ "$1" = is-enabled ] && exit 1\nexit 0\n',
    "cp": (
        'if [ -n "${FAIL_COPY:-}" ]; then\n'
        '  src="${@: -2:1}"; dst="${@: -1}"\n'
        '  /usr/bin/cp "$src/ld-linux-aarch64.so.1" "$dst"; exit 1\n'
        "fi\n"
        'exec /usr/bin/cp "$@"\n'
    ),
}


@pytest.fixture
def update(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        stub = bin_dir / name
        stub.write_text("#!/usr/bin/env bash\n" + body, newline="\n")
        stub.chmod(0o755)
    soloist = tmp_path / "soloist"

    def run(fail_copy=False):
        env = {
            **os.environ,
            "SOLOIST_DIR": soloist.as_posix(),
            "FAIL_COPY": "1" if fail_copy else "",
            "STUB_BIN": bin_dir.as_posix(),
        }
        script = f'export PATH="$(cd "$STUB_BIN" && pwd):$PATH"\nexec bash "{UPDATE.as_posix()}"\n'
        return subprocess.run(
            [BASH, "-c", script], check=False, capture_output=True, text=True, env=env
        )

    run.dir = soloist
    return run


def test_failed_glibc_copy_is_not_taken_as_complete_and_next_run_repairs_it(update):
    failed = update(fail_copy=True)
    assert failed.returncode != 0
    lib = update.dir / "lib"
    assert not (lib / ".complete").exists()
    assert not (lib / "ld-linux-aarch64.so.1").exists()

    repaired = update()
    assert repaired.returncode == 0, repaired.stderr
    assert sorted(p.name for p in lib.iterdir()) == [
        ".complete",
        "ld-linux-aarch64.so.1",
        "libc.so.6",
        "libm.so.6",
    ]
    assert not (update.dir / "lib.new").exists()
    assert (update.dir / "soloist").exists()


def test_failed_copy_over_an_old_tree_keeps_the_old_tree(update):
    assert update().returncode == 0
    lib = update.dir / "lib"
    (lib / ".complete").write_text("old\n")
    (lib / "old-only").write_text("x")

    assert update(fail_copy=True).returncode != 0
    assert (lib / "old-only").exists()

    assert update().returncode == 0
    assert not (lib / "old-only").exists()
    assert (lib / "libc.so.6").exists()
    assert not (update.dir / "lib.old").exists()
