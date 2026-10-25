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
WPCTL_TIMEOUT_S = 5
DEFAULT_VOLUME = 70
SINK_WAIT_S = 20
SINK_POLL_S = 1
TICK_S = 5
MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VOLUME_RE = re.compile(r"^Volume: (\d+(?:\.\d+)?)")


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


def run_out(cmd):
    try:
        out = subprocess.run(
            cmd, check=False, capture_output=True, text=True, timeout=WPCTL_TIMEOUT_S
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def speaker_sink(mac):
    if not mac:
        return None
    text = run_out(["pw-dump"])
    try:
        objects = json.loads(text) if text else []
    except ValueError:
        return None
    for o in objects if isinstance(objects, list) else []:
        props = ((o.get("info") or {}).get("props") or {}) if isinstance(o, dict) else {}
        if (
            props.get("media.class") == "Audio/Sink"
            and str(props.get("api.bluez5.address", "")).upper() == mac
        ):
            return o.get("id")
    return None


def get_volume(node):
    m = VOLUME_RE.match((run_out(["wpctl", "get-volume", str(node)]) or "").strip())
    return round(float(m.group(1)) * 100) if m else None


def set_volume(node, percent):
    run_out(["wpctl", "set-volume", str(node), f"{percent / 100:.2f}"])
    return get_volume(node)


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


def read_setting(key):
    try:
        with urllib.request.urlopen(SETTINGS_URL, timeout=5) as resp:
            return json.loads(resp.read())[key]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def idle_setting():
    v = read_setting("speaker_idle_minutes")
    return v if isinstance(v, int) and v > 0 else DEFAULT_IDLE_MINUTES


def volume_setting():
    v = read_setting("speaker_volume")
    ok = isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100
    return v if ok else DEFAULT_VOLUME


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

    def volume(self):
        node = speaker_sink(self.mac)
        return {"volume": None if node is None else get_volume(node)}

    def set_volume(self, percent=None):
        wait = percent is None
        if wait:
            percent = volume_setting()
        node = speaker_sink(self.mac)
        deadline = time.monotonic() + SINK_WAIT_S
        while node is None and wait and time.monotonic() < deadline:
            time.sleep(SINK_POLL_S)
            node = speaker_sink(self.mac)
        if node is None:
            return None
        return {"volume": set_volume(node, percent)}

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
            if self.path == "/volume":
                return self.reply(200, helper.volume())
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
            if self.path not in ("/connect", "/disconnect", "/volume"):
                return self.reply(404, {"detail": "not found"})
            if not helper.mac:
                return self.reply(409, {"detail": "no speaker paired"})
            if self.path == "/volume":
                v = body.get("volume")
                bad = isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 100
                if v is not None and bad:
                    return self.reply(400, {"detail": "volume must be an integer from 0 to 100"})
                done = helper.set_volume(v)
                if done is None:
                    return self.reply(409, {"detail": "speaker not connected"})
                return self.reply(200, done)
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
