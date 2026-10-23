import sqlite3
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app import db, events
from app.calendar import expand
from app.routes_photos import Conn
from app.routes_settings import stream_events

router = APIRouter(prefix="/api")

MAX_RANGE_DAYS = 62


class EventCreate(BaseModel):
    title: str
    date: str
    time: str | None = None
    repeat: str = "never"


class EventPatch(BaseModel):
    title: str | None = None
    date: str | None = None
    time: str | None = None
    repeat: str | None = None


def event_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "date": row["date"],
        "time": row["time"],
        "repeat": row["repeat"],
        "created_at": row["created_at"],
    }


def _parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise HTTPException(422, f"{label} must be YYYY-MM-DD") from e


@router.get("/events")
async def get_events(request: Request, start: str | None = None, end: str | None = None):
    if "text/event-stream" in request.headers.get("accept", ""):
        return await stream_events(request)
    first = _parse_date(start, "start") if start is not None else date.today()
    last = _parse_date(end, "end") if end is not None else first + timedelta(days=6)
    if last < first:
        raise HTTPException(422, "end must not be before start")
    if (last - first).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(422, f"range must be at most {MAX_RANGE_DAYS} days")
    conn = db.connect(request.app.state.db_path)
    try:
        rows = db.list_events(conn)
    finally:
        conn.close()
    return expand(rows, first, last)


@router.post("/events", status_code=201)
def create_event(body: EventCreate, conn: Conn):
    try:
        row = db.create_event(conn, body.title, body.date, body.time, body.repeat)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    events.publish("events_changed")
    return event_dict(row)


@router.patch("/events/{id}")
def patch_event(id: int, body: EventPatch, conn: Conn):
    if db.get_event(conn, id) is None:
        raise HTTPException(404, "event not found")
    fields = {
        k: getattr(body, k)
        for k in body.model_fields_set
        if k == "time" or getattr(body, k) is not None
    }
    try:
        row = db.update_event(conn, id, **fields)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    events.publish("events_changed")
    return event_dict(row)


@router.delete("/events/{id}", status_code=204)
def delete_event(id: int, conn: Conn):
    if db.delete_event(conn, id) is None:
        raise HTTPException(404, "event not found")
    events.publish("events_changed")
    return Response(status_code=204)
