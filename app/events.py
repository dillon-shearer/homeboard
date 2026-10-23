last_event: str | None = None


def publish(name: str) -> None:
    global last_event
    last_event = name
