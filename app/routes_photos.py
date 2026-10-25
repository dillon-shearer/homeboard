import asyncio
import hashlib
import logging
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import db, events, images

router = APIRouter(prefix="/api/photos")
CAPTION_MAX = 120
log = logging.getLogger(__name__)


class PhotoPatch(BaseModel):
    enabled: bool | None = None
    caption: Annotated[str | None, Field(max_length=CAPTION_MAX)] = None
    focus_x: Annotated[int, Field(ge=0, le=100, strict=True)] | None = None
    focus_y: Annotated[int, Field(ge=0, le=100, strict=True)] | None = None


class PhotoOrder(BaseModel):
    ids: list[int]


class UploadGate:
    def __init__(self, app):
        self.app = app
        self.slots = asyncio.Semaphore(images.MAX_ADMITTED)

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"].rstrip("/") != router.prefix
        ):
            await self.app(scope, receive, send)
            return
        length = dict(scope["headers"]).get(b"content-length", b"")
        if not length.isdigit():
            await JSONResponse({"detail": "length required"}, status_code=411)(scope, receive, send)
            return
        if int(length) > images.MAX_REQUEST_BYTES:
            await JSONResponse({"detail": "too large"}, status_code=413)(scope, receive, send)
            return
        async with self.slots:
            await self.app(scope, receive, send)


def get_conn(request: Request):
    conn = db.connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def photo_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "url": f"/photos/{row['filename']}",
        "thumb_url": f"/thumbs/{row['thumb']}",
        "caption": row["caption"],
        "enabled": bool(row["enabled"]),
        "sort_order": row["sort_order"],
        "width": row["width"],
        "height": row["height"],
        "focus_x": row["focus_x"],
        "focus_y": row["focus_y"],
        "uploaded_at": row["uploaded_at"],
    }


@router.get("")
def list_photos(conn: Conn, all: bool = False):
    return [photo_dict(r) for r in db.list_photos(conn, enabled_only=not all)]


@router.post("")
def upload_photos(
    request: Request,
    files: Annotated[list[UploadFile], File()],
    conn: Conn,
):
    state = request.app.state
    uploaded_by = (request.headers.get("user-agent") or "")[:200] or None
    created, errors = [], []
    try:
        for f in files:
            try:
                data = f.file.read(images.MAX_UPLOAD_BYTES + 1)
                if len(data) > images.MAX_UPLOAD_BYTES:
                    raise images.ImageError("too large")
                digest = hashlib.sha256(data).hexdigest()
                if db.has_source_sha256(conn, digest):
                    raise images.ImageError("already on the board")
                p = images.process_upload(data, state.photos_dir, state.thumbs_dir)
            except images.ImageError as e:
                errors.append({"name": f.filename, "error": str(e)})
                continue
            except Exception:
                log.exception("processing %s failed", f.filename)
                errors.append({"name": f.filename, "error": "processing failed"})
                continue
            try:
                row = db.insert_photo(
                    conn,
                    p.filename,
                    p.thumb,
                    p.width,
                    p.height,
                    uploaded_by=uploaded_by,
                    source_sha256=digest,
                )
            except sqlite3.Error:
                images.delete_files(p.filename, p.thumb, state.photos_dir, state.thumbs_dir)
                errors.append({"name": f.filename, "error": "save failed"})
                continue
            created.append(photo_dict(row))
    finally:
        if created:
            events.publish("photos_changed")
    return JSONResponse({"created": created, "errors": errors}, status_code=201 if created else 400)


@router.put("/order")
def set_order(body: PhotoOrder, conn: Conn):
    known = {r["id"] for r in db.list_photos(conn)}
    unknown = [i for i in body.ids if i not in known]
    if unknown:
        raise HTTPException(400, f"unknown ids: {unknown}")
    db.set_order(conn, body.ids)
    events.publish("photos_changed")
    return [photo_dict(r) for r in db.list_photos(conn)]


@router.patch("/{id}")
def patch_photo(id: int, body: PhotoPatch, conn: Conn):
    if db.get_photo(conn, id) is None:
        raise HTTPException(404, "photo not found")
    fields = {}
    if "caption" in body.model_fields_set:
        fields["caption"] = body.caption
    if "enabled" in body.model_fields_set and body.enabled is not None:
        fields["enabled"] = body.enabled
    for k in ("focus_x", "focus_y"):
        if getattr(body, k) is not None:
            fields[k] = getattr(body, k)
    row = db.update_photo(conn, id, **fields)
    events.publish("photos_changed")
    return photo_dict(row)


@router.delete("/{id}", status_code=204)
def delete_photo(id: int, request: Request, conn: Conn):
    row = db.delete_photo(conn, id)
    if row is None:
        raise HTTPException(404, "photo not found")
    state = request.app.state
    images.delete_files(row["filename"], row["thumb"], state.photos_dir, state.thumbs_dir)
    events.publish("photos_changed")
    return Response(status_code=204)
