import json
import time

import pytest

from tests.chrome import _Chrome, needs_chrome, server  # noqa: F401

IDS = ["month-prev", "month-select", "year-select", "month-next", "month-today"]

ROW = """JSON.stringify({
  w: innerWidth,
  box: document.querySelector('.month').getBoundingClientRect().right,
  scrollW: document.documentElement.scrollWidth,
  years: document.getElementById('year-select').options.length,
  rects: IDS.map(id => {
    const r = document.getElementById(id).getBoundingClientRect();
    return {id, top: r.top, right: r.right, width: r.width};
  }),
  months: [...document.getElementById('month-select').options].map(o => [o.value, o.text]),
})""".replace("IDS", json.dumps(IDS))

FULL = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


@needs_chrome
@pytest.mark.parametrize("touch", [False, True])
@pytest.mark.parametrize("width,height", [(320, 844), (360, 844), (390, 844), (412, 915)])
def test_month_row_stays_on_one_line(server, tmp_path, width, height, touch):  # noqa: F811
    chrome = _Chrome(tmp_path / "chrome", width, height)
    try:
        if touch:
            chrome.send(
                "Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5}
            )
        chrome.send("Page.navigate", {"url": f"{server}/admin/calendar"})
        row = None
        for _ in range(100):
            row = json.loads(chrome.eval(ROW) or "null")
            if row and row["years"]:
                break
            time.sleep(0.05)
    finally:
        chrome.close()

    assert row["w"] == width
    tops = {r["top"] for r in row["rects"]}
    assert len(tops) == 1, row["rects"]
    for r in row["rects"]:
        assert r["right"] <= width, r
        assert r["right"] <= row["box"], r
    assert row["scrollW"] <= width
    year = next(r for r in row["rects"] if r["id"] == "year-select")
    assert year["width"] >= 50, year
    assert [v for v, _ in row["months"]] == [str(n) for n in range(1, 13)]
    labels = [t for _, t in row["months"]]
    assert labels == ([m[:3] for m in FULL] if width < 360 else FULL)
