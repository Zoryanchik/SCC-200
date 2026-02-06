import threading

from ws_manager import WebSocketManager
from stomp_relay import StompRelay


class WebSocketRelay:
    def __init__(self, manager: WebSocketManager) -> None:
        self.manager = manager
        self.relay = StompRelay(self._handle_message)
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.relay.start, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.relay.stop()

    def _handle_message(self, destination: str, body: str) -> None:
        message = f"{destination}|{body}"
        self.manager.broadcast_threadsafe(message)
