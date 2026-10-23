import asyncio
import hashlib
import hmac
import html
import os
import secrets
import sys
import time
from collections import deque
from pathlib import Path
from urllib.parse import parse_qs, quote

from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

TUNNEL_HEADER = b"x-homeboard-tunnel"
COOKIE = "homeboard_auth"
COOKIE_MAX_AGE = 365 * 24 * 3600
ITERATIONS = 200_000
FAIL_WINDOW_S = 15 * 60
FAILS_PER_CLIENT = 5
FAILS_TOTAL = 30
WRONG_DELAY_S = 2.0
MAX_FORM_BYTES = 4096


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, expected = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest.hex(), expected)


def cookie_value(stored: str) -> str:
    return hmac.new(stored.encode(), b"homeboard-tunnel-session", hashlib.sha256).hexdigest()


def password_file() -> Path | None:
    explicit = os.environ.get("HOMEBOARD_PASSWORD_FILE")
    if explicit:
        return Path(explicit)
    creds = os.environ.get("CREDENTIALS_DIRECTORY")
    if creds:
        return Path(creds) / "tunnel-password"
    return None


def stored_hash() -> str | None:
    path = password_file()
    if path is None:
        return None
    try:
        text = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return text or None


def is_tunnel(scope) -> bool:
    return any(k == TUNNEL_HEADER and v == b"1" for k, v in scope.get("headers", []))


def _header(scope, name: bytes) -> str:
    for k, v in scope.get("headers", []):
        if k == name:
            return v.decode("latin-1")
    return ""


def _cookies(scope) -> dict[str, str]:
    out = {}
    for k, v in scope.get("headers", []):
        if k == b"cookie":
            for part in v.decode("latin-1").split(";"):
                name, _, value = part.strip().partition("=")
                if name:
                    out[name] = value
    return out


def _safe_next(value: str) -> str:
    if value.startswith("/") and not value.startswith(("//", "/\\")) and value != "/login":
        return value
    return "/admin"


LOGIN_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="robots" content="noindex">
<title>Home Board</title>
<style>
body{{font:16px system-ui,sans-serif;margin:0;display:flex;min-height:100vh;
align-items:center;justify-content:center;background:Canvas;color:CanvasText}}
form{{width:min(320px,90vw);display:flex;flex-direction:column;gap:12px}}
h1{{font-size:24px;margin:0}}
input,button{{font:inherit;padding:12px;border-radius:10px;border:1px solid GrayText}}
button{{background:#007aff;color:#fff;border:0}}
.error{{color:#d70015;margin:0}}
</style>
</head>
<body>
<form method="post" action="/login">
<h1>Home Board</h1>
<p class="error"{hidden}>{message}</p>
<input type="hidden" name="next" value="{next}">
<input type="password" name="password" autocomplete="current-password"
placeholder="Password" aria-label="Password" required autofocus>
<button type="submit">Open</button>
</form>
</body>
</html>
"""


def login_page(next_path: str, message: str = "", status: int = 200) -> HTMLResponse:
    body = LOGIN_PAGE.format(
        hidden="" if message else " hidden",
        message=html.escape(message),
        next=html.escape(next_path),
    )
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


def private_cache(send):
    async def wrapped(message):
        if message["type"] == "http.response.start":
            headers = [(k, v) for k, v in message.get("headers", []) if k != b"cache-control"]
            old = dict(message.get("headers", [])).get(b"cache-control", b"no-cache")
            parts = [p.strip() for p in old.split(b",") if p.strip() not in (b"public", b"")]
            headers.append((b"cache-control", b", ".join([b"private", *parts])))
            message = {**message, "headers": headers}
        await send(message)

    return wrapped


class TunnelGate:
    def __init__(self, app):
        self.app = app
        self.fails: dict[str, deque] = {}
        self.all_fails: deque = deque()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not is_tunnel(scope):
            await self.app(scope, receive, send)
            return
        send = private_cache(send)
        stored = stored_hash()
        if scope["path"] == "/login":
            response = await self.login(scope, receive, stored)
        elif stored and hmac.compare_digest(_cookies(scope).get(COOKIE, ""), cookie_value(stored)):
            await self.app(scope, receive, send)
            return
        else:
            response = self.deny(scope)
        await response(scope, receive, send)

    def deny(self, scope) -> Response:
        path = scope["path"]
        if scope["method"] == "GET" and not path.startswith(("/api/", "/photos/", "/thumbs/")):
            query = scope.get("query_string", b"").decode("latin-1")
            target = path + ("?" + query if query else "")
            return RedirectResponse(
                "/login?next=" + quote(target, safe="/"),
                status_code=303,
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse({"detail": "password needed"}, status_code=401)

    def _prune(self, now: float) -> None:
        cutoff = now - FAIL_WINDOW_S
        while self.all_fails and self.all_fails[0] < cutoff:
            self.all_fails.popleft()
        for key in list(self.fails):
            q = self.fails[key]
            while q and q[0] < cutoff:
                q.popleft()
            if not q:
                del self.fails[key]

    async def login(self, scope, receive, stored: str | None) -> Response:
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        if scope["method"] != "POST":
            return login_page(_safe_next(query.get("next", [""])[0]))
        body = b""
        more = True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                break
            body += message.get("body", b"")
            more = message.get("more_body", False)
            if len(body) > MAX_FORM_BYTES:
                return login_page("/admin", "That did not work. Try again.", 400)
        form = parse_qs(body.decode("utf-8", "replace"))
        next_path = _safe_next(form.get("next", [""])[0])
        password = form.get("password", [""])[0]
        if not stored:
            return login_page(next_path, "No password is set on the board yet.", 503)
        client = _header(scope, b"cf-connecting-ip") or "unknown"
        now = time.monotonic()
        self._prune(now)
        if (
            len(self.fails.get(client, ())) >= FAILS_PER_CLIENT
            or len(self.all_fails) >= FAILS_TOTAL
        ):
            return login_page(next_path, "Too many wrong tries. Wait 15 minutes.", 429)
        if not check_password(password, stored):
            self.fails.setdefault(client, deque()).append(now)
            self.all_fails.append(now)
            await asyncio.sleep(WRONG_DELAY_S)
            return login_page(next_path, "Wrong password.", 401)
        self.fails.pop(client, None)
        response = RedirectResponse(next_path, status_code=303)
        response.set_cookie(
            COOKIE,
            cookie_value(stored),
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return response


def main() -> None:
    password = sys.stdin.read().rstrip("\r\n")
    if not password:
        sys.exit("No password given")
    print(hash_password(password))


if __name__ == "__main__":
    main()
