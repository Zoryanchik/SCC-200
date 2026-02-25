"""Tests for ws_server.py — WebSocket/STOMP live updates adapter.

Covers StompFrame parsing/serialisation, StompConnection state,
StompBroker operations (broadcast, polling), and the FastAPI
WebSocket endpoint integration.

All async helpers are run via ``asyncio.run()`` so no extra
pytest-asyncio dependency is required.
"""

import asyncio
import functools
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

# Ensure transport-backend is importable
_backend = os.path.join(os.path.dirname(__file__), "..")
if os.path.abspath(_backend) not in sys.path:
    sys.path.insert(0, os.path.abspath(_backend))

from ws_server import (  # noqa: E402
    StompBroker,
    StompConnection,
    StompFrame,
    broker,
    websocket_endpoint,
)


# ── helper: run async test functions without pytest-asyncio ───────────


def async_test(fn):
    """Decorator — run an ``async def`` test with ``asyncio.run()``."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


# ═════════════════════════════════════════════════════════════════════
#  StompFrame
# ═════════════════════════════════════════════════════════════════════


class TestStompFrame:
    """Unit tests for STOMP frame parsing and serialisation."""

    # -- parse -------------------------------------------------------------

    def test_parse_connect_frame(self):
        raw = (
            "CONNECT\n"
            "accept-version:1.2\n"
            "heart-beat:4000,4000\n"
            "login:guest\n"
            "passcode:guest\n"
            "\n"
            "\x00"
        )
        frame = StompFrame.parse(raw)
        assert frame is not None
        assert frame.command == "CONNECT"
        assert frame.headers["accept-version"] == "1.2"
        assert frame.headers["heart-beat"] == "4000,4000"
        assert frame.headers["login"] == "guest"
        assert frame.headers["passcode"] == "guest"
        assert frame.body == ""

    def test_parse_subscribe_frame(self):
        raw = "SUBSCRIBE\nid:sub-0\ndestination:/topic/BUS_MVT_ALL\n\n\x00"
        frame = StompFrame.parse(raw)
        assert frame is not None
        assert frame.command == "SUBSCRIBE"
        assert frame.headers["id"] == "sub-0"
        assert frame.headers["destination"] == "/topic/BUS_MVT_ALL"

    def test_parse_disconnect_with_receipt(self):
        raw = "DISCONNECT\nreceipt:77\n\n\x00"
        frame = StompFrame.parse(raw)
        assert frame is not None
        assert frame.command == "DISCONNECT"
        assert frame.headers["receipt"] == "77"

    def test_parse_frame_with_body(self):
        raw = (
            "SEND\n"
            "destination:/topic/test\n"
            "content-type:application/json\n"
            "\n"
            '{"hello":"world"}\x00'
        )
        frame = StompFrame.parse(raw)
        assert frame is not None
        assert frame.command == "SEND"
        assert frame.headers["destination"] == "/topic/test"
        assert frame.body == '{"hello":"world"}'

    def test_parse_empty_returns_none(self):
        assert StompFrame.parse("") is None
        assert StompFrame.parse("\x00") is None
        assert StompFrame.parse("\n\n") is None
        assert StompFrame.parse("   ") is None

    def test_parse_command_only(self):
        frame = StompFrame.parse("HEARTBEAT\n\n\x00")
        assert frame is not None
        assert frame.command == "HEARTBEAT"
        assert frame.headers == {}
        assert frame.body == ""

    # -- serialize ---------------------------------------------------------

    def test_serialize_connected(self):
        frame = StompFrame(
            command="CONNECTED",
            headers={"version": "1.2", "server": "test/1.0", "heart-beat": "0,0"},
        )
        text = frame.serialize()
        assert text.startswith("CONNECTED\n")
        assert "version:1.2\n" in text
        assert "heart-beat:0,0\n" in text
        assert text.endswith("\x00")

    def test_serialize_message_with_body(self):
        frame = StompFrame(
            command="MESSAGE",
            headers={
                "subscription": "sub-0",
                "message-id": "msg-1",
                "destination": "/topic/BUS_MVT_ALL",
                "content-type": "application/json",
            },
            body='{"count":3}',
        )
        text = frame.serialize()
        assert "MESSAGE\n" in text
        assert "subscription:sub-0\n" in text
        assert '{"count":3}' in text
        assert text.endswith("\x00")

    def test_serialize_no_body(self):
        frame = StompFrame(command="RECEIPT", headers={"receipt-id": "r1"})
        text = frame.serialize()
        # Should end with empty-line + NULL (no body between them)
        assert text.endswith("\n\x00")

    # -- round-trip --------------------------------------------------------

    def test_roundtrip(self):
        original = StompFrame(
            command="MESSAGE",
            headers={"destination": "/topic/test", "id": "42"},
            body='{"data":true}',
        )
        parsed = StompFrame.parse(original.serialize())
        assert parsed is not None
        assert parsed.command == original.command
        assert parsed.headers == original.headers
        assert parsed.body == original.body


# ═════════════════════════════════════════════════════════════════════
#  StompConnection
# ═════════════════════════════════════════════════════════════════════


class TestStompConnection:
    """Unit tests for per-client connection state."""

    def test_initial_state(self):
        ws = MagicMock(spec=WebSocket)
        conn = StompConnection(ws, "conn-1")
        assert conn.connected is False
        assert conn.subscriptions == {}

    def test_subscribe_and_unsubscribe(self):
        ws = MagicMock(spec=WebSocket)
        conn = StompConnection(ws, "conn-1")

        conn.subscribe("sub-1", "/topic/BUS_MVT_ALL")
        conn.subscribe("sub-2", "/topic/SERVICE_ALERTS")
        assert conn.subscriptions == {
            "sub-1": "/topic/BUS_MVT_ALL",
            "sub-2": "/topic/SERVICE_ALERTS",
        }

        conn.unsubscribe("sub-1")
        assert "sub-1" not in conn.subscriptions
        assert conn.subscriptions == {"sub-2": "/topic/SERVICE_ALERTS"}

    def test_unsubscribe_nonexistent_is_noop(self):
        ws = MagicMock(spec=WebSocket)
        conn = StompConnection(ws, "conn-1")
        conn.unsubscribe("does-not-exist")  # should not raise

    def test_get_subscriptions_for_topic(self):
        ws = MagicMock(spec=WebSocket)
        conn = StompConnection(ws, "conn-1")
        conn.subscribe("sub-1", "/topic/BUS_MVT_ALL")
        conn.subscribe("sub-2", "/topic/BUS_MVT_ALL")
        conn.subscribe("sub-3", "/topic/SERVICE_ALERTS")

        assert set(conn.get_subscriptions_for_topic("/topic/BUS_MVT_ALL")) == {
            "sub-1",
            "sub-2",
        }
        assert conn.get_subscriptions_for_topic("/topic/SERVICE_ALERTS") == ["sub-3"]
        assert conn.get_subscriptions_for_topic("/topic/TRAIN_MVT_ALL_TOC") == []

    @async_test
    async def test_handle_connect_sends_connected(self):
        ws = AsyncMock(spec=WebSocket)
        conn = StompConnection(ws, "conn-1")
        frame = StompFrame(command="CONNECT", headers={"heart-beat": "4000,4000"})

        await conn.handle_connect(frame)

        assert conn.connected is True
        ws.send_text.assert_called_once()
        sent = ws.send_text.call_args[0][0]
        assert "CONNECTED" in sent
        assert "version:1.2" in sent

    @async_test
    async def test_send_frame_swallows_transport_error(self):
        ws = AsyncMock(spec=WebSocket)
        ws.send_text.side_effect = RuntimeError("transport gone")
        conn = StompConnection(ws, "conn-err")

        # Must not raise
        await conn.send_frame(StompFrame(command="MESSAGE", body="hi"))


# ═════════════════════════════════════════════════════════════════════
#  StompBroker
# ═════════════════════════════════════════════════════════════════════


class TestStompBroker:
    """Unit tests for the broker: connections, broadcast, polling."""

    # -- connection management ---------------------------------------------

    @async_test
    async def test_add_and_remove_connection(self):
        b = StompBroker()
        ws = MagicMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")

        await b.add_connection(conn)
        assert "c1" in b.connections

        await b.remove_connection("c1")
        assert "c1" not in b.connections

    @async_test
    async def test_remove_nonexistent_connection_is_safe(self):
        b = StompBroker()
        await b.remove_connection("nope")  # must not raise

    # -- message IDs -------------------------------------------------------

    def test_next_message_id_increments(self):
        b = StompBroker()
        assert b._next_message_id() == "msg-1"
        assert b._next_message_id() == "msg-2"

    # -- _has_subscribers --------------------------------------------------

    def test_has_subscribers_true(self):
        b = StompBroker()
        ws = MagicMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")
        conn.connected = True
        conn.subscribe("sub-0", "/topic/BUS_MVT_ALL")
        b.connections["c1"] = conn

        assert b._has_subscribers("/topic/BUS_MVT_ALL") is True
        assert b._has_subscribers("/topic/SERVICE_ALERTS") is False

    def test_has_subscribers_false_when_empty(self):
        b = StompBroker()
        assert b._has_subscribers("/topic/BUS_MVT_ALL") is False

    def test_has_subscribers_false_when_disconnected(self):
        b = StompBroker()
        ws = MagicMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")
        conn.connected = False
        conn.subscribe("sub-0", "/topic/BUS_MVT_ALL")
        b.connections["c1"] = conn

        assert b._has_subscribers("/topic/BUS_MVT_ALL") is False

    # -- broadcast ---------------------------------------------------------

    @async_test
    async def test_broadcast_sends_to_subscriber(self):
        b = StompBroker()
        ws = AsyncMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")
        conn.connected = True
        conn.subscribe("sub-1", "/topic/BUS_MVT_ALL")
        await b.add_connection(conn)

        sent = await b.broadcast("/topic/BUS_MVT_ALL", {"hello": "world"})

        assert sent == 1
        ws.send_text.assert_called_once()
        parsed = StompFrame.parse(ws.send_text.call_args[0][0])
        assert parsed.command == "MESSAGE"
        assert parsed.headers["destination"] == "/topic/BUS_MVT_ALL"
        assert json.loads(parsed.body) == {"hello": "world"}

    @async_test
    async def test_broadcast_multiple_subscribers(self):
        b = StompBroker()
        ws1, ws2 = AsyncMock(spec=WebSocket), AsyncMock(spec=WebSocket)
        c1 = StompConnection(ws1, "c1")
        c1.connected = True
        c1.subscribe("s1", "/topic/BUS_MVT_ALL")
        c2 = StompConnection(ws2, "c2")
        c2.connected = True
        c2.subscribe("s2", "/topic/BUS_MVT_ALL")
        await b.add_connection(c1)
        await b.add_connection(c2)

        sent = await b.broadcast("/topic/BUS_MVT_ALL", {"n": 1})

        assert sent == 2
        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()

    @async_test
    async def test_broadcast_skips_disconnected(self):
        b = StompBroker()
        ws = AsyncMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")
        conn.connected = False
        conn.subscribe("sub-1", "/topic/BUS_MVT_ALL")
        await b.add_connection(conn)

        sent = await b.broadcast("/topic/BUS_MVT_ALL", {"x": 1})
        assert sent == 0

    @async_test
    async def test_broadcast_no_connections(self):
        b = StompBroker()
        sent = await b.broadcast("/topic/BUS_MVT_ALL", {"x": 1})
        assert sent == 0

    @async_test
    async def test_broadcast_wrong_topic(self):
        b = StompBroker()
        ws = AsyncMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")
        conn.connected = True
        conn.subscribe("sub-1", "/topic/SERVICE_ALERTS")
        await b.add_connection(conn)

        sent = await b.broadcast("/topic/BUS_MVT_ALL", {"x": 1})
        assert sent == 0

    @async_test
    async def test_broadcast_string_body_not_double_encoded(self):
        b = StompBroker()
        ws = AsyncMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")
        conn.connected = True
        conn.subscribe("sub-1", "/topic/BUS_MVT_ALL")
        await b.add_connection(conn)

        raw_json = '{"already":"json"}'
        await b.broadcast("/topic/BUS_MVT_ALL", raw_json)

        text = ws.send_text.call_args[0][0]
        parsed = StompFrame.parse(text)
        assert parsed.body == raw_json  # not double-encoded

    # -- configuration -----------------------------------------------------

    def test_configure_sets_values(self):
        b = StompBroker()
        factory = lambda: None  # noqa: E731
        b.configure(
            bus_live_factory=factory,
            poll_interval=30.0,
            poll_lat=51.5,
            poll_lon=-0.1,
        )
        assert b._bus_live_factory is factory
        assert b._poll_interval == 30.0
        assert b._poll_lat == 51.5
        assert b._poll_lon == -0.1

    # -- polling -----------------------------------------------------------

    @async_test
    async def test_poll_bus_live_broadcasts(self):
        b = StompBroker()
        mock_bl = MagicMock()
        mock_bl.get_bus_live.return_value = [
            ("1", "Lancaster", 54.046, -2.798, "SCCU"),
        ]
        b.configure(bus_live_factory=lambda: mock_bl, poll_interval=1)

        ws = AsyncMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")
        conn.connected = True
        conn.subscribe("sub-0", "/topic/BUS_MVT_ALL")
        b.connections["c1"] = conn

        await b._poll_bus_live()

        ws.send_text.assert_called_once()
        parsed = StompFrame.parse(ws.send_text.call_args[0][0])
        body = json.loads(parsed.body)
        assert body["type"] == "bus_positions"
        assert body["count"] == 1
        assert body["vehicles"][0]["line"] == "1"
        assert body["vehicles"][0]["destination"] == "Lancaster"
        assert body["vehicles"][0]["operator"] == "SCCU"

    @async_test
    async def test_poll_skips_when_no_subscribers(self):
        b = StompBroker()
        mock_bl = MagicMock()
        b.configure(bus_live_factory=lambda: mock_bl)

        await b._poll_bus_live()
        mock_bl.get_bus_live.assert_not_called()

    @async_test
    async def test_poll_skips_when_no_factory(self):
        b = StompBroker()
        # No factory configured — must not raise
        await b._poll_bus_live()

    @async_test
    async def test_poll_handles_exception_gracefully(self):
        b = StompBroker()
        mock_bl = MagicMock()
        mock_bl.get_bus_live.side_effect = RuntimeError("network error")
        b.configure(bus_live_factory=lambda: mock_bl)

        ws = MagicMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")
        conn.connected = True
        conn.subscribe("sub-0", "/topic/BUS_MVT_ALL")
        b.connections["c1"] = conn

        # Must not raise
        await b._poll_bus_live()

    @async_test
    async def test_start_and_stop_polling(self):
        b = StompBroker()
        b.configure(poll_interval=999)  # very large to avoid actual poll
        await b.start_polling()
        assert b._poll_task is not None

        await b.stop_polling()
        assert b._poll_task is None

    @async_test
    async def test_start_polling_idempotent(self):
        b = StompBroker()
        b.configure(poll_interval=999)
        await b.start_polling()
        task1 = b._poll_task

        await b.start_polling()
        assert b._poll_task is task1

        await b.stop_polling()

    @async_test
    async def test_stop_polling_when_not_started(self):
        b = StompBroker()
        await b.stop_polling()  # should not raise

    # -- frame handling ----------------------------------------------------

    @async_test
    async def test_handle_connect(self):
        b = StompBroker()
        ws = AsyncMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")

        await b.handle_frame(conn, StompFrame(command="CONNECT"))
        assert conn.connected is True

    @async_test
    async def test_handle_stomp_alias(self):
        """STOMP command is a valid alias for CONNECT."""
        b = StompBroker()
        ws = AsyncMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")

        await b.handle_frame(conn, StompFrame(command="STOMP"))
        assert conn.connected is True

    @async_test
    async def test_handle_subscribe(self):
        b = StompBroker()
        ws = AsyncMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")

        frame = StompFrame(
            command="SUBSCRIBE",
            headers={"id": "sub-0", "destination": "/topic/BUS_MVT_ALL"},
        )
        await b.handle_frame(conn, frame)
        assert conn.subscriptions == {"sub-0": "/topic/BUS_MVT_ALL"}

    @async_test
    async def test_handle_subscribe_missing_headers_sends_error(self):
        b = StompBroker()
        ws = AsyncMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")

        await b.handle_frame(conn, StompFrame(command="SUBSCRIBE"))

        ws.send_text.assert_called_once()
        text = ws.send_text.call_args[0][0]
        assert "ERROR" in text

    @async_test
    async def test_handle_subscribe_missing_destination(self):
        b = StompBroker()
        ws = AsyncMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")

        frame = StompFrame(command="SUBSCRIBE", headers={"id": "sub-0"})
        await b.handle_frame(conn, frame)

        ws.send_text.assert_called_once()
        assert "ERROR" in ws.send_text.call_args[0][0]

    @async_test
    async def test_handle_unsubscribe(self):
        b = StompBroker()
        ws = AsyncMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")
        conn.subscribe("sub-0", "/topic/BUS_MVT_ALL")

        frame = StompFrame(command="UNSUBSCRIBE", headers={"id": "sub-0"})
        await b.handle_frame(conn, frame)
        assert "sub-0" not in conn.subscriptions

    @async_test
    async def test_handle_unsubscribe_no_id(self):
        b = StompBroker()
        ws = AsyncMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")

        frame = StompFrame(command="UNSUBSCRIBE", headers={})
        await b.handle_frame(conn, frame)  # should not raise

    @async_test
    async def test_handle_disconnect_with_receipt(self):
        b = StompBroker()
        ws = AsyncMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")
        conn.connected = True

        frame = StompFrame(command="DISCONNECT", headers={"receipt": "rcpt-1"})
        await b.handle_frame(conn, frame)

        assert conn.connected is False
        ws.send_text.assert_called_once()
        text = ws.send_text.call_args[0][0]
        assert "RECEIPT" in text
        assert "rcpt-1" in text

    @async_test
    async def test_handle_disconnect_without_receipt(self):
        b = StompBroker()
        ws = AsyncMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")
        conn.connected = True

        frame = StompFrame(command="DISCONNECT", headers={})
        await b.handle_frame(conn, frame)

        assert conn.connected is False
        ws.send_text.assert_not_called()

    @async_test
    async def test_handle_unknown_command_ignored(self):
        b = StompBroker()
        ws = AsyncMock(spec=WebSocket)
        conn = StompConnection(ws, "c1")

        frame = StompFrame(command="FOOBAR")
        await b.handle_frame(conn, frame)  # must not raise
        ws.send_text.assert_not_called()


# ═════════════════════════════════════════════════════════════════════
#  WebSocket endpoint integration tests
# ═════════════════════════════════════════════════════════════════════


class TestWebSocketEndpoint:
    """Integration tests using a minimal FastAPI app + TestClient."""

    @pytest.fixture(autouse=True)
    def _reset_broker(self):
        """Reset the module-level broker singleton between tests."""
        broker.connections.clear()
        broker._message_counter = 0
        broker._bus_live_factory = None
        broker._poll_task = None
        yield
        broker.connections.clear()

    @pytest.fixture()
    def ws_client(self):
        """Create a TestClient with just the WebSocket route."""
        test_app = FastAPI()
        test_app.add_api_websocket_route("/ws/live", websocket_endpoint)
        return TestClient(test_app)

    def test_connect_and_receive_connected(self, ws_client):
        with ws_client.websocket_connect("/ws/live") as ws:
            ws.send_text("CONNECT\naccept-version:1.2\nheart-beat:0,0\n\n\x00")
            data = ws.receive_text()
            frame = StompFrame.parse(data)
            assert frame is not None
            assert frame.command == "CONNECTED"
            assert frame.headers["version"] == "1.2"
            assert frame.headers["server"] == "transport-backend/1.0"

    def test_stomp_command_alias(self, ws_client):
        with ws_client.websocket_connect("/ws/live") as ws:
            ws.send_text("STOMP\naccept-version:1.2\n\n\x00")
            data = ws.receive_text()
            frame = StompFrame.parse(data)
            assert frame.command == "CONNECTED"

    def test_disconnect_with_receipt(self, ws_client):
        with ws_client.websocket_connect("/ws/live") as ws:
            ws.send_text("CONNECT\naccept-version:1.2\n\n\x00")
            ws.receive_text()  # CONNECTED

            ws.send_text("DISCONNECT\nreceipt:rcpt-42\n\n\x00")
            data = ws.receive_text()
            frame = StompFrame.parse(data)
            assert frame.command == "RECEIPT"
            assert frame.headers["receipt-id"] == "rcpt-42"

    def test_heartbeat_ignored(self, ws_client):
        with ws_client.websocket_connect("/ws/live") as ws:
            ws.send_text("CONNECT\naccept-version:1.2\n\n\x00")
            ws.receive_text()  # CONNECTED

            # Heartbeat (just newline) should be silently ignored
            ws.send_text("\n")

            # Connection still works after heartbeat
            ws.send_text("DISCONNECT\nreceipt:hb\n\n\x00")
            data = ws.receive_text()
            assert "RECEIPT" in data

    def test_subscribe_error_missing_headers(self, ws_client):
        with ws_client.websocket_connect("/ws/live") as ws:
            ws.send_text("CONNECT\naccept-version:1.2\n\n\x00")
            ws.receive_text()  # CONNECTED

            # SUBSCRIBE without required headers → ERROR
            ws.send_text("SUBSCRIBE\n\n\x00")
            data = ws.receive_text()
            frame = StompFrame.parse(data)
            assert frame.command == "ERROR"

    def test_multiple_frames_in_one_message(self, ws_client):
        """Client may send multiple NULL-separated frames in one WS msg."""
        with ws_client.websocket_connect("/ws/live") as ws:
            # CONNECT + DISCONNECT with receipt in a single WS text message
            combined = (
                "CONNECT\naccept-version:1.2\n\n\x00"
                "DISCONNECT\nreceipt:multi\n\n\x00"
            )
            ws.send_text(combined)

            # Should receive CONNECTED then RECEIPT
            data1 = ws.receive_text()
            assert "CONNECTED" in data1
            data2 = ws.receive_text()
            assert "RECEIPT" in data2
            assert "multi" in data2

    def test_connection_cleanup_on_close(self, ws_client):
        with ws_client.websocket_connect("/ws/live") as ws:
            ws.send_text("CONNECT\naccept-version:1.2\n\n\x00")
            ws.receive_text()
            assert len(broker.connections) == 1

        # After the WS closes, the endpoint removes the connection
        assert len(broker.connections) == 0
