import asyncio
from typing import Optional, Set

from fastapi import WebSocket


class WebSocketManager:
    def __init__(self) -> None:
        self.clients: Set[WebSocket] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.clients.add(websocket)
        if self.loop is None:
            self.loop = asyncio.get_running_loop()

    def disconnect(self, websocket: WebSocket) -> None:
        self.clients.discard(websocket)

    async def broadcast(self, message: str) -> None:
        if not self.clients:
            return
        await asyncio.gather(*[client.send_text(message) for client in self.clients])

    def broadcast_threadsafe(self, message: str) -> None:
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(message), self.loop)
