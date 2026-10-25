import base64
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1"
CALLBACK_PATH = "/api/spotify/callback"
SCOPES = (
    "user-read-playback-state user-modify-playback-state "
    "playlist-read-private playlist-read-collaborative"
)
DEVICE_NAME = "Home Board"
TOKEN_NAME = "spotify.json"
TIMEOUT_S = 5
TOKEN_MARGIN_S = 60
NOW_EVERY_S = 3
STATE_TTL_S = 600

log = logging.getLogger(__name__)
_lock = threading.Lock()
_now_lock = threading.Lock()
_token: dict = {}
_now: dict = {}
_profile: dict = {}
_states: dict[str, float] = {}


class NotLinked(Exception):
    pass


class SpotifyError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def fetch(method: str, url: str, headers=None, body: bytes | None = None):
    req = urllib.request.Request(
        url, data=body, method=method, headers={"User-Agent": "homeboard", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, None


def _clock() -> float:
    return time.monotonic()


def credentials() -> tuple[str, str] | None:
    values = {}
    creds = os.environ.get("CREDENTIALS_DIRECTORY")
    if creds:
        try:
            text = (Path(creds) / "spotify").read_text(encoding="utf-8")
        except OSError:
            text = ""
        for line in text.splitlines():
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    cid = values.get("SPOTIFY_CLIENT_ID") or os.environ.get("SPOTIFY_CLIENT_ID")
    secret = values.get("SPOTIFY_CLIENT_SECRET") or os.environ.get("SPOTIFY_CLIENT_SECRET")
    return (cid, secret) if cid and secret else None


def _creds() -> tuple[str, str]:
    creds = credentials()
    if creds is None:
        raise NotLinked("no credentials")
    return creds


def token_path(data_dir) -> Path:
    return Path(data_dir) / TOKEN_NAME


def _refresh_token(data_dir) -> str | None:
    try:
        return json.loads(token_path(data_dir).read_text(encoding="utf-8")).get("refresh_token")
    except (OSError, ValueError, AttributeError):
        return None


def _save_refresh_token(data_dir, refresh: str) -> None:
    path = token_path(data_dir)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"refresh_token": refresh}), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def forget(data_dir=None) -> None:
    with _lock:
        if data_dir is not None:
            token_path(data_dir).unlink(missing_ok=True)
        _token.clear()
        _profile.clear()
    with _now_lock:
        _now.clear()


def linked(data_dir) -> dict:
    if credentials() is None:
        return {"linked": False, "reason": "no credentials"}
    if _refresh_token(data_dir) is None:
        return {"linked": False, "reason": "not linked"}
    return {"linked": True}


def authorize_url(redirect_uri: str) -> str:
    cid, _ = _creds()
    state = secrets.token_urlsafe(24)
    now = _clock()
    with _lock:
        for s, expires in list(_states.items()):
            if expires < now:
                del _states[s]
        _states[state] = now + STATE_TTL_S
    query = {
        "client_id": cid,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


def check_state(state: str | None) -> bool:
    with _lock:
        expires = _states.pop(state, None) if state else None
    return expires is not None and expires >= _clock()


def _token_call(form: dict) -> dict:
    cid, secret = _creds()
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    status, data = fetch(
        "POST",
        TOKEN_URL,
        {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        urlencode(form).encode(),
    )
    if status == 400:
        raise NotLinked("sign in again")
    if status >= 400 or not isinstance(data, dict) or "access_token" not in data:
        raise SpotifyError(502, "spotify token error")
    return data


def _keep_token(data: dict) -> str:
    _token["access"] = data["access_token"]
    _token["expires"] = _clock() + int(data.get("expires_in", 3600))
    return _token["access"]


def exchange_code(data_dir, code: str, redirect_uri: str) -> None:
    data = _token_call(
        {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}
    )
    if not data.get("refresh_token"):
        raise SpotifyError(502, "spotify gave no refresh token")
    with _lock:
        _save_refresh_token(data_dir, data["refresh_token"])
        _keep_token(data)
        _profile.clear()
    with _now_lock:
        _now.clear()


def access_token(data_dir) -> str:
    _creds()
    with _lock:
        if _token.get("access") and _token["expires"] - TOKEN_MARGIN_S > _clock():
            return _token["access"]
        refresh = _refresh_token(data_dir)
        if refresh is None:
            raise NotLinked("not linked")
        data = _token_call({"grant_type": "refresh_token", "refresh_token": refresh})
        if data.get("refresh_token") and data["refresh_token"] != refresh:
            _save_refresh_token(data_dir, data["refresh_token"])
        return _keep_token(data)


def api(data_dir, method: str, path: str, payload=None, query=None):
    url = path if path.startswith("https://") else API_URL + path
    if query:
        url += "?" + urlencode(query)
    body = json.dumps(payload).encode() if payload is not None else None
    if body is None and method != "GET":
        body = b""
    for attempt in (1, 2):
        headers = {"Authorization": f"Bearer {access_token(data_dir)}"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        status, data = fetch(method, url, headers, body)
        if status == 401 and attempt == 1:
            with _lock:
                _token.clear()
            continue
        break
    if status == 404 and path.startswith("/me/player"):
        raise SpotifyError(409, "no active device")
    if status >= 400:
        message = data.get("error") if isinstance(data, dict) else None
        if isinstance(message, dict):
            message = message.get("message")
        player = path.startswith("/me/player") and status == 403
        raise SpotifyError(409 if player else 502, str(message or f"spotify error {status}"))
    return data


def control(data_dir, method: str, path: str, payload=None, query=None) -> dict:
    api(data_dir, method, path, payload, query)
    with _now_lock:
        _now.clear()
    return {"linked": True, "ok": True}


def _largest(images) -> str | None:
    images = [i for i in images or [] if i.get("url")]
    if not images:
        return None
    return max(images, key=lambda i: (i.get("width") or 0) * (i.get("height") or 0))["url"]


def _empty(stale=False) -> dict:
    return {
        "linked": True,
        "playing": False,
        "on_board": False,
        "track": None,
        "artist": None,
        "album": None,
        "cover": None,
        "progress_ms": None,
        "duration_ms": None,
        "volume": None,
        "device": None,
        "stale": stale,
    }


def _shape(data) -> dict:
    if not isinstance(data, dict) or not data.get("item"):
        return _empty()
    item = data["item"]
    album = item.get("album") or {}
    device = data.get("device") or {}
    return {
        "linked": True,
        "playing": bool(data.get("is_playing")),
        "on_board": device.get("name") == DEVICE_NAME,
        "track": item.get("name"),
        "artist": ", ".join(a["name"] for a in item.get("artists") or [] if a.get("name")) or None,
        "album": album.get("name"),
        "cover": _largest(album.get("images")),
        "progress_ms": data.get("progress_ms"),
        "duration_ms": item.get("duration_ms"),
        "volume": device.get("volume_percent"),
        "device": device.get("name"),
        "stale": False,
    }


def now_playing(data_dir) -> dict:
    with _now_lock:
        if _now.get("data") is not None and _clock() - _now["at"] < NOW_EVERY_S:
            return _now["data"]
        _now["at"] = _clock()
        try:
            _now["data"] = _shape(api(data_dir, "GET", "/me/player"))
        except (OSError, SpotifyError) as e:
            log.warning("spotify now playing failed: %s", e)
            last = _now.get("data") or _empty()
            _now["data"] = {**last, "stale": True}
        return _now["data"]


def profile(data_dir) -> dict:
    with _lock:
        if "display_name" in _profile:
            return {"linked": True, "display_name": _profile["display_name"]}
    data = api(data_dir, "GET", "/me") or {}
    name = data.get("display_name") or data.get("id") or None
    with _lock:
        _profile["display_name"] = name
    return {"linked": True, "display_name": name}


def playlists(data_dir) -> list[dict]:
    out = []
    url = "/me/playlists"
    query = {"limit": 50}
    while url:
        data = api(data_dir, "GET", url, query=query) or {}
        for p in data.get("items") or []:
            if not p or not p.get("id"):
                continue
            tracks = p.get("tracks") or p.get("items") or {}
            out.append(
                {
                    "id": p["id"],
                    "name": p.get("name"),
                    "cover": _largest(p.get("images")),
                    "tracks": tracks.get("total") if isinstance(tracks, dict) else None,
                }
            )
        url, query = data.get("next"), None
    return out


def play_playlist(data_dir, playlist_id: str) -> dict:
    devices = (api(data_dir, "GET", "/me/player/devices") or {}).get("devices") or []
    board = next((d for d in devices if d.get("name") == DEVICE_NAME), None)
    if board is None:
        raise SpotifyError(409, "Home Board speaker not found")
    if not board.get("is_active"):
        api(data_dir, "PUT", "/me/player", {"device_ids": [board["id"]], "play": False})
    return control(
        data_dir,
        "PUT",
        "/me/player/play",
        {"context_uri": f"spotify:playlist:{playlist_id}"},
        {"device_id": board["id"]},
    )
