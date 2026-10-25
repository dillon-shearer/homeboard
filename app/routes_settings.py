import asyncio
import json
import shutil
import socket
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from app import __version__, db, events, weather
from app.auth import is_tunnel
from app.routes_photos import Conn

router = APIRouter(prefix="/api")

KEEPALIVE_S = 15


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interval_s: int | None = None
    transition: bool | None = None
    fit: str | None = None
    shuffle: bool | None = None
    captions: bool | None = None
    channel: str | None = None
    channel_timeout_min: int | None = None
    screen_id: int | None = None
    clock_size: int | None = None
    caption_size: int | None = None
    clock_pos: str | None = None
    caption_pos: str | None = None
    clock_on_top: bool | None = None
    date_size: int | None = None
    date_pos: str | None = None
    date_on_top: bool | None = None
    date_show: bool | None = None
    brightness: int | None = None
    weather_location: str | None = None
    speaker_idle_minutes: int | None = None
    speaker_volume: int | None = None


class DisplayCurrent(BaseModel):
    id: int | None = None


def settings_dict(row: sqlite3.Row) -> dict:
    return {
        "interval_s": row["interval_s"],
        "transition": bool(row["transition"]),
        "fit": row["fit"],
        "shuffle": bool(row["shuffle"]),
        "captions": bool(row["captions"]),
        "channel": row["channel"],
        "channel_timeout_min": row["channel_timeout_min"],
        "screen_id": row["screen_id"],
        "clock_size": row["clock_size"],
        "caption_size": row["caption_size"],
        "clock_pos": row["clock_pos"],
        "caption_pos": row["caption_pos"],
        "clock_on_top": bool(row["clock_on_top"]),
        "date_size": row["date_size"],
        "date_pos": row["date_pos"],
        "date_on_top": bool(row["date_on_top"]),
        "date_show": bool(row["date_show"]),
        "brightness": row["brightness"],
        "weather_location": row["weather_location"],
        "weather_lat": row["weather_lat"],
        "weather_lon": row["weather_lon"],
        "weather_name": row["weather_name"],
        "speaker_idle_minutes": row["speaker_idle_minutes"],
        "speaker_volume": row["speaker_volume"],
        "updated_at": row["updated_at"],
    }


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))
            addr = s.getsockname()[0]
    except OSError:
        return ""
    if addr.startswith("127.") or addr == "0.0.0.0":
        return ""
    return addr


@router.get("/settings")
def get_settings(conn: Conn):
    return settings_dict(db.get_settings(conn))


@router.put("/settings")
def put_settings(body: SettingsPatch, request: Request, conn: Conn):
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if "screen_id" in body.model_fields_set:
        fields["screen_id"] = body.screen_id
    try:
        row = db.update_settings(conn, **fields)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if "weather_location" in fields:
        weather.drop_cache(request.app.state.data_dir)
    events.publish("settings_changed")
    return settings_dict(row)


@router.get("/weather")
def get_weather(request: Request, conn: Conn):
    return weather.get_weather(conn, request.app.state.data_dir)


@router.get("/display/current")
def get_display_current(conn: Conn):
    return {"id": db.get_current_photo_id(conn)}


@router.put("/display/current")
def put_display_current(body: DisplayCurrent, conn: Conn):
    db.set_current_photo_id(conn, body.id)
    return {"id": body.id}


async def stream_events(request: Request):
    conn = db.connect(request.app.state.db_path)
    try:
        hello = json.dumps(settings_dict(db.get_settings(conn)))
    finally:
        conn.close()
    host = request.client.host if request.client and not is_tunnel(request.scope) else None
    limit = request.app.state.sse_limit

    async def gen():
        async with events.subscribe(host) as queue:
            yield f"event: hello\ndata: {hello}\n\n"
            sent = 0
            while limit is None or sent < limit:
                try:
                    name = await asyncio.wait_for(queue.get(), KEEPALIVE_S)
                except TimeoutError:
                    yield ": keepalive\n\n"
                else:
                    yield f"event: {name}\ndata: {{}}\n\n"
                sent += 1

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


@router.get("/status")
def status(request: Request, conn: Conn):
    hostname = socket.gethostname()
    return {
        "display_connected": events.display_connected(),
        "photo_count": len(db.list_photos(conn)),
        "current_photo_id": db.get_current_photo_id(conn),
        "channel": db.get_settings(conn)["channel"],
        "screen_id": db.get_settings(conn)["screen_id"],
        "hostname": hostname,
        "ip": lan_ip(),
        "disk_free_bytes": shutil.disk_usage(request.app.state.photos_dir).free,
        "version": __version__,
    }
