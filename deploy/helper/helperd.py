import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = 8002
SETTINGS_URL = "http://127.0.0.1:8000/api/settings"
SOLOIST_EXPIRES = "/opt/soloist/expires"
DEFAULT_IDLE_MINUTES = 10
CONNECT_TRIES = 5
CONNECT_GAP_S = 3
BLUETOOTHCTL_TIMEOUT_S = 20
TICK_S = 5
MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class IdleState:
    def __init__(self):
        self.playing = False
        self.deadline = None
        self.stopped_at = None

    def set_playing(self, playing, now, idle_minutes=DEFAULT_IDLE_MINUTES):
        self.playing = playing
        if playing:
            self.deadline = None
            self.stopped_at = None
            return "connect"
        self.stopped_at = now
        self.deadline = now + idle_minutes * 60
        return None

    def tick(self, now):
        if self.deadline is not None and now >= self.deadline:
            self.deadline = None
            return "disconnect"
        return None

    def idle_minutes(self, now):
        if self.playing or self.stopped_at is None:
            return 0
        return int((now - self.stopped_at) // 60)


def bluetoothctl(*args):
    try:
        out = subprocess.run(
            ["bluetoothctl", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=BLUETOOTHCTL_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout


def speaker_info(mac):
    if not mac:
        return {"mac": None, "connected": False, "name": None}
    text = bluetoothctl("info", mac)
    name = None
    for line in text.splitlines():
        key, _, value = line.strip().partition(": ")
        if key in ("Name", "Alias") and name is None:
            name = value
    return {"mac": mac, "connected": "Connected: yes" in text, "name": name}


def soloist_expires():
    try:
        with open(SOLOIST_EXPIRES, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return None
    return text if DATE_RE.match(text) else None


def idle_setting():
    try:
        with urllib.request.urlopen(SETTINGS_URL, timeout=5) as resp:
            v = json.loads(resp.read())["speaker_idle_minutes"]
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_IDLE_MINUTES
    return v if isinstance(v, int) and v > 0 else DEFAULT_IDLE_MINUTES


class Helper:
    def __init__(self, mac):
        self.mac = mac
        self.state = IdleState()
        self.lock = threading.Lock()
        self.bt_lock = threading.Lock()

    def connected(self):
        return speaker_info(self.mac)["connected"]

    def connect(self):
        with self.bt_lock:
            for attempt in range(CONNECT_TRIES):
                if self.connected():
                    return True
                bluetoothctl("connect", self.mac)
                if self.connected():
                    return True
                if attempt < CONNECT_TRIES - 1:
                    time.sleep(CONNECT_GAP_S)
            return False

    def disconnect(self):
        with self.bt_lock:
            bluetoothctl("disconnect", self.mac)
            return self.connected()

    def status(self):
        with self.lock:
            playing = self.state.playing
            idle = self.state.idle_minutes(time.monotonic())
        return {
            "speaker": speaker_info(self.mac),
            "playing": playing,
            "idle_minutes": idle,
            "soloist_expires": soloist_expires(),
        }

    def set_playing(self, playing):
        minutes = DEFAULT_IDLE_MINUTES if playing else idle_setting()
        with self.lock:
            action = self.state.set_playing(playing, time.monotonic(), minutes)
        if action == "connect" and self.mac:
            self.connect()

    def tick(self):
        with self.lock:
            action = self.state.tick(time.monotonic())
        if action == "disconnect" and self.mac:
            self.disconnect()

    def run_timer(self):
        while True:
            time.sleep(TICK_S)
            self.tick()


def make_handler(helper):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def read_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                body = json.loads(self.rfile.read(length))
            except ValueError:
                return None
            return body if isinstance(body, dict) else None

        def do_GET(self):
            if self.path == "/status":
                return self.reply(200, helper.status())
            self.reply(404, {"detail": "not found"})

        def do_POST(self):
            body = self.read_body()
            if body is None:
                return self.reply(400, {"detail": "body must be a JSON object"})
            if self.path == "/reboot":
                done = subprocess.run(["sudo", "-n", "systemctl", "reboot"], check=False)
                ok = done.returncode == 0
                return self.reply(200 if ok else 500, {"rebooting": ok})
            if self.path == "/playing":
                playing = body.get("playing")
                if not isinstance(playing, bool):
                    return self.reply(400, {"detail": "playing must be true or false"})
                helper.set_playing(playing)
                return self.reply(200, helper.status())
            if self.path not in ("/connect", "/disconnect"):
                return self.reply(404, {"detail": "not found"})
            if not helper.mac:
                return self.reply(409, {"detail": "no speaker paired"})
            if self.path == "/connect":
                return self.reply(200, {"connected": helper.connect()})
            self.reply(200, {"connected": helper.disconnect()})

        def log_message(self, format, *args):
            pass

    return Handler


def main():
    mac = os.environ.get("SPEAKER_MAC", "").strip().upper()
    helper = Helper(mac if MAC_RE.match(mac) else "")
    helper.set_playing(False)
    threading.Thread(target=helper.run_timer, daemon=True).start()
    ThreadingHTTPServer((HOST, PORT), make_handler(helper)).serve_forever()


if __name__ == "__main__":
    main()
