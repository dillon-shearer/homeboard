import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

LOCAL_HOSTS = ("127.0.0.1", "::1")
QUEUE_MAX = 32

last_event: str | None = None


class EventQueue(asyncio.Queue):
    def __init__(self):
        super().__init__(QUEUE_MAX)

    def put_nowait(self, item):
        if item in self._queue:
            return
        if self.full():
            self.get_nowait()
        super().put_nowait(item)


@dataclass(eq=False)
class Subscriber:
    loop: asyncio.AbstractEventLoop
    local: bool
    queue: asyncio.Queue = field(default_factory=EventQueue)


subscribers: set[Subscriber] = set()


def publish(name: str) -> None:
    global last_event
    last_event = name
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    for sub in list(subscribers):
        if sub.loop is running:
            sub.queue.put_nowait(name)
        elif not sub.loop.is_closed():
            sub.loop.call_soon_threadsafe(sub.queue.put_nowait, name)


@asynccontextmanager
async def subscribe(client_host: str | None = None):
    sub = Subscriber(loop=asyncio.get_running_loop(), local=client_host in LOCAL_HOSTS)
    subscribers.add(sub)
    try:
        yield sub.queue
    finally:
        subscribers.discard(sub)


def display_connected() -> bool:
    return any(sub.local for sub in subscribers)
