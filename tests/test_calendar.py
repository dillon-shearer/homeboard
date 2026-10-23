from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import db, events
from app.calendar import expand
from app.main import create_app

SSE = {"Accept": "text/event-stream"}


def ev(day, repeat="never", time=None, id=1, title="E"):
    return {"id": id, "title": title, "date": day, "time": time, "repeat": repeat}


def dates(events_, start, end):
    return [o["date"] for o in expand(events_, date.fromisoformat(start), date.fromisoformat(end))]


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_never_only_on_its_date_with_inclusive_ends():
    assert dates([ev("2026-10-24")], "2026-10-24", "2026-10-24") == ["2026-10-24"]
    assert dates([ev("2026-10-24")], "2026-10-01", "2026-10-24") == ["2026-10-24"]
    assert dates([ev("2026-10-24")], "2026-10-24", "2026-10-30") == ["2026-10-24"]
    assert dates([ev("2026-10-24")], "2026-10-25", "2026-10-30") == []
    assert dates([ev("2026-10-24")], "2026-10-01", "2026-10-23") == []


def test_weekly_same_weekday_from_start_date_on():
    assert dates([ev("2026-10-24", "weekly")], "2026-10-24", "2026-11-07") == [
        "2026-10-24",
        "2026-10-31",
        "2026-11-07",
    ]
    assert dates([ev("2026-10-24", "weekly")], "2026-09-01", "2026-10-30") == ["2026-10-24"]
    assert dates([ev("2026-01-03", "weekly")], "2026-10-25", "2026-11-08") == [
        "2026-10-31",
        "2026-11-07",
    ]
    assert dates([ev("2026-01-03", "weekly")], "2026-10-25", "2026-10-30") == []


def test_monthly_same_day_of_month():
    assert dates([ev("2026-01-15", "monthly")], "2026-01-01", "2026-03-31") == [
        "2026-01-15",
        "2026-02-15",
        "2026-03-15",
    ]
    assert dates([ev("2025-11-15", "monthly")], "2026-01-15", "2026-02-15") == [
        "2026-01-15",
        "2026-02-15",
    ]
    assert dates([ev("2026-03-15", "monthly")], "2026-01-01", "2026-03-14") == []


def test_monthly_day_31_skips_short_months():
    assert dates([ev("2026-01-31", "monthly")], "2026-01-01", "2026-08-31") == [
        "2026-01-31",
        "2026-03-31",
        "2026-05-31",
        "2026-07-31",
        "2026-08-31",
    ]
    assert dates([ev("2026-01-30", "monthly")], "2026-02-01", "2026-03-31") == ["2026-03-30"]
    assert dates([ev("2026-10-31", "monthly")], "2026-11-01", "2027-01-31") == [
        "2026-12-31",
        "2027-01-31",
    ]


def test_yearly_same_month_and_day():
    e = [ev("2024-10-24", "yearly")]
    assert dates(e, "2026-10-24", "2026-10-24") == ["2026-10-24"]
    assert dates(e, "2026-10-25", "2026-12-31") == []
    assert dates(e, "2025-12-01", "2027-01-31") == ["2026-10-24"]
    assert dates([ev("2026-12-31", "yearly")], "2026-12-25", "2027-01-05") == ["2026-12-31"]
    assert dates([ev("2027-01-01", "yearly")], "2026-12-25", "2027-01-05") == ["2027-01-01"]
    assert dates([ev("2026-10-24", "yearly")], "2025-10-01", "2025-10-31") == []


def test_yearly_feb_29_only_in_leap_years():
    e = [ev("2024-02-29", "yearly")]
    for year in (2025, 2026, 2027, 2100):
        assert dates(e, f"{year}-02-01", f"{year}-03-31") == []
    assert dates(e, "2028-02-01", "2028-03-31") == ["2028-02-29"]
    assert dates(e, "2024-02-29", "2024-02-29") == ["2024-02-29"]


def test_monthly_feb_29_start_skips_to_months_with_day_29():
    assert dates([ev("2024-02-29", "monthly")], "2025-01-01", "2025-04-30") == [
        "2025-01-29",
        "2025-03-29",
        "2025-04-29",
    ]


def test_occurrences_sorted_by_date_then_time_all_day_first():
    out = expand(
        [
            ev("2026-10-25", time="09:00", id=1, title="late"),
            ev("2026-10-24", time="18:00", id=2, title="evening"),
            ev("2026-10-24", time="08:30", id=3, title="morning"),
            ev("2026-10-24", id=4, title="allday"),
        ],
        date(2026, 10, 24),
        date(2026, 10, 25),
    )
    assert [o["title"] for o in out] == ["allday", "morning", "evening", "late"]
    assert out[0] == {
        "id": 4,
        "title": "allday",
        "date": "2026-10-24",
        "time": None,
        "repeat": "never",
    }


def test_db_helpers_validate(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.migrate(conn)
    row = db.create_event(conn, "  Dentist  ", "2026-10-24", "09:05:00", "yearly")
    assert (row["title"], row["date"], row["time"], row["repeat"]) == (
        "Dentist",
        "2026-10-24",
        "09:05",
        "yearly",
    )
    for args in (
        ("", "2026-10-24"),
        ("x" * 121, "2026-10-24"),
        ("a", "2026-13-01"),
        ("a", "2026-02-30"),
        ("a", "2026-10-24", "25:00"),
        ("a", "2026-10-24", None, "daily"),
    ):
        with pytest.raises(ValueError):
            db.create_event(conn, *args)
    assert db.create_event(conn, "x" * 120, "2026-10-24")["time"] is None
    row = db.update_event(conn, row["id"], time=None, title="Doc")
    assert (row["title"], row["time"]) == ("Doc", None)
    with pytest.raises(ValueError):
        db.update_event(conn, row["id"], date="nope")
    assert len(db.list_events(conn)) == 2
    assert db.delete_event(conn, row["id"]) is not None
    assert db.delete_event(conn, row["id"]) is None
    conn.close()


def test_weekly_event_api(client):
    r = client.post(
        "/api/events", json={"title": "Trash", "date": "2026-10-24", "repeat": "weekly"}
    )
    assert r.status_code == 201
    assert r.json()["repeat"] == "weekly" and r.json()["time"] is None
    r = client.get("/api/events?start=2026-10-24&end=2026-11-07")
    assert r.status_code == 200
    assert [o["date"] for o in r.json()] == ["2026-10-24", "2026-10-31", "2026-11-07"]
    assert all(o["title"] == "Trash" for o in r.json())


def test_bad_dates_are_422(client):
    assert client.post("/api/events", json={"title": "a", "date": "2026-13-01"}).status_code == 422
    body = {"title": "a", "date": "2026-10-24", "time": "7pm"}
    assert client.post("/api/events", json=body).status_code == 422
    body = {"title": "a", "date": "2026-10-24", "repeat": "daily"}
    assert client.post("/api/events", json=body).status_code == 422
    assert client.post("/api/events", json={"title": " ", "date": "2026-10-24"}).status_code == 422
    assert client.get("/api/events?start=2026-02-30&end=2026-03-01").status_code == 422
    assert client.get("/api/events?start=2026-03-02&end=2026-03-01").status_code == 422


def test_range_limit(client):
    assert client.get("/api/events?start=2026-01-01&end=2026-03-05").status_code == 422
    assert client.get("/api/events?start=2026-01-01&end=2026-03-04").status_code == 422
    assert client.get("/api/events?start=2026-01-01&end=2026-03-03").status_code == 200


def test_default_range_is_today_through_six_days(client):
    today = date.today()
    for offset in (-1, 0, 6, 7):
        day = (today + timedelta(days=offset)).isoformat()
        client.post("/api/events", json={"title": str(offset), "date": day})
    r = client.get("/api/events")
    assert r.status_code == 200
    assert [o["title"] for o in r.json()] == ["0", "6"]


def test_patch_and_delete(client):
    body = {"title": "Gym", "date": "2026-10-24", "time": "18:00"}
    eid = client.post("/api/events", json=body).json()["id"]
    r = client.patch(f"/api/events/{eid}", json={"time": None, "repeat": "monthly"})
    assert r.status_code == 200
    assert (r.json()["time"], r.json()["repeat"], r.json()["title"]) == (None, "monthly", "Gym")
    r = client.patch(f"/api/events/{eid}", json={"time": "07:15"})
    assert r.json()["time"] == "07:15"
    assert client.patch(f"/api/events/{eid}", json={"date": "2026-13-01"}).status_code == 422
    assert client.patch("/api/events/999", json={"title": "x"}).status_code == 404
    assert client.delete(f"/api/events/{eid}").status_code == 204
    assert client.delete(f"/api/events/{eid}").status_code == 404
    assert client.get("/api/events?start=2026-10-24&end=2026-10-24").json() == []


def test_writes_publish_events_changed(client):
    events.last_event = None
    eid = client.post("/api/events", json={"title": "a", "date": "2026-10-24"}).json()["id"]
    assert events.last_event == "events_changed"
    events.last_event = None
    client.patch(f"/api/events/{eid}", json={"title": "b"})
    assert events.last_event == "events_changed"
    events.last_event = None
    client.delete(f"/api/events/{eid}")
    assert events.last_event == "events_changed"


def test_post_emits_events_changed_on_sse(client):
    client.app.state.sse_limit = 1
    with client.stream("GET", "/api/events", headers=SSE) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        client.post("/api/events", json={"title": "Trash", "date": "2026-10-24"})
        body = "".join(r.iter_text())
    assert "event: events_changed" in body
