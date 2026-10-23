import mimetypes
import os
import socket
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import segno
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db, settings
from app.auth import TunnelGate
from app.routes_calendar import router as calendar_router
from app.routes_lists import router as lists_router
from app.routes_photos import router as photos_router
from app.routes_settings import lan_ip
from app.routes_settings import router as settings_router

APP_DIR = Path(__file__).resolve().parent

mimetypes.add_type("application/manifest+json", ".webmanifest")


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


def static_url(name: str) -> str:
    version = int((APP_DIR / "static" / name).stat().st_mtime)
    return f"/static/{name}?v={version}"


templates.env.globals["static_url"] = static_url


def create_app(data_dir=None) -> FastAPI:
    data_dir = Path(data_dir) if data_dir is not None else settings.DATA_DIR
    db_path = data_dir / settings.DB_PATH.name
    photos_dir = data_dir / settings.PHOTOS_DIR.name
    thumbs_dir = data_dir / settings.THUMBS_DIR.name
    for d in (data_dir, photos_dir, thumbs_dir):
        d.mkdir(parents=True, exist_ok=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db_path = db_path
    app.state.photos_dir = photos_dir
    app.state.thumbs_dir = thumbs_dir
    app.state.sse_limit = None
    app.add_middleware(TunnelGate)
    app.include_router(photos_router)
    app.include_router(settings_router)
    app.include_router(lists_router)
    app.include_router(calendar_router)
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

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        conn = db.connect(app.state.db_path)
        try:
            empty = not db.list_photos(conn, enabled_only=True)
        finally:
            conn.close()
        context = {"empty": empty}
        if empty:
            public_host = os.environ.get("HOMEBOARD_HOST", "")
            if public_host:
                admin_url = f"https://{public_host}/admin"
                ip = ""
            else:
                hostname = socket.gethostname()
                admin_url = f"http://{hostname.split('.')[0]}.local/admin"
                ip = lan_ip()
            context.update(
                admin_url=admin_url,
                ip_url=f"http://{ip}/admin" if ip else "",
                qr_svg=segno.make(admin_url, error="m").svg_inline(
                    omitsize=True, dark="#000", light="#fff", border=2
                ),
            )
        return templates.TemplateResponse(request, "display.html", context)

    @app.get("/admin", response_class=HTMLResponse)
    def admin(request: Request):
        return templates.TemplateResponse(request, "start.html")

    @app.get("/admin/photos", response_class=HTMLResponse)
    def admin_photos(request: Request):
        return templates.TemplateResponse(request, "admin.html")

    @app.get("/admin/about", response_class=HTMLResponse)
    def admin_about(request: Request):
        return templates.TemplateResponse(request, "about.html")

    return app


app = create_app()
