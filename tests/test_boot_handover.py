import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
NGINX = DEPLOY / "nginx"


def read(path):
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    return raw.decode("ascii")


def localhost_server(name):
    servers = re.split(r"(?m)^server \{", read(NGINX / name))[1:]
    found = [s for s in servers if "listen 127.0.0.1:80;" in s or "listen 80 default_server;" in s]
    return [s for s in found if "homeboard-proxy.conf" in s][0]


def test_nginx_serves_boot_page_on_localhost():
    for name in ["homeboard-http.conf", "homeboard-https.conf"]:
        server = localhost_server(name)
        location = server.split("location = /boot.html {", 1)[1].split("}", 1)[0]
        assert "alias /opt/homeboard/deploy/splash/boot.html;" in location
        assert 'add_header Cache-Control "no-store" always;' in location
        assert "internal;" not in location


def test_kiosk_opens_the_slideshow_when_nginx_listens():
    text = read(DEPLOY / "kiosk.sh")
    assert "BOOT=file:///opt/homeboard/deploy/splash/boot.html\n" in text
    assert "    if (exec 3<>/dev/tcp/127.0.0.1/80) 2>/dev/null; then\n" in text
    assert "        BOOT=http://localhost/\n" in text
    assert "/boot.html" not in text.replace("/deploy/splash/boot.html", "")
    assert text.rstrip().endswith('"$BOOT"')
    assert text.index("BOOT=http://localhost/") < text.index("exec chromium")


def test_display_page_ground_matches_splash(tmp_path):
    boot = read(DEPLOY / "splash" / "boot.html")
    rule = boot.split(".splash, .splash-ground {", 1)[1].split("}", 1)[0]
    assert "linear-gradient(180deg, #1b3f8f 0%, #3a74c8 45%, #8fbbe8 100%)" in rule
    with TestClient(create_app(tmp_path)) as client:
        text = client.get("/").text
    assert '<html lang="en" class="splash-ground">' in text
    assert ".splash, .splash-ground {" in text
