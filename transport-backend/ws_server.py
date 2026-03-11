"""WebSocket/STOMP live updates adapter.

Provides a lightweight STOMP-over-WebSocket broker that bridges
live transport feeds into real-time push updates for frontend clients.

Supported topics
~~~~~~~~~~~~~~~~
  /topic/BUS_MVT_ALL        – Live bus movement positions
  /topic/TRAIN_MVT_ALL_TOC  – Train movement updates  (placeholder)
  /topic/TD_ALL_SIG_AREA    – Train signal-area updates (placeholder)
  /topic/SERVICE_ALERTS     – Service alert updates     (placeholder)

The broker periodically polls ``BusLive`` data and broadcasts JSON
messages to every client subscribed to ``/topic/BUS_MVT_ALL``.

Integration
~~~~~~~~~~~
::

    from ws_server import broker as ws_broker, websocket_endpoint

    app.add_api_websocket_route("/ws/live", websocket_endpoint)
    ws_broker.configure(bus_live_factory=lambda: BusLive(timeout=20), poll_interval=20.0)
    await ws_broker.start_polling()
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


# ── STOMP frame representation ────────────────────────────────────────


@dataclass
class StompFrame:
    """A single STOMP 1.2 protocol frame.

    Only the subset required by ``@stomp/stompjs`` is implemented:

    * **Client → server:** CONNECT / STOMP, SUBSCRIBE, UNSUBSCRIBE,
      DISCONNECT
    * **Server → client:** CONNECTED, MESSAGE, RECEIPT, ERROR
    """

    command: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: str = ""

    # -- parsing -----------------------------------------------------------

    @classmethod
    def parse(cls, raw: str) -> Optional[StompFrame]:
        """Parse a raw STOMP frame string.

        Returns ``None`` for empty / heartbeat-only input.
        """
        raw = raw.replace("\x00", "").strip("\r\n ")
        if not raw:
            return None

        lines = raw.split("\n")
        command = lines[0].strip()
        if not command:
            return None

        headers: Dict[str, str] = {}
        body_start = len(lines)
        for i in range(1, len(lines)):
            line = lines[i].rstrip("\r")
            if line == "":
                body_start = i + 1
                break
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip()] = value.strip()

        body = "\n".join(lines[body_start:]) if body_start < len(lines) else ""
        return cls(command=command, headers=headers, body=body)

    # -- serialisation -----------------------------------------------------

    def serialize(self) -> str:
        """Serialise to STOMP wire format (text + NULL terminator)."""
        parts: list[str] = [self.command, "\n"]
        for key, value in self.headers.items():
            parts.append(f"{key}:{value}\n")
        parts.append("\n")
        if self.body:
            parts.append(self.body)
        parts.append("\x00")
        return "".join(parts)


# ── Per-client connection ─────────────────────────────────────────────


class StompConnection:
    """State for a single STOMP client WebSocket connection."""

    def __init__(self, websocket: WebSocket, connection_id: str) -> None:
        self.websocket = websocket
        self.connection_id = connection_id
        self.subscriptions: Dict[str, str] = {}  # sub_id → destination
        self.connected: bool = False

    async def send_frame(self, frame: StompFrame) -> None:
        """Send a STOMP frame; silently swallow transport errors."""
        try:
            await self.websocket.send_text(frame.serialize())
        except Exception as exc:
            logger.warning(
                "send_frame to %s failed: %s", self.connection_id, exc
            )

    async def handle_connect(self, frame: StompFrame) -> None:
        """Respond to a CONNECT / STOMP frame with CONNECTED."""
        self.connected = True
        await self.send_frame(
            StompFrame(
                command="CONNECTED",
                headers={
                    "version": "1.2",
                    "server": "transport-backend/1.0",
                    "heart-beat": "0,0",
                },
            )
        )

    def subscribe(self, sub_id: str, destination: str) -> None:
        self.subscriptions[sub_id] = destination

    def unsubscribe(self, sub_id: str) -> None:
        self.subscriptions.pop(sub_id, None)

    def get_subscriptions_for_topic(self, topic: str) -> List[str]:
        """Return subscription IDs that match *topic*."""
        return [sid for sid, dest in self.subscriptions.items() if dest == topic]


# ── Broker (manages all connections + broadcast) ──────────────────────


class StompBroker:
    """Lightweight in-process STOMP message broker.

    Manages client connections, topic subscriptions, and a periodic
    background poll that fetches live bus data and pushes it to
    subscribers.
    """

    SUPPORTED_TOPICS: frozenset[str] = frozenset(
        {
            "/topic/BUS_MVT_ALL",
            "/topic/TRAIN_MVT_ALL_TOC",
            "/topic/TD_ALL_SIG_AREA",
            "/topic/SERVICE_ALERTS",
        }
    )

    def __init__(self) -> None:
        self.connections: Dict[str, StompConnection] = {}
        self._message_counter: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()
        self._poll_task: Optional[asyncio.Task] = None
        self._bus_live_factory: Optional[Callable] = None
        self._poll_interval: float = 20.0
        self._poll_lat: float = 54.046
        self._poll_lon: float = -2.798

    # -- configuration -----------------------------------------------------

    def configure(
        self,
        bus_live_factory: Optional[Callable] = None,
    poll_interval: float = 20.0,
        poll_lat: float = 54.046,
        poll_lon: float = -2.798,
    ) -> None:
        """Set the data-source configuration for the broker.

        Args:
            bus_live_factory: zero-arg callable returning a ``BusLive``
            poll_interval: seconds between live-data polls
            poll_lat / poll_lon: default centre for bus polling
        """
        self._bus_live_factory = bus_live_factory
        self._poll_interval = poll_interval
        self._poll_lat = poll_lat
        self._poll_lon = poll_lon

    # -- connection management ---------------------------------------------

    async def add_connection(self, connection: StompConnection) -> None:
        async with self._lock:
            self.connections[connection.connection_id] = connection

    async def remove_connection(self, connection_id: str) -> None:
        async with self._lock:
            self.connections.pop(connection_id, None)

    # -- helpers -----------------------------------------------------------

    def _next_message_id(self) -> str:
        self._message_counter += 1
        return f"msg-{self._message_counter}"

    def _has_subscribers(self, topic: str) -> bool:
        """Return *True* if any connected client subscribes to *topic*."""
        for conn in self.connections.values():
            if conn.connected and conn.get_subscriptions_for_topic(topic):
                return True
        return False

    # -- broadcast ---------------------------------------------------------

    async def broadcast(self, topic: str, body: Any) -> int:
        """Send a MESSAGE frame to every subscriber of *topic*.

        Returns the number of individual messages sent.
        """
        body_str = json.dumps(body) if not isinstance(body, str) else body

        async with self._lock:
            connections = list(self.connections.values())

        sent = 0
        for conn in connections:
            if not conn.connected:
                continue
            for sub_id in conn.get_subscriptions_for_topic(topic):
                frame = StompFrame(
                    command="MESSAGE",
                    headers={
                        "subscription": sub_id,
                        "message-id": self._next_message_id(),
                        "destination": topic,
                        "content-type": "application/json",
                    },
                    body=body_str,
                )
                await conn.send_frame(frame)
                sent += 1
        return sent

    # -- periodic polling --------------------------------------------------

    async def _poll_bus_live(self) -> None:
        """Fetch live bus positions and broadcast to subscribers."""
        if self._bus_live_factory is None:
            return
        if not self._has_subscribers("/topic/BUS_MVT_ALL"):
            return

        try:
            bus_live_instance = self._bus_live_factory()
            results = await asyncio.to_thread(
                bus_live_instance.get_bus_live,
                self._poll_lat,
                self._poll_lon,
                lat_tol=0.1,
                lon_tol=0.1,
            )
            vehicles = [
                {
                    "line": line_ref,
                    "destination": dest,
                    "lat": lat,
                    "lon": lon,
                    "operator": operator,
                    "timestamp": time.time(),
                }
                for line_ref, dest, lat, lon, operator, *_rest in results
            ]
            await self.broadcast(
                "/topic/BUS_MVT_ALL",
                {
                    "type": "bus_positions",
                    "count": len(vehicles),
                    "vehicles": vehicles,
                    "timestamp": time.time(),
                },
            )
        except Exception as exc:
            logger.warning("Bus live poll failed: %s", exc)

    async def start_polling(self) -> None:
        """Start the background poll loop (idempotent)."""
        if self._poll_task is not None:
            return

        async def _loop() -> None:
            while True:
                await self._poll_bus_live()
                await asyncio.sleep(self._poll_interval)

        self._poll_task = asyncio.create_task(_loop())

    async def stop_polling(self) -> None:
        """Cancel the background poll loop."""
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    # -- frame dispatch ----------------------------------------------------

    async def handle_frame(
        self, connection: StompConnection, frame: StompFrame
    ) -> None:
        """Route an incoming client frame to its handler."""
        cmd = frame.command.upper()

        if cmd in ("CONNECT", "STOMP"):
            await connection.handle_connect(frame)

        elif cmd == "SUBSCRIBE":
            sub_id = frame.headers.get("id", "")
            destination = frame.headers.get("destination", "")
            if not sub_id or not destination:
                await connection.send_frame(
                    StompFrame(
                        command="ERROR",
                        headers={"message": "Missing required header"},
                        body="SUBSCRIBE requires 'id' and 'destination' headers",
                    )
                )
                return
            connection.subscribe(sub_id, destination)

        elif cmd == "UNSUBSCRIBE":
            sub_id = frame.headers.get("id", "")
            if sub_id:
                connection.unsubscribe(sub_id)

        elif cmd == "DISCONNECT":
            receipt = frame.headers.get("receipt")
            if receipt:
                await connection.send_frame(
                    StompFrame(
                        command="RECEIPT",
                        headers={"receipt-id": receipt},
                    )
                )
            connection.connected = False

        else:
            logger.debug("Ignoring unknown STOMP command: %s", cmd)


# ── Module-level singleton ────────────────────────────────────────────

broker = StompBroker()


# ── FastAPI WebSocket endpoint ────────────────────────────────────────


async def websocket_endpoint(websocket: WebSocket) -> None:
    """Accept a WebSocket and speak STOMP 1.2 over it.

    Mount with::

        app.add_api_websocket_route("/ws/live", websocket_endpoint)
    """
    await websocket.accept()

    connection_id = f"conn-{id(websocket)}-{int(time.time() * 1000)}"
    connection = StompConnection(websocket, connection_id)
    await broker.add_connection(connection)

    try:
        while True:
            raw = await websocket.receive_text()

            # Heartbeat (empty / whitespace-only)
            if not raw or raw.strip() == "":
                continue

            # A single WS message may contain multiple NULL-separated frames
            for segment in raw.split("\x00"):
                frame = StompFrame.parse(segment)
                if frame is not None:
                    await broker.handle_frame(connection, frame)

    except WebSocketDisconnect:
        logger.info("Client %s disconnected", connection_id)
    except Exception as exc:
        logger.warning("WebSocket error for %s: %s", connection_id, exc)
    finally:
        await broker.remove_connection(connection_id)
