from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "app"
JS = (ROOT / "static" / "settings.js").read_text()
HTML = (ROOT / "templates" / "settings.html").read_text()


def test_interval_input_steps_by_one():
    assert 'type="number" min="5" max="600" step="1"' in HTML
    assert 'step="5"' not in HTML


def test_no_round_to_five():
    assert "/ 5) * 5" not in JS
    assert "stepInterval(-5)" not in JS and "stepInterval(5)" not in JS
    assert 'makeStepper("interval", "interval_s", 5, 600)' in JS
    assert 'holdStep($(id + "-minus"), function () { step(-1); })' in JS
    assert 'holdStep($(id + "-plus"), function () { step(1); })' in JS
