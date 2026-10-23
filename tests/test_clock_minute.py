import json
import subprocess

import pytest

from tests.test_caption_lifetime import DISPLAY_JS, HARNESS, needs_node

FAKE_DATE = r"""
const RealDate = Date;
globalThis.Date = class extends RealDate {
  constructor(...a) { if (a.length) super(...a); else super(now); }
  static now() { return now; }
};
const base = new RealDate(2026, 9, 23, 10, 15, 0, 0).getTime();
"""


def _run(body):
    script = (
        f"{HARNESS}\n{FAKE_DATE}\n(async () => {{\n{body}\n}})();"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@needs_node
@pytest.mark.parametrize("offset_ms", [0, 1, 999, 15000, 29999, 30000, 45500, 59000, 59999])
def test_clock_changes_within_a_second_of_the_minute(offset_ms):
    r = _run(
        f"""
        now = base + {offset_ms};
        require({json.dumps(str(DISPLAY_JS))});
        const clock = els.clock;
        const seen = [];
        for (let minute = 1; minute <= 3; minute++) {{
          const roll = base + minute * 60000;
          await advance(roll - 1 - now);
          const before = clock.textContent;
          await advance(1000);
          seen.push({{ before, after: clock.textContent }});
        }}
        console.log(JSON.stringify(seen));
        """
    )
    assert r == [
        {"before": "10:15 am", "after": "10:16 am"},
        {"before": "10:16 am", "after": "10:17 am"},
        {"before": "10:17 am", "after": "10:18 am"},
    ]
