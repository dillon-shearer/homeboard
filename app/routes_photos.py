import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import db, events, images

router = APIRouter(prefix="/api/photos")


class PhotoPatch(BaseModel):
    enabled: bool | None = None
    caption: str | None = None


class PhotoOrder(BaseModel):
    ids: list[int]


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
    for f in files:
        try:
            data = f.file.read(images.MAX_UPLOAD_BYTES + 1)
            if len(data) > images.MAX_UPLOAD_BYTES:
                raise images.ImageError("too large")
            p = images.process_upload(data, state.photos_dir, state.thumbs_dir)
        except images.ImageError as e:
            errors.append({"name": f.filename, "error": str(e)})
            continue
        except OSError:
            errors.append({"name": f.filename, "error": "processing failed"})
            continue
        try:
            row = db.insert_photo(
                conn, p.filename, p.thumb, p.width, p.height, uploaded_by=uploaded_by
            )
        except sqlite3.Error:
            images.delete_files(p.filename, p.thumb, state.photos_dir, state.thumbs_dir)
            errors.append({"name": f.filename, "error": "save failed"})
            continue
        created.append(photo_dict(row))
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
