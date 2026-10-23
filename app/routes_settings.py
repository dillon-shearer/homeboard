import asyncio
import json
import shutil
import socket
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import __version__, db, events
from app.routes_photos import Conn

router = APIRouter(prefix="/api")

KEEPALIVE_S = 15


class SettingsPatch(BaseModel):
    interval_s: int | None = None
    transition: bool | None = None
    fit: str | None = None
    shuffle: bool | None = None
    captions: bool | None = None


def settings_dict(row: sqlite3.Row) -> dict:
    return {
        "interval_s": row["interval_s"],
        "transition": bool(row["transition"]),
        "fit": row["fit"],
        "shuffle": bool(row["shuffle"]),
        "captions": bool(row["captions"]),
        "updated_at": row["updated_at"],
    }


def _ip(hostname: str) -> str:
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_INET)
    except OSError:
        return ""
    for info in infos:
        addr = info[4][0]
        if not addr.startswith("127."):
            return addr
    return ""


@router.get("/settings")
def get_settings(conn: Conn):
    return settings_dict(db.get_settings(conn))


@router.put("/settings")
def put_settings(body: SettingsPatch, conn: Conn):
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    try:
        row = db.update_settings(conn, **fields)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    events.publish("settings_changed")
    return settings_dict(row)


@router.get("/events")
async def stream_events(request: Request):
    conn = db.connect(request.app.state.db_path)
    try:
        hello = json.dumps(settings_dict(db.get_settings(conn)))
    finally:
        conn.close()
    host = request.client.host if request.client else None
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
        "hostname": hostname,
        "ip": _ip(hostname),
        "disk_free_bytes": shutil.disk_usage(request.app.state.photos_dir).free,
        "version": __version__,
    }
