import json
import subprocess
from pathlib import Path

from tests.chrome import needs_node

SCREEN_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "tv_screen.js"
WIDTH, HEIGHT = 1024, 600

FAKE_DOM = """
const node = tag => ({tag, attrs: {}, children: [], className: '', textContent: '',
  setAttribute(k, v) { this.attrs[k] = String(v); },
  appendChild(c) { this.children.push(c); return c; }});
globalThis.document = {createElement: node, createTextNode: t => ({text: t})};
const rows = list => T.itemList(list).children.map(li => ({
  text: li.children.map(c => c.text).filter(Boolean).join(''), item: li.attrs['data-item'] || null}));
"""


@needs_node
def test_item_list_marks_tickable_rows_only():
    items = (
        "[{id: 7, text: 'Milk', done: false, pinned: false, sort_order: 1},"
        " {id: 8, text: 'Eggs', done: true, pinned: false, sort_order: 2},"
        " {id: 9, text: 'Dog food', done: false, pinned: true, sort_order: 3}]"
    )
    script = (
        f"const T = require({json.dumps(str(SCREEN_JS))});\n{FAKE_DOM}"
        f"const items = {items};\n"
        "console.log(JSON.stringify({"
        " grocery: rows({kind: 'grocery', items}),"
        " todo: rows({kind: 'todo', items}),"
        " checklist: rows({kind: 'checklist', items}),"
        " notes: rows({kind: 'notes', items}),"
        " empty: rows({kind: 'checklist', items: []})}))"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    result = json.loads(out.stdout)
    for kind in ("grocery", "todo", "checklist"):
        assert sorted(r["item"] for r in result[kind]) == ["7", "8", "9"], kind
    assert [r["item"] for r in result["notes"]] == [None, None, None]
    assert result["empty"] == [{"text": "", "item": None}]
