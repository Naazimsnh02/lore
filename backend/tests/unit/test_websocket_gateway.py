"""Unit tests for the WebSocket Gateway service.

Covers:
  - MessageBuffer: enqueue, flush, expiry pruning, overflow
  - ConnectionManager: connect/disconnect, metadata updates, buffer flush
  - MessageRouter: all message type handlers
  - Auth: mock token verification
  - Models: ClientMessage validation

Run with:
    pytest backend/tests/unit/test_websocket_gateway.py -v
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from backend.services.websocket_gateway.buffer import MessageBuffer
from backend.services.websocket_gateway.connection_manager import ConnectionManager
from backend.services.websocket_gateway.message_router import MessageRouter
from backend.services.websocket_gateway.models import (
    ClientMessage,
    ComponentStatus,
    ConnectionInfo,
    DepthDial,
    OperatingMode,
    ServerMessage,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_client_message(msg_type: str, payload: dict) -> ClientMessage:
    return ClientMessage(type=msg_type, payload=payload)


def make_mock_websocket() -> MagicMock:
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    return ws


# ── MessageBuffer tests ────────────────────────────────────────────────────────

class TestMessageBuffer:

    @pytest.mark.asyncio
    async def test_enqueue_and_flush_returns_messages(self):
        buf = MessageBuffer("client-1")
        await buf.enqueue("msg-a")
        await buf.enqueue("msg-b")
        result = await buf.flush()
        assert result == ["msg-a", "msg-b"]

    @pytest.mark.asyncio
    async def test_flush_empties_buffer(self):
        buf = MessageBuffer("client-1")
        await buf.enqueue("msg-a")
        await buf.flush()
        assert await buf.peek_size() == 0

    @pytest.mark.asyncio
    async def test_expired_messages_not_returned(self):
        """Messages older than max_age_seconds are pruned on flush."""
        buf = MessageBuffer("client-1", max_age_seconds=0.05)  # 50 ms TTL
        await buf.enqueue("old-msg")
        await asyncio.sleep(0.1)  # Let it expire
        result = await buf.flush()
        assert result == []

    @pytest.mark.asyncio
    async def test_overflow_drops_oldest_message(self):
        from backend.services.websocket_gateway.buffer import MAX_BUFFER_SIZE
        buf = MessageBuffer("client-x")
        # Fill buffer to capacity
        for i in range(MAX_BUFFER_SIZE):
            await buf.enqueue(f"msg-{i}")
        # This should drop "msg-0" to make room
        await buf.enqueue("overflow-msg")
        messages = await buf.flush()
        assert "msg-0" not in messages
        assert "overflow-msg" in messages
        assert len(messages) == MAX_BUFFER_SIZE

    @pytest.mark.asyncio
    async def test_flush_of_empty_buffer_returns_empty_list(self):
        buf = MessageBuffer("client-empty")
        assert await buf.flush() == []

    @pytest.mark.asyncio
    async def test_peek_size_reflects_enqueue(self):
        buf = MessageBuffer("client-1")
        await buf.enqueue("a")
        await buf.enqueue("b")
        assert await buf.peek_size() == 2


# ── ConnectionManager tests ────────────────────────────────────────────────────

class TestConnectionManager:

    @pytest.mark.asyncio
    async def test_connect_registers_connection(self):
        cm = ConnectionManager()
        ws = make_mock_websocket()
        info = await cm.connect(ws, user_id="user-1")
        assert info.user_id == "user-1"
        assert cm.active_count() == 1

    @pytest.mark.asyncio
    async def test_disconnect_removes_connection(self):
        cm = ConnectionManager()
        ws = make_mock_websocket()
        info = await cm.connect(ws, user_id="user-1")
        await cm.disconnect(info.client_id)
        assert cm.active_count() == 0

    @pytest.mark.asyncio
    async def test_send_to_active_client(self):
        cm = ConnectionManager()
        ws = make_mock_websocket()
        info = await cm.connect(ws, user_id="user-2")
        msg = ServerMessage(type="status", payload={"event": "test"})
        await cm.send(info.client_id, msg)
        ws.send_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_to_offline_client_buffers(self):
        cm = ConnectionManager()
        ws = make_mock_websocket()
        info = await cm.connect(ws, user_id="user-3")
        await cm.disconnect(info.client_id)

        msg = ServerMessage(type="status", payload={"event": "buffered"})
        await cm.send(info.client_id, msg)

        # Buffer should now have the message
        buf = cm._buffers.get(info.client_id)
        assert buf is not None
        assert await buf.peek_size() == 1

    @pytest.mark.asyncio
    async def test_reconnect_flushes_buffer(self):
        cm = ConnectionManager()
        ws1 = make_mock_websocket()
        info = await cm.connect(ws1, user_id="user-4")
        client_id = info.client_id
        await cm.disconnect(client_id)

        # Buffer a message while offline
        msg = ServerMessage(type="status", payload={"event": "replay"})
        await cm.send(client_id, msg)

        # Reconnect with same client_id — buffer should be flushed
        ws2 = make_mock_websocket()
        await cm.connect(ws2, user_id="user-4", client_id=client_id)

        ws2.send_text.assert_awaited()  # Buffer was flushed

    @pytest.mark.asyncio
    async def test_update_mode(self):
        cm = ConnectionManager()
        ws = make_mock_websocket()
        info = await cm.connect(ws, user_id="user-5")
        cm.update_mode(info.client_id, OperatingMode.VOICE)
        assert cm.get_connection_info(info.client_id).mode == OperatingMode.VOICE

    @pytest.mark.asyncio
    async def test_update_depth_dial(self):
        cm = ConnectionManager()
        ws = make_mock_websocket()
        info = await cm.connect(ws, user_id="user-6")
        cm.update_depth_dial(info.client_id, DepthDial.EXPERT)
        assert cm.get_connection_info(info.client_id).depth_dial == DepthDial.EXPERT

    @pytest.mark.asyncio
    async def test_cleanup_removes_stale_buffers(self):
        cm = ConnectionManager()
        ws = make_mock_websocket()
        info = await cm.connect(ws, user_id="user-7")
        await cm.disconnect(info.client_id)
        # Force stale last_seen
        cm._info[info.client_id].last_seen = time.time() - 200
        cm.cleanup_stale_buffers()
        assert info.client_id not in cm._buffers

    @pytest.mark.asyncio
    async def test_active_count_multiple_connections(self):
        cm = ConnectionManager()
        for i in range(5):
            ws = make_mock_websocket()
            await cm.connect(ws, user_id=f"user-{i}")
        assert cm.active_count() == 5


# ── MessageRouter tests ────────────────────────────────────────────────────────

class TestMessageRouter:

    def _make_router(self):
        cm = ConnectionManager()
        return MessageRouter(cm), cm

    @pytest.mark.asyncio
    async def test_mode_select_returns_status(self):
        router, cm = self._make_router()
        ws = make_mock_websocket()
        info = await cm.connect(ws, user_id="u1")
        msg = make_client_message("mode_select", {
            "mode": "voice",
            "depthDial": "scholar",
            "language": "en",
        })
        responses = await router.route(info.client_id, msg)
        assert len(responses) == 1
        assert responses[0].type == "status"

    @pytest.mark.asyncio
    async def test_mode_select_updates_connection_mode(self):
        router, cm = self._make_router()
        ws = make_mock_websocket()
        info = await cm.connect(ws, user_id="u2")
        msg = make_client_message("mode_select", {
            "mode": "lore",
            "depthDial": "expert",
            "language": "fr",
        })
        await router.route(info.client_id, msg)
        conn = cm.get_connection_info(info.client_id)
        assert conn.mode == OperatingMode.LORE
        assert conn.depth_dial == DepthDial.EXPERT
        assert conn.language == "fr"

    @pytest.mark.asyncio
    async def test_camera_frame_returns_empty_list(self):
        """Camera frame handling is async — no synchronous response expected."""
        router, cm = self._make_router()
        ws = make_mock_websocket()
        info = await cm.connect(ws, user_id="u3")
        msg = make_client_message("camera_frame", {
            "imageData": "base64data==",
            "timestamp": 1234567890,
        })
        responses = await router.route(info.client_id, msg)
        assert responses == []

    @pytest.mark.asyncio
    async def test_voice_input_returns_empty_list(self):
        router, cm = self._make_router()
        ws = make_mock_websocket()
        info = await cm.connect(ws, user_id="u4")
        msg = make_client_message("voice_input", {
            "audioData": "base64audio==",
            "sampleRate": 16000,
            "timestamp": 1234567890,
        })
        responses = await router.route(info.client_id, msg)
        assert responses == []

    @pytest.mark.asyncio
    async def test_barge_in_returns_acknowledgement(self):
        """Barge-in must return an immediate ACK (Requirement 19.2)."""
        router, cm = self._make_router()
        ws = make_mock_websocket()
        info = await cm.connect(ws, user_id="u5")
        msg = make_client_message("barge_in", {
            "audioData": "base64audio==",
            "streamPosition": 12.5,
            "timestamp": 1234567890,
        })
        responses = await router.route(info.client_id, msg)
        assert len(responses) == 1
        assert responses[0].type == "status"
        assert responses[0].payload["event"] == "barge_in_acknowledged"

    @pytest.mark.asyncio
    async def test_depth_dial_change_updates_state(self):
        router, cm = self._make_router()
        ws = make_mock_websocket()
        info = await cm.connect(ws, user_id="u6")
        msg = make_client_message("depth_dial_change", {
            "newLevel": "explorer",
            "timestamp": 1234567890,
        })
        await router.route(info.client_id, msg)
        assert cm.get_connection_info(info.client_id).depth_dial == DepthDial.EXPLORER

    @pytest.mark.asyncio
    async def test_unknown_message_type_returns_error(self):
        router, cm = self._make_router()
        ws = make_mock_websocket()
        info = await cm.connect(ws, user_id="u7")
        # Bypass model validation to inject an unknown type
        msg = MagicMock()
        msg.type = "unsupported_type"
        msg.payload = {}
        msg.timestamp = int(time.time() * 1000)
        responses = await router.route(info.client_id, msg)
        assert len(responses) == 1
        assert responses[0].type == "error"
        assert responses[0].payload["errorCode"] == "UNKNOWN_MESSAGE_TYPE"

    @pytest.mark.asyncio
    async def test_gps_update_returns_empty_list(self):
        router, cm = self._make_router()
        ws = make_mock_websocket()
        info = await cm.connect(ws, user_id="u8")
        msg = make_client_message("gps_update", {
            "latitude": 51.5074,
            "longitude": -0.1278,
            "accuracy": 5.0,
            "timestamp": 1234567890,
        })
        responses = await router.route(info.client_id, msg)
        assert responses == []


# ── Auth tests ─────────────────────────────────────────────────────────────────

class TestMockAuth:

    @pytest.mark.asyncio
    async def test_mock_token_accepted(self):
        import os
        os.environ["LORE_MOCK_AUTH"] = "true"
        # Re-import to pick up env change
        import importlib
        import backend.services.websocket_gateway.auth as auth_mod
        importlib.reload(auth_mod)

        claims = await auth_mod.verify_token("mock_testuser")
        assert claims["user_id"] == "testuser"

        os.environ.pop("LORE_MOCK_AUTH", None)

    @pytest.mark.asyncio
    async def test_empty_token_raises(self):
        from fastapi import HTTPException
        import os
        os.environ["LORE_MOCK_AUTH"] = "true"
        import importlib
        import backend.services.websocket_gateway.auth as auth_mod
        importlib.reload(auth_mod)

        with pytest.raises(HTTPException) as exc_info:
            await auth_mod.verify_token("")
        assert exc_info.value.status_code == 401

        os.environ.pop("LORE_MOCK_AUTH", None)

    @pytest.mark.asyncio
    async def test_invalid_mock_token_raises(self):
        from fastapi import HTTPException
        import os
        os.environ["LORE_MOCK_AUTH"] = "true"
        import importlib
        import backend.services.websocket_gateway.auth as auth_mod
        importlib.reload(auth_mod)

        with pytest.raises(HTTPException):
            await auth_mod.verify_token("not-a-mock-token")

        os.environ.pop("LORE_MOCK_AUTH", None)


# ── Model validation tests ─────────────────────────────────────────────────────

class TestClientMessageValidation:

    def test_valid_mode_select_message(self):
        raw = json.dumps({
            "type": "mode_select",
            "payload": {"mode": "sight", "depthDial": "explorer", "language": "en"},
        })
        msg = ClientMessage.model_validate_json(raw)
        assert msg.type == "mode_select"

    def test_invalid_message_type_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ClientMessage(type="unsupported_type", payload={})

    def test_timestamp_defaults_to_now(self):
        before = int(time.time() * 1000)
        msg = ClientMessage(type="gps_update", payload={})
        after = int(time.time() * 1000)
        assert before <= msg.timestamp <= after
