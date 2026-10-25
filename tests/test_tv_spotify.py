import base64
import io
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import db, spotify
from app.main import create_app
from tests.chrome import needs_node

SCREEN_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "tv_screen.js"
SIZES = [(2, 2), (2, 1), (1, 2), (1, 1)]


def _node(body):
    script = f"const T = require({json.dumps(str(SCREEN_JS))});\n{body}"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


def _png(color, size=64):
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _playing(**over):
    now = {
        "linked": True,
        "playing": True,
        "on_board": True,
        "track": "Here Comes the Sun",
        "artist": "The Beatles",
        "album": "Abbey Road",
        "cover": _png((200, 120, 40), 300),
        "progress_ms": 65000,
        "duration_ms": 185000,
        "volume": 40,
        "device": "Home Board",
        "stale": False,
    }
    now.update(over)
    return now


@needs_node
def test_spotify_helpers():
    empty = spotify._empty()
    result = _node(
        f"const empty = {json.dumps(empty)};"
        f"const playing = {json.dumps(_playing(cover='https://i.scdn.co/big'))};"
        "const paused = Object.assign({}, playing, {playing: false});"
        "console.log(JSON.stringify({"
        " ms: [0, 65000, 3600000, 59999, null].map(T.formatMs),"
        " layouts: [[2, 2], [2, 1], [1, 2], [1, 1]].map(s => T.spotifyLayout({w: s[0], h: s[1]})),"
        " states: [empty, playing, paused, {linked: false, reason: 'x'}, null].map(T.spotifyState),"
        " covers: [empty, playing, paused, {linked: false}].map(T.coverUrl),"
        " progress: [T.progressAt(playing, 2000), T.progressAt(paused, 2000),"
        "   T.progressAt(playing, 999999), T.progressAt(empty, 1000)],"
        " volume: [T.volumeStep(40, 5), T.volumeStep(98, 5), T.volumeStep(3, -5), T.volumeStep(null, 5)],"
        " speaker: [T.speakerOn({speaker: {connected: true}}), T.speakerOn({speaker: {connected: false}}),"
        "   T.speakerOn(null)]}))"
    )
    assert empty["playing"] is False
    assert result["ms"] == ["0:00", "1:05", "60:00", "0:59", "0:00"]
    assert result["layouts"] == ["full", "wide", "tall", "tile"]
    assert result["states"] == ["idle", "playing", "paused", "unlinked", "idle"]
    assert result["covers"] == [None, "https://i.scdn.co/big", "https://i.scdn.co/big", None]
    assert result["progress"] == [67000, 65000, 185000, 0]
    assert result["volume"] == [45, 100, 0, None]
    assert result["speaker"] == [True, False, False]


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_spotify_widget_kind_takes_all_four_sizes(client):
    assert client.get("/api/widget-sizes").json()["spotify"] == [[2, 2], [2, 1], [1, 2], [1, 1]]
    for w, h in SIZES:
        sid = client.post("/api/screens", json={"name": f"Music {w}x{h}"}).json()["id"]
        r = client.post(
            f"/api/screens/{sid}/widgets",
            json={"kind": "spotify", "col": 0, "row": 0, "w": w, "h": h},
        )
        assert r.status_code == 201, r.text
        assert [(x["kind"], x["w"], x["h"]) for x in r.json()["widgets"]] == [("spotify", w, h)]
    sid = client.post("/api/screens", json={"name": "Default"}).json()["id"]
    r = client.post(f"/api/screens/{sid}/widgets", json={"kind": "spotify", "col": 0, "row": 0})
    assert r.status_code == 201
    assert [(x["w"], x["h"]) for x in r.json()["widgets"]] == [(2, 2)]
    page = client.get("/tv")
    assert page.status_code == 200 and "tv_screen.js" in page.text


def test_migration_keeps_widgets_and_allows_spotify(tmp_path):
    path = tmp_path / "homeboard.db"
    conn = db.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        for v in range(1, 17):
            sql = next(db.MIGRATIONS_DIR.glob(f"{v:04d}_*.sql"))
            conn.executescript(sql.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (v,))
        conn.commit()
        before = [tuple(r) for r in conn.execute("SELECT * FROM widgets ORDER BY id")]
        assert before
        db.migrate(conn)
        assert [tuple(r) for r in conn.execute("SELECT * FROM widgets ORDER BY id")] == before
        sid = conn.execute(
            "INSERT INTO tv_screens (name, sort_order, created_at) VALUES ('M', 99, 'x')"
        ).lastrowid
        conn.commit()
        assert db.add_widget(conn, sid, "spotify", 0, 0, 1, 1)["kind"] == "spotify"
        names = {r[1] for r in conn.execute("PRAGMA index_list(widgets)")}
        assert "widgets_screen_id" in names
    finally:
        conn.close()
