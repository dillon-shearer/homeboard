from pathlib import Path

INSTALL = Path(__file__).resolve().parent.parent / "deploy" / "install.sh"


def test_wireplumber_restarts_only_when_its_config_changes():
    text = INSTALL.read_text(encoding="utf-8")
    sound = text.split('echo "== Sound"', 1)[1].split('echo "== Spotify Connect speaker"', 1)[0]
    assert sound.index("WP_OLD_SUM=") < sound.index("printf ")
    restart = 'systemctl --machine="$KIOSK_USER@.host" --user restart wireplumber.service'
    assert sound.count(restart) == 1
    guard = sound.split(restart, 1)[0].rsplit("if ", 1)[1]
    assert '"$WP_OLD_SUM" !=' in guard
    assert "is-active --quiet wireplumber.service" in guard
    assert "WP_RESTARTED=1" in sound.split(restart, 1)[1].split("fi", 1)[0]


def test_soloist_restarts_after_wireplumber_restart():
    text = INSTALL.read_text(encoding="utf-8")
    assert text.index("WP_RESTARTED=0") < text.index('echo "== Spotify Connect speaker"')
    tail = text.split('if [ -s "$SOLOIST_ENV" ]; then\n    SOLOIST_SUM=', 1)[1]
    guard = tail.split("systemctl restart homeboard-soloist.service", 1)[0]
    assert '[ "$WP_RESTARTED" -eq 1 ]' in guard
