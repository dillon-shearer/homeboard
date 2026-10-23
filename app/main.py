import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db, settings
from app.routes_photos import router as photos_router

APP_DIR = Path(__file__).resolve().parent


class ImmutableStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect(app.state.db_path)
    try:
        db.migrate(conn)
    finally:
        conn.close()
    yield


templates = Jinja2Templates(directory=APP_DIR / "templates")


def create_app(data_dir=None) -> FastAPI:
    data_dir = Path(data_dir) if data_dir is not None else settings.DATA_DIR
    db_path = data_dir / settings.DB_PATH.name
    photos_dir = data_dir / settings.PHOTOS_DIR.name
    thumbs_dir = data_dir / settings.THUMBS_DIR.name
    for d in (data_dir, photos_dir, thumbs_dir):
        d.mkdir(parents=True, exist_ok=True)

    app = FastAPI(lifespan=lifespan)
    app.state.db_path = db_path
    app.state.photos_dir = photos_dir
    app.state.thumbs_dir = thumbs_dir
    app.include_router(photos_router)
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
    app.mount("/photos", ImmutableStaticFiles(directory=photos_dir), name="photos")
    app.mount("/thumbs", ImmutableStaticFiles(directory=thumbs_dir), name="thumbs")

    @app.get("/healthz")
    def healthz():
        try:
            conn = db.connect(app.state.db_path)
            try:
                conn.execute("select 1").fetchone()
            finally:
                conn.close()
            if not (photos_dir.is_dir() and os.access(photos_dir, os.R_OK)):
                raise OSError("photos dir not readable")
        except (sqlite3.Error, OSError):
            return JSONResponse({"ok": False}, status_code=503)
        return {"ok": True}

    @app.get("/", response_class=PlainTextResponse)
    def index():
        return "homeboard"

    return app


app = create_app()
