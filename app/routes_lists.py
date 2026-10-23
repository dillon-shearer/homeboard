import sqlite3

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from app import db, events
from app.routes_photos import Conn

router = APIRouter(prefix="/api")


class ListCreate(BaseModel):
    name: str
    kind: str = "checklist"


class ListPatch(BaseModel):
    name: str | None = None
    kind: str | None = None
    show_on_tv: bool | None = None
    screen_id: int | None = None


class ScreenCreate(BaseModel):
    name: str


class ScreenPatch(BaseModel):
    name: str | None = None


class ItemCreate(BaseModel):
    text: str


class ItemPatch(BaseModel):
    text: str | None = None
    done: bool | None = None
    pinned: bool | None = None


class Order(BaseModel):
    ids: list[int]


def item_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "list_id": row["list_id"],
        "text": row["text"],
        "done": bool(row["done"]),
        "pinned": bool(row["pinned"]),
        "sort_order": row["sort_order"],
        "created_at": row["created_at"],
    }


def list_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "show_on_tv": row["screen_id"] is not None,
        "screen_id": row["screen_id"],
        "sort_order": row["sort_order"],
        "created_at": row["created_at"],
        "items": [item_dict(r) for r in db.list_items(conn, row["id"])],
    }


def all_lists(conn: sqlite3.Connection) -> list[dict]:
    return [list_dict(conn, r) for r in db.list_lists(conn)]


def screen_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "sort_order": row["sort_order"],
        "list_ids": db.screen_list_ids(conn, row["id"]),
    }


def all_screens(conn: sqlite3.Connection) -> list[dict]:
    return [screen_dict(conn, r) for r in db.list_screens(conn)]


def _set_fields(body: BaseModel) -> dict:
    return {k: getattr(body, k) for k in body.model_fields_set if getattr(body, k) is not None}


def _require_list(conn: sqlite3.Connection, id: int) -> sqlite3.Row:
    row = db.get_list(conn, id)
    if row is None:
        raise HTTPException(404, "list not found")
    return row


@router.get("/lists")
def get_lists(conn: Conn):
    return all_lists(conn)


@router.post("/lists", status_code=201)
def create_list(body: ListCreate, conn: Conn):
    try:
        row = db.create_list(conn, body.name, body.kind)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    events.publish("lists_changed")
    return list_dict(conn, row)


@router.put("/lists/order")
def order_lists(body: Order, conn: Conn):
    try:
        db.reorder_lists(conn, body.ids)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    events.publish("lists_changed")
    return all_lists(conn)


@router.patch("/lists/{id}")
def patch_list(id: int, body: ListPatch, conn: Conn):
    _require_list(conn, id)
    fields = _set_fields(body)
    if "screen_id" in body.model_fields_set:
        fields["screen_id"] = body.screen_id
    try:
        row = db.update_list(conn, id, **fields)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    events.publish("lists_changed")
    return list_dict(conn, row)


@router.delete("/lists/{id}", status_code=204)
def delete_list(id: int, conn: Conn):
    if db.delete_list(conn, id) is None:
        raise HTTPException(404, "list not found")
    events.publish("lists_changed")
    return Response(status_code=204)


@router.post("/lists/{id}/items", status_code=201)
def add_item(id: int, body: ItemCreate, conn: Conn):
    _require_list(conn, id)
    try:
        row = db.add_item(conn, id, body.text)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    events.publish("lists_changed")
    return item_dict(row)


@router.put("/lists/{id}/items/order")
def order_items(id: int, body: Order, conn: Conn):
    row = _require_list(conn, id)
    try:
        db.reorder_items(conn, id, body.ids)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    events.publish("lists_changed")
    return list_dict(conn, row)


@router.post("/lists/{id}/clear-done")
def clear_done(id: int, conn: Conn):
    row = _require_list(conn, id)
    db.clear_done(conn, id)
    events.publish("lists_changed")
    return list_dict(conn, row)


@router.patch("/items/{id}")
def patch_item(id: int, body: ItemPatch, conn: Conn):
    if db.get_item(conn, id) is None:
        raise HTTPException(404, "item not found")
    try:
        row = db.update_item(conn, id, **_set_fields(body))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    events.publish("lists_changed")
    return item_dict(row)


@router.delete("/items/{id}", status_code=204)
def delete_item(id: int, conn: Conn):
    if db.delete_item(conn, id) is None:
        raise HTTPException(404, "item not found")
    events.publish("lists_changed")
    return Response(status_code=204)


@router.get("/screens")
def get_screens(conn: Conn):
    return all_screens(conn)


@router.post("/screens", status_code=201)
def create_screen(body: ScreenCreate, conn: Conn):
    try:
        row = db.create_screen(conn, body.name)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    events.publish("lists_changed")
    return screen_dict(conn, row)


@router.put("/screens/order")
def order_screens(body: Order, conn: Conn):
    try:
        db.reorder_screens(conn, body.ids)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    events.publish("lists_changed")
    return all_screens(conn)


@router.patch("/screens/{id}")
def patch_screen(id: int, body: ScreenPatch, conn: Conn):
    if db.get_screen(conn, id) is None:
        raise HTTPException(404, "screen not found")
    try:
        row = db.update_screen(conn, id, **_set_fields(body))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    events.publish("lists_changed")
    return screen_dict(conn, row)


@router.delete("/screens/{id}", status_code=204)
def delete_screen(id: int, conn: Conn):
    if db.delete_screen(conn, id) is None:
        raise HTTPException(404, "screen not found")
    events.publish("lists_changed")
    return Response(status_code=204)
