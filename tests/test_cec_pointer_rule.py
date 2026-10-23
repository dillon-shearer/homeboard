from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
RULE = "90-homeboard-ignore-cec-input.rules"


def test_rule_untags_cec_inputs_as_pointers():
    raw = (DEPLOY / RULE).read_bytes()
    assert b"\r\n" not in raw
    text = raw.decode("ascii")
    assert text.strip() == (
        'SUBSYSTEM=="input", ATTRS{name}=="vc4-hdmi*", '
        'ENV{ID_INPUT_POINTINGSTICK}="", ENV{ID_INPUT_MOUSE}=""'
    )
    assert "LIBINPUT_IGNORE_DEVICE" not in text


def test_install_copies_rule_and_reloads_udev():
    text = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    line = f'install -m 644 "$APP_DIR"/deploy/{RULE} /etc/udev/rules.d/'
    assert line in text
    assert text.index(line) < text.index("udevadm control --reload")
    assert text.index('git clone "$REPO_URL" "$APP_DIR"') < text.index(line)
