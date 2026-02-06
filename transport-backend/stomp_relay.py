import os
import threading
from typing import Callable, List

import stomp


class StompRelay:
    def __init__(self, on_message: Callable[[str, str], None]):
        self.on_message = on_message
        self.conn = None
        self.thread = None

    def start(self) -> None:
        host = os.getenv("STOMP_HOST", "transport.scc.lancs.ac.uk")
        port = int(os.getenv("STOMP_PORT", "61613"))
        user = os.getenv("STOMP_USER", "")
        password = os.getenv("STOMP_PASSWORD", "")

        self.conn = stomp.Connection([(host, port)])
        self.conn.set_listener("relay", _RelayListener(self.on_message))
        self.conn.connect(user, password, wait=True)

        topics = _topics_from_env()
        for topic in topics:
            self.conn.subscribe(destination=topic, id=topic, ack="auto")

    def stop(self) -> None:
        if self.conn:
            self.conn.disconnect()
            self.conn = None


def _topics_from_env() -> List[str]:
    value = os.getenv("STOMP_TOPICS", "/topic/TRAIN_MVT_ALL_TOC,/topic/TD_ALL_SIG_AREA")
    return [item.strip() for item in value.split(",") if item.strip()]


class _RelayListener(stomp.ConnectionListener):
    def __init__(self, on_message: Callable[[str, str], None]):
        self.on_message = on_message

    def on_message(self, frame):
        self.on_message(frame.headers.get("destination", ""), frame.body)
