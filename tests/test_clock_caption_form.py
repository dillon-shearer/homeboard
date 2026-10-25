import json
import re
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import POSITIONS
from app.main import create_app
from tests.test_upload_batches import PAGE_STUB, needs_node

SETTINGS_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "settings.js"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


@pytest.fixture
def page(client):
    r = client.get("/admin/settings")
    assert r.status_code == 200
    return r.text


def _group(page, legend_id):
    start = page.index(f'aria-labelledby="{legend_id}"')
    return page[start : page.index("</section>", start)]


def test_group_lives_on_settings_page_after_photos(client, page):
    assert page.index(">Clock, date and caption</h2>") > page.index(">Photos</h2>")
    photos = client.get("/admin/photos").text
    for gone in ("Clock, date and caption", 'name="clock_pos"', 'name="caption_pos"'):
        assert gone not in photos


def test_group_has_steppers_spots_stack_and_captions(page):
    group = _group(page, "group-overlay")
    assert 'type="number" min="2" max="12" step="1" inputmode="numeric"' in group
    for key in ("clock_pos", "caption_pos"):
        values = re.findall(rf'name="{key}" value="([a-z-]+)"', group)
        assert values == list(POSITIONS)
    assert 'aria-label="Top left"' in group and 'aria-label="Middle center"' in group
    assert re.findall(r'name="clock_on_top" value="(\w+)"', group) == ["true", "false"]
    assert '<input type="checkbox" data-key="captions">' in group
    assert 'name="fit"' not in group


FORM_SETUP = """
var puts = [];
var settings = {
  interval_s: 20, transition: true, fit: "contain", shuffle: false, captions: true,
  clock_size: 5, caption_size: 4, clock_pos: "bottom-right", caption_pos: "bottom-left", clock_on_top: true,
  date_size: 3, date_pos: "bottom-right", date_on_top: false, date_show: true,
  cover_size: 3, cover_pos: "top-right", cover_on_top: true, cover_show: true,
};
var radios = {};
["fit", "clock_pos", "caption_pos", "clock_on_top", "date_pos", "date_on_top", "cover_pos", "cover_on_top"].forEach(function (key) {
  radios[key] = [];
  var flag = key === "clock_on_top" || key === "date_on_top" || key === "cover_on_top";
  var values = key === "fit" ? ["contain", "cover"] : flag ? ["true", "false"] : [
    "top-left", "top-center", "top-right", "middle-left", "middle-center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
  ];
  values.forEach(function (v) {
    var el = new El("input");
    el.setAttribute("name", key);
    el.value = v;
    el.checked = false;
    radios[key].push(el);
  });
});
document.querySelectorAll = function (sel) {
  var m = /name="(\\w+)"/.exec(sel);
  return m ? radios[m[1]] : [];
};
function radio(key, value) { return radios[key].filter(function (r) { return r.value === value; })[0]; }
function checkedValue(key) {
  var on = radios[key].filter(function (r) { return r.checked; });
  return on.length ? on[0].value : null;
}
globalThis.fetch = function (url, opts) {
  var body = {};
  if (url === "/api/status") body = { display_connected: true, photo_count: 0 };
  if (url === "/api/settings") {
    if (opts && opts.method === "PUT") {
      var patch = JSON.parse(opts.body);
      puts.push(patch);
      settings = Object.assign({}, settings, patch);
    }
    body = settings;
  }
  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(body); } });
};
function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
function tap(id) { $(id).fire("click", { detail: 0 }); }
"""


def _node(body, setup=""):
    script = (
        f"{PAGE_STUB}\n{setup}\nrequire({json.dumps(str(SETTINGS_JS))});\n"
        f"(async function () {{\n{body}\n}})().catch(function (e) {{ console.error(e); process.exit(1); }});"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


@needs_node
def test_stack_row_shows_only_when_spots_match():
    r = _node(
        """
        await flush();
        const apart = { hidden: $("stack-row").hidden, clock: checkedValue("clock_pos"), top: checkedValue("clock_on_top") };
        const cell = radio("caption_pos", "bottom-right");
        cell.checked = true;
        cell.fire("change");
        const sameNow = $("stack-row").hidden;
        await flush();
        const sameAfter = $("stack-row").hidden;
        const moved = radio("clock_pos", "top-center");
        moved.checked = true;
        moved.fire("change");
        await flush();
        const stackBtn = radio("clock_on_top", "false");
        stackBtn.checked = true;
        stackBtn.fire("change");
        await flush();
        console.log(JSON.stringify({
          apart: apart, sameNow: sameNow, sameAfter: sameAfter, movedHidden: $("stack-row").hidden,
          puts: puts, sizes: [$("clock-size").value, $("caption-size").value],
        }));
        """,
        FORM_SETUP,
    )
    assert r["apart"] == {"hidden": True, "clock": "bottom-right", "top": "true"}
    assert r["sameNow"] is False and r["sameAfter"] is False
    assert r["movedHidden"] is True
    assert r["puts"] == [
        {"caption_pos": "bottom-right"},
        {"clock_pos": "top-center"},
        {"clock_on_top": False},
    ]
    assert r["sizes"] == [5, 4]


@needs_node
def test_clock_size_steps_clamp_and_put_only_that_field():
    r = _node(
        """
        await flush();
        $("clock-size").value = 12;
        tap("clock-size-plus");
        const atTop = $("clock-size").value;
        await wait(450);
        $("clock-size").value = 2;
        tap("clock-size-minus");
        tap("clock-size-minus");
        const atBottom = $("clock-size").value;
        await wait(450);
        tap("clock-size-plus");
        tap("clock-size-plus");
        await wait(450);
        tap("caption-size-plus");
        await wait(450);
        console.log(JSON.stringify({ atTop: atTop, atBottom: atBottom, puts: puts, shown: $("clock-size").value }));
        """,
        FORM_SETUP,
    )
    assert r["atTop"] == 12
    assert r["atBottom"] == 2
    assert r["puts"] == [
        {"clock_size": 12},
        {"clock_size": 2},
        {"clock_size": 4},
        {"caption_size": 5},
    ]
    assert r["shown"] == 4
