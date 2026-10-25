import re
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import events
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
NGINX = DEPLOY / "nginx"


def read(path):
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    return raw.decode("ascii")


def server_blocks(text):
    return [b for b in re.split(r"(?m)^server \{", text) if b.strip()]


def test_https_site_proxies_loopback_and_redirects_lan():
    text = read(NGINX / "homeboard-https.conf")
    local, redirect, tls = server_blocks(text)
    assert "listen 127.0.0.1:80;" in local
    assert "listen [::1]:80;" in local
    assert "include snippets/homeboard-proxy.conf;" in local
    assert "return" not in local
    assert "listen 80 default_server;" in redirect
    assert "return 301 https://__HOST__$request_uri;" in redirect
    assert "proxy_pass" not in redirect
    assert "listen 443 ssl default_server;" in tls
    assert "ssl_certificate /etc/letsencrypt/live/__HOST__/fullchain.pem;" in tls
    assert "ssl_certificate_key /etc/letsencrypt/live/__HOST__/privkey.pem;" in tls
    assert "include snippets/homeboard-proxy.conf;" in tls


def test_http_site_proxies_everything_when_no_host_is_set():
    text = read(NGINX / "homeboard-http.conf")
    assert "listen 80 default_server;" in text
    assert "include snippets/homeboard-proxy.conf;" in text
    assert "443" not in text and "return" not in text


def test_proxy_keeps_client_address_sse_and_large_uploads():
    text = read(NGINX / "homeboard-proxy.conf")
    assert "proxy_pass http://127.0.0.1:8000;" in text
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in text
    assert "proxy_buffering off;" in text
    assert "proxy_request_buffering off;" in text
    assert "client_max_body_size 51m;" in text


def test_kiosk_still_uses_plain_localhost():
    assert "https://" not in read(DEPLOY / "kiosk.sh")
    boot = read(DEPLOY / "splash" / "boot.html")
    assert '"http://localhost"' in boot
    assert '"http://localhost/"' in boot
    assert "https://" not in boot


def test_renewal_hook_reloads_nginx():
    text = read(DEPLOY / "certbot-reload-nginx.sh")
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "systemctl reload nginx" in text


def test_install_handles_certificate_token_and_renewal():
    text = read(DEPLOY / "install.sh")
    assert "--host)" in text and "--dns)" in text
    assert 'DNS_CREDENTIALS="$ETC_DIR/dns-credentials.ini"' in text
    assert "read -rsp" in text
    assert 'chown root:root "$DNS_CREDENTIALS"' in text
    assert 'chmod 600 "$DNS_CREDENTIALS"' in text
    assert "install -d -m 700 -o root -g root" in text
    assert "--keep-until-expiring" in text
    assert "systemctl enable --now certbot.timer" in text
    assert "renewal-hooks/deploy" in text
    assert 'if [ -z "$HOST" ] && [ -f "$HTTPS_ENV" ]; then' in text
    assert "nginx -t" in text


def test_no_token_or_credentials_file_in_repo():
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert not [f for f in out if f.endswith((".ini", ".pem", ".key")) or "credentials" in f]


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_install_script_parses():
    subprocess.run(["bash", "-n", str(DEPLOY / "install.sh")], check=True)


def test_empty_screen_shows_https_address_when_host_is_set(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMEBOARD_HOST", "board.example.com")
    with TestClient(create_app(tmp_path)) as client:
        r = client.get("/")
    assert '<div class="url">https://board.example.com/admin</div>' in r.text
    assert 'class="url ip"' not in r.text


@pytest.mark.parametrize("host,local", [("127.0.0.1", True), ("192.168.1.20", False)])
def test_loopback_client_still_counts_as_display(tmp_path, monkeypatch, host, local):
    marked = []
    real = events.subscribe

    @asynccontextmanager
    async def spy(client_host=None):
        async with real(client_host) as queue:
            marked.extend(sub.local for sub in events.subscribers if sub.queue is queue)
            yield queue

    monkeypatch.setattr(events, "subscribe", spy)
    app = create_app(tmp_path)
    app.state.sse_limit = 0
    with TestClient(app, client=(host, 50000)) as client:
        assert client.get("/api/events", headers={"Accept": "text/event-stream"}).status_code == 200
    assert marked == [local]


def test_setup_guide_explains_domain_dns_and_token():
    text = read(DEPLOY / "PI_SETUP.md")
    assert "## Part 6: Secure address for phones (https)" in text
    assert "--host homeboard.datawithdillon.com --dns cloudflare" in text
    assert "DNS only" in text
    assert "/etc/homeboard/dns-credentials.ini" in text
    assert "sudo certbot renew --dry-run" in text
