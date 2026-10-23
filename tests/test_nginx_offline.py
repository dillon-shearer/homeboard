import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NGINX = ROOT / "deploy" / "nginx"
BOOT = ROOT / "deploy" / "splash" / "boot.html"


def read(path):
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    return raw.decode("ascii")


def proxy_servers(text):
    return [s for s in re.split(r"(?m)^server \{", text)[1:] if "homeboard-proxy.conf" in s]


@pytest.mark.parametrize("name", ["homeboard-http.conf", "homeboard-https.conf"])
def test_site_serves_boot_page_when_api_is_down(name):
    servers = proxy_servers(read(NGINX / name))
    assert servers
    for server in servers:
        assert "error_page 502 503 504 /homeboard-offline.html;" in server
        location = server.split("location = /homeboard-offline.html {", 1)[1].split("}", 1)[0]
        assert "internal;" in location
        assert "alias /opt/homeboard/deploy/splash/boot.html;" in location
        assert "proxy_pass" not in location
        assert "Home Board is restarting..." in location


def test_restarting_label_swap_matches_boot_page():
    text = read(NGINX / "homeboard-http.conf")
    original = re.search(r"sub_filter '([^']+)'", text).group(1)
    assert original in read(BOOT)


def test_boot_page_polls_and_returns_on_same_origin():
    text = read(BOOT)
    assert 'window.location.protocol === "file:" ? "http://localhost" : ""' in text
    assert 'base ? "http://localhost/" : "/"' in text
    assert 'fetch(base + "/healthz"' in text


def test_tunnel_keeps_default_nginx_error():
    text = read(NGINX / "homeboard-tunnel.conf")
    assert "error_page" not in text
    assert "boot.html" not in text
    assert "error_page" not in read(NGINX / "homeboard-proxy.conf")
