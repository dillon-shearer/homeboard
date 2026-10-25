import json
import urllib.error
import urllib.request

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/sound")

HELPER_URL = "http://127.0.0.1:8002"
TIMEOUT_S = 5
CONNECT_TIMEOUT_S = 40


def helper(method: str, path: str, timeout: float = TIMEOUT_S) -> JSONResponse:
    data = b"{}" if method == "POST" else None
    req = urllib.request.Request(
        HELPER_URL + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return JSONResponse(json.loads(resp.read() or b"{}"), status_code=resp.status)
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except ValueError:
            body = {"detail": "helper error"}
        return JSONResponse(body, status_code=e.code)
    except (OSError, ValueError):
        return JSONResponse({"detail": "helper down"}, status_code=503)


@router.get("/status")
def sound_status():
    return helper("GET", "/status")


@router.post("/connect")
def sound_connect():
    return helper("POST", "/connect", CONNECT_TIMEOUT_S)


@router.post("/disconnect")
def sound_disconnect():
    return helper("POST", "/disconnect")


@router.post("/reboot")
def sound_reboot():
    return helper("POST", "/reboot")
