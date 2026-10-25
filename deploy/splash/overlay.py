import mmap
import os
import select
import socket
import struct
import sys
import time

from PIL import Image, ImageChops, ImageSequence

HOLE = 4
ARGB8888 = 0
XBGR8888 = 0x34324258
ABGR8888 = 0x34324241
WAIT_FOR_MAP = 120
WAIT_FOR_PAGE = 30


def u(value):
    return struct.pack("<I", value)


def i(value):
    return struct.pack("<i", value)


def s(value):
    raw = value.encode() + b"\0"
    return u(len(raw)) + raw + b"\0" * (-len(raw) % 4)


def read_string(body, at):
    size = struct.unpack_from("<I", body, at)[0]
    text = body[at + 4 : at + 3 + size].decode()
    return text, at + 4 + size + (-size % 4)


def is_ground(pixel):
    r, _, b = pixel
    return b > 100 and b > r + 60


class Wayland:
    def __init__(self):
        path = os.path.join(
            os.environ["XDG_RUNTIME_DIR"], os.environ.get("WAYLAND_DISPLAY", "wayland-0")
        )
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)
        self.next_id = 2
        self.handlers = {1: self.on_display}
        self.data = b""
        self.globals = {}
        registry = self.new_id(self.on_global)
        self.send(1, 1, u(registry))
        self.registry = registry
        self.roundtrip()

    def on_display(self, op, body):
        if op == 0:
            code = struct.unpack_from("<I", body, 4)[0]
            sys.exit(f"wayland error {code}: {read_string(body, 8)[0]}")

    def on_global(self, op, body):
        if op == 0:
            name = struct.unpack_from("<I", body)[0]
            interface, at = read_string(body, 4)
            self.globals[interface] = (name, struct.unpack_from("<I", body, at)[0])

    def new_id(self, handler=None):
        oid = self.next_id
        self.next_id += 1
        if handler:
            self.handlers[oid] = handler
        return oid

    def send(self, oid, op, *parts, fd=None):
        body = b"".join(parts)
        message = struct.pack("<II", oid, (8 + len(body)) << 16 | op) + body
        if fd is None:
            self.sock.sendall(message)
        else:
            socket.send_fds(self.sock, [message], [fd])

    def bind(self, interface, version, handler=None):
        name, offered = self.globals[interface]
        oid = self.new_id(handler)
        self.send(self.registry, 0, u(name), s(interface), u(min(version, offered)), u(oid))
        return oid

    def roundtrip(self):
        done = []
        callback = self.new_id(lambda op, body: done.append(op))
        self.send(1, 0, u(callback))
        while not done:
            self.dispatch(None)

    def dispatch(self, timeout):
        ready, _, _ = select.select([self.sock], [], [], timeout)
        if ready:
            chunk = self.sock.recv(65536)
            if not chunk:
                sys.exit(0)
            self.data += chunk
        while len(self.data) >= 8:
            oid, word = struct.unpack_from("<II", self.data)
            size = word >> 16
            if len(self.data) < size:
                break
            body, self.data = self.data[8:size], self.data[size:]
            handler = self.handlers.get(oid)
            if handler:
                handler(word & 0xFFFF, body)


def load(path):
    gif = Image.open(path)
    delay = gif.info.get("duration", 100) / 1000
    first = None
    box = None
    for frame in ImageSequence.Iterator(gif):
        frame = frame.convert("RGBA")
        if first is None:
            first = frame
            continue
        diff = ImageChops.difference(first, frame).getbbox()
        if diff:
            box = (
                diff
                if box is None
                else (*map(min, box[:2], diff[:2]), *map(max, box[2:], diff[2:]))
            )
    box = box or (0, 0, first.width, first.height)
    strips = [
        frame.convert("RGBA").crop(box).tobytes("raw", "BGRA")
        for frame in ImageSequence.Iterator(gif)
    ]
    base = bytearray(first.tobytes("raw", "BGRA"))
    return first.size, base, box, strips, max(delay, 0.02)


class Pool:
    def __init__(self, wl, shm, size):
        self.fd = os.memfd_create("homeboard-overlay")
        os.ftruncate(self.fd, size)
        self.map = mmap.mmap(self.fd, size)
        self.wl = wl
        self.pool = wl.new_id()
        wl.send(shm, 0, u(self.pool), i(size), fd=self.fd)

    def buffer(self, offset, width, height, stride, fmt, handler=None):
        oid = self.wl.new_id(handler)
        self.wl.send(self.pool, 0, u(oid), i(offset), i(width), i(height), i(stride), u(fmt))
        return oid


class Overlay:
    def __init__(self, path):
        (self.width, self.height), base, self.box, self.strips, self.delay = load(path)
        self.wl = wl = Wayland()
        self.compositor = wl.bind("wl_compositor", 4)
        self.shm = wl.bind("wl_shm", 1)
        self.wm = wl.bind("xdg_wm_base", 1, self.on_wm)
        self.output = wl.bind("wl_output", 2, self.on_output)
        self.copier = wl.bind("zwlr_screencopy_manager_v1", 2)
        self.viewporter = wl.bind("wp_viewporter", 1) if "wp_viewporter" in wl.globals else None
        self.mode = None
        self.size = None
        self.stride = self.width * 4
        frame_bytes = self.stride * self.height
        self.pool = Pool(wl, self.shm, frame_bytes * 2 + 64)
        self.pool.map[:frame_bytes] = base
        self.pool.map[frame_bytes : frame_bytes * 2] = base
        for start in (0, frame_bytes):
            self.punch(start)
        self.busy = {}
        self.offsets = {}
        for n in range(2):
            oid = self.pool.buffer(n * frame_bytes, self.width, self.height, self.stride, ARGB8888)
            wl.handlers[oid] = self.released(oid)
            self.busy[oid] = False
            self.offsets[oid] = n * frame_bytes
        self.pixel = None
        self.shown = False
        self.index = 0
        self.surface = wl.new_id()
        wl.send(self.compositor, 0, u(self.surface))
        self.xdg = wl.new_id(self.on_xdg_surface)
        wl.send(self.wm, 2, u(self.xdg), u(self.surface))
        self.toplevel = wl.new_id(self.on_toplevel)
        wl.send(self.xdg, 1, u(self.toplevel))
        wl.send(self.toplevel, 3, s("homeboard-overlay"))
        wl.send(self.toplevel, 11, u(0))
        wl.send(self.surface, 6)
        wl.roundtrip()

    def on_wm(self, op, body):
        if op == 0:
            self.wl.send(self.wm, 3, body[:4])

    def on_output(self, op, body):
        if op == 1:
            flags, width, height = struct.unpack_from("<Iii", body)
            if flags & 1:
                self.mode = (width, height)

    def on_toplevel(self, op, body):
        if op == 0:
            width, height = struct.unpack_from("<ii", body)
            if width > 0 and height > 0:
                self.size = (width, height)

    def on_xdg_surface(self, op, body):
        if op == 0:
            self.wl.send(self.xdg, 4, body[:4])
            if self.shown:
                self.place()
                self.wl.send(self.surface, 6)

    def released(self, oid):
        def handler(op, body):
            if op == 0:
                self.busy[oid] = False

        return handler

    def place(self):
        target = self.size or self.mode
        if self.viewporter and target and target != (self.width, self.height):
            if not hasattr(self, "viewport"):
                self.viewport = self.wl.new_id()
                self.wl.send(self.viewporter, 1, u(self.viewport), u(self.surface))
            self.wl.send(self.viewport, 2, i(target[0]), i(target[1]))

    def show(self):
        self.place()
        self.shown = True
        self.commit(next(iter(self.busy)), (0, 0, self.width, self.height))

    def draw(self):
        free = [oid for oid, busy in self.busy.items() if not busy]
        if not free:
            return
        oid = free[0]
        x0, y0, x1, y1 = self.box
        row = (x1 - x0) * 4
        strip = self.strips[self.index % len(self.strips)]
        start = self.offsets[oid]
        for n in range(y1 - y0):
            at = start + (y0 + n) * self.stride + x0 * 4
            self.pool.map[at : at + row] = strip[n * row : (n + 1) * row]
        self.punch(start)
        self.commit(oid, (x0, y0, x1 - x0, y1 - y0))

    def punch(self, start):
        for y in range(HOLE):
            at = start + y * self.stride
            self.pool.map[at : at + HOLE * 4] = bytes(HOLE * 4)

    def commit(self, oid, box):
        self.wl.send(self.surface, 1, u(oid), i(0), i(0))
        self.wl.send(self.surface, 9, *[i(v) for v in box])
        self.wl.send(self.surface, 6)
        self.busy[oid] = True
        self.index += 1

    def sample(self):
        frame = self.wl.new_id()
        result = []

        def handler(op, body):
            if op == 0:
                fmt, width, height, stride = struct.unpack_from("<IIII", body)
                if self.pixel is None or self.pixel[1:3] != (fmt, stride):
                    pool = Pool(self.wl, self.shm, max(stride * height, 4))
                    self.pixel = (pool.buffer(0, width, height, stride, fmt), fmt, stride, pool)
                self.wl.send(frame, 2, u(self.pixel[0]))
            elif op == 2:
                raw = self.pixel[3].map[:4]
                if self.pixel[1] in (XBGR8888, ABGR8888):
                    result.append((raw[0], raw[1], raw[2]))
                else:
                    result.append((raw[2], raw[1], raw[0]))
            elif op == 3:
                result.append(None)

        self.wl.handlers[frame] = handler
        self.wl.send(self.copier, 1, u(frame), i(0), u(self.output), i(0), i(0), i(1), i(1))
        return frame, result

    def run(self):
        state = "seen"
        deadline = time.monotonic() + WAIT_FOR_MAP
        tick = None
        frame, result = self.sample()
        while time.monotonic() < deadline:
            wait = deadline - time.monotonic()
            if tick is not None:
                wait = min(wait, tick - time.monotonic())
            self.wl.dispatch(max(wait, 0))
            if tick is not None and time.monotonic() >= tick:
                self.draw()
                tick += self.delay
                if tick < time.monotonic():
                    tick = time.monotonic() + self.delay
            if not result:
                continue
            pixel = result[0]
            self.wl.send(frame, 1)
            del self.wl.handlers[frame]
            if pixel is not None:
                ground = is_ground(pixel)
                if state == "seen" and ground:
                    state = "wait"
                elif state == "wait" and not ground:
                    self.show()
                    state = "cover"
                    tick = time.monotonic() + self.delay
                    deadline = time.monotonic() + WAIT_FOR_PAGE
                elif state == "cover" and ground:
                    return
            frame, result = self.sample()


def main(argv):
    Overlay(argv[1]).run()


if __name__ == "__main__":
    main(sys.argv)
