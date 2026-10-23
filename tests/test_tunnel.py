import configparser
import io
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import auth, events
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
NGINX = DEPLOY / "nginx"
TUNNEL = {"X-Homeboard-Tunnel": "1", "CF-Connecting-IP": "203.0.113.5"}


def read(path):
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    return raw.decode("ascii")


@pytest.fixture
def client(tmp_path, monkeypatch):
    hash_file = tmp_path / "tunnel-password"
    hash_file.write_text(auth.hash_password("letmein") + "\n")
    monkeypatch.setenv("HOMEBOARD_PASSWORD_FILE", str(hash_file))
    monkeypatch.setattr(auth, "WRONG_DELAY_S", 0)
    with TestClient(create_app(tmp_path / "data"), base_url="https://board.example.com") as c:
        yield c


def login(client, password, **extra):
    return client.post(
        "/login",
        data={"password": password, "next": "/admin"},
        headers={**TUNNEL, **extra},
        follow_redirects=False,
    )


def test_hash_round_trip():
    stored = auth.hash_password("p4ss word!")
    assert stored.startswith("pbkdf2_sha256$")
    assert auth.check_password("p4ss word!", stored)
    assert not auth.check_password("password", stored)
    assert not auth.check_password("x", "garbage")


def test_lan_and_kiosk_need_no_password(client):
    assert client.get("/admin").status_code == 200
    assert client.get("/api/photos").status_code == 200
    assert client.get("/admin", headers={"X-Homeboard-Tunnel": ""}).status_code == 200


@pytest.mark.parametrize("path", ["/admin", "/", "/static/admin.css"])
def test_tunnel_pages_redirect_to_login(client, path):
    r = client.get(path, headers=TUNNEL, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?next=" + path)
    assert "private" in r.headers["cache-control"]


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/photos"),
        ("GET", "/api/status"),
        ("GET", "/api/events"),
        ("GET", "/photos/1.jpg"),
        ("GET", "/thumbs/1.jpg"),
        ("POST", "/api/photos"),
        ("PATCH", "/api/settings"),
        ("DELETE", "/api/photos/1"),
    ],
)
def test_tunnel_api_and_photos_refused_without_password(client, method, path):
    r = client.request(method, path, headers=TUNNEL, follow_redirects=False)
    assert r.status_code == 401


def test_login_page_shows_form(client):
    r = client.get("/login?next=/admin", headers=TUNNEL)
    assert r.status_code == 200
    assert 'type="password"' in r.text
    assert 'value="/admin"' in r.text


def test_wrong_password_does_not_get_in(client):
    r = login(client, "nope")
    assert r.status_code == 401
    assert "Wrong password." in r.text
    assert auth.COOKIE not in r.cookies
    assert client.get("/admin", headers=TUNNEL, follow_redirects=False).status_code == 303


def test_right_password_sets_long_cookie_and_opens_admin(client):
    r = login(client, "letmein")
    assert r.status_code == 303
    assert r.headers["location"] == "/admin"
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert f"Max-Age={auth.COOKIE_MAX_AGE}" in cookie
    assert auth.COOKIE_MAX_AGE >= 180 * 24 * 3600
    page = client.get("/admin", headers=TUNNEL)
    assert page.status_code == 200
    assert 'href="/admin/photos"' in page.text
    page = client.get("/admin/photos", headers=TUNNEL)
    assert page.status_code == 200
    assert "Add Photos" in page.text
    assert client.get("/api/photos", headers=TUNNEL).status_code == 200


def test_upload_works_through_tunnel_after_login(client):
    login(client, "letmein")
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(buf, "JPEG")
    r = client.post(
        "/api/photos", headers=TUNNEL, files={"files": ("a.jpg", buf.getvalue(), "image/jpeg")}
    )
    assert r.status_code == 201
    photos = client.get("/api/photos", headers=TUNNEL).json()
    assert photos
    got = client.get(photos[0]["url"], headers=TUNNEL)
    assert got.status_code == 200
    assert got.headers["cache-control"].startswith("private")
    assert "public" not in got.headers["cache-control"]


def test_forged_cookie_and_old_password_cookie_refused(client, tmp_path):
    client.cookies.set(auth.COOKIE, "0" * 64)
    assert client.get("/admin", headers=TUNNEL, follow_redirects=False).status_code == 303
    login(client, "letmein")
    assert client.get("/admin", headers=TUNNEL).status_code == 200
    (tmp_path / "tunnel-password").write_text(auth.hash_password("newone"))
    assert client.get("/admin", headers=TUNNEL, follow_redirects=False).status_code == 303


def test_repeated_wrong_guesses_are_blocked(client):
    for _ in range(auth.FAILS_PER_CLIENT):
        assert login(client, "nope").status_code == 401
    r = login(client, "letmein")
    assert r.status_code == 429
    assert auth.COOKIE not in r.cookies
    assert login(client, "letmein", **{"CF-Connecting-IP": "198.51.100.7"}).status_code == 303


def test_no_password_file_refuses_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMEBOARD_PASSWORD_FILE", str(tmp_path / "missing"))
    with TestClient(create_app(tmp_path / "data")) as c:
        assert login(c, "").status_code == 503
        assert c.get("/api/photos", headers=TUNNEL).status_code == 401
        assert c.get("/admin").status_code == 200


def test_next_cannot_leave_the_site():
    assert auth._safe_next("//evil.example") == "/admin"
    assert auth._safe_next("https://evil.example") == "/admin"
    assert auth._safe_next("/admin?x=1") == "/admin?x=1"
    assert auth._safe_next("/login") == "/admin"
    assert auth._safe_next("/login?x=1") == "/admin"


def test_password_check_does_not_block_other_requests(client, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    real = auth.check_password

    def slow(password, stored):
        started.set()
        release.wait(10)
        return real(password, stored)

    monkeypatch.setattr(auth, "check_password", slow)
    result = {}
    worker = threading.Thread(target=lambda: result.update(r=login(client, "letmein")))
    worker.start()
    try:
        assert started.wait(10)
        t0 = time.monotonic()
        assert client.get("/healthz").status_code == 200
        assert time.monotonic() - t0 < 5
        assert not release.is_set()
    finally:
        release.set()
        worker.join(10)
    assert result["r"].status_code == 303


def test_tunnel_admin_does_not_count_as_display(client, monkeypatch):
    hosts = []
    real = events.subscribe

    def spy(host=None):
        hosts.append(host)
        return real(host)

    monkeypatch.setattr(events, "subscribe", spy)
    client.app.state.sse_limit = 0
    login(client, "letmein")
    assert client.get("/api/events", headers=TUNNEL).status_code == 200
    assert client.get("/api/events").status_code == 200
    assert hosts[0] is None
    assert hosts[1] is not None


def test_nginx_marks_only_the_tunnel_port():
    mapping = read(NGINX / "homeboard-map.conf")
    assert "map $server_port $homeboard_tunnel {" in mapping
    assert "8081 1;" in mapping
    assert 'default "";' in mapping
    proxy = read(NGINX / "homeboard-proxy.conf")
    assert "proxy_set_header X-Homeboard-Tunnel $homeboard_tunnel;" in proxy
    assert "client_max_body_size 0;" in proxy
    tunnel = read(NGINX / "homeboard-tunnel.conf")
    assert "listen 127.0.0.1:8081;" in tunnel
    assert "listen [::1]:8081;" in tunnel
    assert "default_server" not in tunnel
    assert "include snippets/homeboard-proxy.conf;" in tunnel
    for name in ("homeboard-http.conf", "homeboard-https.conf"):
        assert "8081" not in read(NGINX / name)


def test_tunnel_service_reads_root_only_token():
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read_string(read(DEPLOY / "homeboard-tunnel.service"))
    s = parser["Service"]
    assert s["EnvironmentFile"] == "/etc/homeboard/tunnel.env"
    assert s["ExecStart"] == "/usr/bin/cloudflared --no-autoupdate tunnel run"
    assert s["DynamicUser"] == "yes"
    assert s["Restart"] == "always"
    kiosk = read(DEPLOY / "homeboard-kiosk.service")
    assert "tunnel" not in kiosk and "cloudflared" not in kiosk


def test_install_sets_up_tunnel_and_password():
    text = read(DEPLOY / "install.sh")
    for flag in ("--tunnel)", "--new-tunnel-token)", "--new-password)"):
        assert flag in text
    assert 'TUNNEL_ENV="$ETC_DIR/tunnel.env"' in text
    assert 'PASSWORD_HASH="$ETC_DIR/tunnel-password.hash"' in text
    assert 'if [ -s "$TUNNEL_ENV" ]; then' in text
    assert 'chown root:root "$TUNNEL_ENV"' in text and 'chmod 600 "$TUNNEL_ENV"' in text
    assert 'chown root:root "$PASSWORD_HASH"' in text and 'chmod 600 "$PASSWORD_HASH"' in text
    assert ".venv/bin/python -m app.auth" in text
    assert "LoadCredential=tunnel-password:%s" in text
    assert "deploy/nginx/homeboard-map.conf /etc/nginx/conf.d/" in text
    assert "apt-get install -y cloudflared" in text
    assert "systemctl enable homeboard-tunnel.service" in text


def test_no_tunnel_secret_in_repo():
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert not [f for f in out if f.endswith((".env", ".hash", ".json")) and "tunnel" in f]
    assert not [f for f in out if "cert.pem" in f]


def test_setup_guide_explains_tunnel_and_password():
    text = read(DEPLOY / "PI_SETUP.md")
    assert "## Part 7: Use the board away from home" in text
    assert "https://one.dash.cloudflare.com/" in text
    assert "board.datawithdillon.com" in text
    assert "http://127.0.0.1:8081" in text
    assert "--tunnel" in text
    assert "--new-password" in text
    assert "Notepad" in text
