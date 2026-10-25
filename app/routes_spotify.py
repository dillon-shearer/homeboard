import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app import db, events, spotify
from app.auth import is_tunnel
from app.routes_photos import Conn

router = APIRouter(prefix="/api/spotify")


class Volume(BaseModel):
    percent: int = Field(ge=0, le=100)


class PlaylistId(BaseModel):
    id: str = Field(min_length=1)


class PlayerEvent(BaseModel):
    type: str | None = None
    status: str | None = None


class ShownPlaylist(BaseModel):
    id: str = Field(min_length=1)
    name: str | None = None
    cover: str | None = None


def redirect_uri(request: Request) -> str:
    host = os.environ.get("HOMEBOARD_HOST")
    if host:
        return f"https://{host}{spotify.CALLBACK_PATH}"
    return str(request.base_url).rstrip("/") + spotify.CALLBACK_PATH


def _data_dir(request: Request):
    return request.app.state.data_dir


def _call(fn, *args):
    try:
        return fn(*args)
    except spotify.NotLinked as e:
        return {"linked": False, "reason": str(e)}
    except spotify.SpotifyError as e:
        return JSONResponse({"detail": e.detail}, status_code=e.status)
    except OSError:
        return JSONResponse({"detail": "spotify unreachable"}, status_code=503)


def _command(request: Request, fn, *args):
    data_dir = _data_dir(request)
    before = spotify.seen_now()
    try:
        return _call(fn, data_dir, *args)
    finally:
        spotify.settle(data_dir, events.publish, before)


@router.get("/login")
def login(request: Request):
    try:
        url = spotify.authorize_url(redirect_uri(request))
    except spotify.NotLinked as e:
        return {"linked": False, "reason": str(e)}
    return RedirectResponse(url, status_code=303)


@router.get("/callback")
def callback(request: Request, state: str | None = None, code: str | None = None):
    if not spotify.check_state(state):
        return JSONResponse({"detail": "bad state"}, status_code=400)
    if not code:
        return RedirectResponse("/admin/settings", status_code=303)
    out = _call(spotify.exchange_code, _data_dir(request), code, redirect_uri(request))
    if out is not None:
        return out
    return RedirectResponse("/admin/settings", status_code=303)


@router.post("/unlink")
def unlink(request: Request):
    spotify.forget(_data_dir(request))
    return spotify.linked(_data_dir(request))


@router.get("/now")
def now(request: Request, fresh: bool = False):
    if fresh:
        return _call(spotify.now_playing, _data_dir(request), True)
    return _call(spotify.now_playing, _data_dir(request))


@router.get("/me")
def me(request: Request):
    return _call(spotify.profile, _data_dir(request))


@router.post("/event")
def player_event(body: PlayerEvent, request: Request):
    host = request.client.host if request.client else None
    if is_tunnel(request.scope) or host not in events.LOCAL_HOSTS:
        return JSONResponse({"detail": "local only"}, status_code=403)
    spotify.settle(_data_dir(request), events.publish)
    spotify.clear_now()
    events.publish("spotify_changed")
    return {"ok": True}


@router.post("/play")
def play(request: Request):
    return _command(request, spotify.control, "PUT", "/me/player/play")


@router.post("/play-here")
def play_here(request: Request):
    return _command(request, spotify.play_here)


@router.post("/pause")
def pause(request: Request):
    return _command(request, spotify.control, "PUT", "/me/player/pause")


@router.post("/next")
def next_track(request: Request):
    return _command(request, spotify.control, "POST", "/me/player/next")


@router.post("/previous")
def previous_track(request: Request):
    return _command(request, spotify.control, "POST", "/me/player/previous")


@router.put("/volume")
def volume(body: Volume, request: Request):
    return _call(
        spotify.control,
        _data_dir(request),
        "PUT",
        "/me/player/volume",
        None,
        {"volume_percent": body.percent},
    )


@router.get("/playlists")
def playlists(request: Request):
    out = _call(spotify.playlists, _data_dir(request))
    return {"linked": True, "playlists": out} if isinstance(out, list) else out


@router.post("/play-playlist")
def play_playlist(body: PlaylistId, request: Request):
    return _command(request, spotify.play_playlist, body.id)


@router.get("/shown")
def shown(conn: Conn):
    return [dict(r) for r in db.list_spotify_playlists(conn)]


@router.put("/shown")
def set_shown(body: list[ShownPlaylist], conn: Conn):
    rows = db.set_spotify_playlists(conn, [p.model_dump() for p in body])
    return [dict(r) for r in rows]
