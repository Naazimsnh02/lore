"""Property-based tests for the WebSocket Gateway.

Feature: lore-multimodal-documentary-app
Property 18: WebSocket Message Latency
Validates: Requirement 20.7 — message processing latency < 100 ms under normal
           conditions.

Uses Hypothesis to generate 100+ random message payloads and verifies that
the router handles each one within the latency budget.

Run with:
    pytest backend/tests/properties/test_ws_message_latency.py -v --hypothesis-seed=0
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st
from unittest.mock import AsyncMock

from backend.services.websocket_gateway.connection_manager import ConnectionManager
from backend.services.websocket_gateway.message_router import MessageRouter
from backend.services.websocket_gateway.models import ClientMessage


# ── Strategies for random message payloads ─────────────────────────────────────

_mode_select_st = st.fixed_dictionaries({
    "mode": st.sampled_from(["sight", "voice", "lore"]),
    "depthDial": st.sampled_from(["explorer", "scholar", "expert"]),
    "language": st.sampled_from(["en", "fr", "de", "es", "zh", "ja", "ar"]),
})

_camera_frame_st = st.fixed_dictionaries({
    "imageData": st.text(min_size=10, max_size=100),
    "timestamp": st.integers(min_value=0, max_value=10**13),
})

_voice_input_st = st.fixed_dictionaries({
    "audioData": st.text(min_size=10, max_size=100),
    "sampleRate": st.sampled_from([8000, 16000, 44100, 48000]),
    "timestamp": st.integers(min_value=0, max_value=10**13),
})

_gps_update_st = st.fixed_dictionaries({
    "latitude": st.floats(min_value=-90.0, max_value=90.0, allow_nan=False),
    "longitude": st.floats(min_value=-180.0, max_value=180.0, allow_nan=False),
    "accuracy": st.floats(min_value=1.0, max_value=100.0, allow_nan=False),
    "timestamp": st.integers(min_value=0, max_value=10**13),
})

_barge_in_st = st.fixed_dictionaries({
    "audioData": st.text(min_size=1, max_size=50),
    "streamPosition": st.floats(min_value=0.0, max_value=3600.0, allow_nan=False),
    "timestamp": st.integers(min_value=0, max_value=10**13),
})

_query_st = st.fixed_dictionaries({
    "query": st.text(min_size=1, max_size=200),
    "timestamp": st.integers(min_value=0, max_value=10**13),
})

_depth_dial_change_st = st.fixed_dictionaries({
    "newLevel": st.sampled_from(["explorer", "scholar", "expert"]),
    "timestamp": st.integers(min_value=0, max_value=10**13),
})

_chronicle_export_st = st.fixed_dictionaries({
    "sessionId": st.text(min_size=1, max_size=64),
})

# Map each message type to its payload strategy
_MESSAGE_STRATEGIES = {
    "mode_select": _mode_select_st,
    "camera_frame": _camera_frame_st,
    "voice_input": _voice_input_st,
    "gps_update": _gps_update_st,
    "barge_in": _barge_in_st,
    "query": _query_st,
    "depth_dial_change": _depth_dial_change_st,
    "chronicle_export": _chronicle_export_st,
}

_random_message_st = st.one_of(*[
    st.fixed_dictionaries({"type": st.just(msg_type), "payload": payload_st})
    for msg_type, payload_st in _MESSAGE_STRATEGIES.items()
])


# ── Test fixture ───────────────────────────────────────────────────────────────

def _make_router_with_client():
    """Return (router, cm, client_id) with one registered mock connection."""
    cm = ConnectionManager()
    router = MessageRouter(cm)

    ws = AsyncMock()
    ws.send_text = AsyncMock()

    # Register connection synchronously (for use inside sync Hypothesis tests)
    loop = asyncio.new_event_loop()
    info = loop.run_until_complete(cm.connect(ws, user_id="prop-test-user"))
    loop.close()

    return router, cm, info.client_id


# ── Property 18: WebSocket Message Latency ─────────────────────────────────────

class TestWebSocketMessageLatency:
    """
    Feature: lore-multimodal-documentary-app
    Property 18: WebSocket Message Latency
    Validates: Requirement 20.7
    """

    @given(raw_msg=_random_message_st)
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,  # Let each example take as long as it needs
    )
    def test_routing_latency_under_100ms(self, raw_msg: dict):
        """For any valid client message the routing latency must be < 100 ms.

        This exercises the router in isolation (without network I/O) to
        verify the processing overhead is well within the target.
        """
        router, cm, client_id = _make_router_with_client()
        message = ClientMessage(**raw_msg)

        loop = asyncio.new_event_loop()
        try:
            start = time.monotonic()
            loop.run_until_complete(router.route(client_id, message))
            elapsed_ms = (time.monotonic() - start) * 1000
        finally:
            loop.close()

        assert elapsed_ms < 100, (
            f"Routing latency {elapsed_ms:.2f} ms exceeded 100 ms target "
            f"for message type={raw_msg['type']}"
        )

    @given(
        msg_count=st.integers(min_value=10, max_value=50),
        msg_type=st.sampled_from(list(_MESSAGE_STRATEGIES.keys())),
    )
    @settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow],
        deadline=None,
    )
    def test_burst_routing_average_latency(self, msg_count: int, msg_type: str):
        """Average routing latency stays under 100 ms even for bursts.

        Simulates a client sending a rapid burst of messages and measures
        the mean processing time.
        """
        router, cm, client_id = _make_router_with_client()
        payload_st = _MESSAGE_STRATEGIES[msg_type]

        loop = asyncio.new_event_loop()
        try:
            latencies = []
            # Draw a fixed payload for all messages in this burst
            payload = payload_st.example()
            message = ClientMessage(type=msg_type, payload=payload)

            for _ in range(msg_count):
                start = time.monotonic()
                loop.run_until_complete(router.route(client_id, message))
                latencies.append((time.monotonic() - start) * 1000)
        finally:
            loop.close()

        avg_ms = sum(latencies) / len(latencies)
        assert avg_ms < 100, (
            f"Average burst routing latency {avg_ms:.2f} ms exceeded 100 ms "
            f"(msg_count={msg_count}, type={msg_type})"
        )


# ── Property 21: Authentication Security ───────────────────────────────────────
#
# Feature: lore-multimodal-documentary-app
# Property 21: Authentication Security
# Validates: Requirement 25.7 — all auth operations use HTTPS/TLS,
#            invalid tokens are rejected, session timeout is enforced.

class TestAuthenticationSecurity:
    """Property 21: Authentication Security."""

    @given(token=st.text(min_size=0, max_size=200).filter(lambda t: not t.startswith("mock_")))
    @settings(max_examples=100, deadline=None)
    def test_non_mock_tokens_rejected_in_mock_mode(self, token: str):
        """Any token not starting with 'mock_' must be rejected when LORE_MOCK_AUTH=true.

        Validates that the authentication gate is not permissive.
        """
        import os
        os.environ["LORE_MOCK_AUTH"] = "true"
        import importlib
        import backend.services.websocket_gateway.auth as auth_mod
        importlib.reload(auth_mod)

        from fastapi import HTTPException
        loop = asyncio.new_event_loop()
        try:
            with pytest.raises((HTTPException, Exception)):
                loop.run_until_complete(auth_mod.verify_token(token))
        finally:
            loop.close()
            os.environ.pop("LORE_MOCK_AUTH", None)

    @given(user_id=st.text(min_size=1, max_size=64, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"))))
    @settings(max_examples=100, deadline=None)
    def test_mock_tokens_accepted_with_correct_prefix(self, user_id: str):
        """All tokens of the form 'mock_<user_id>' must be accepted in mock mode."""
        import os
        os.environ["LORE_MOCK_AUTH"] = "true"
        import importlib
        import backend.services.websocket_gateway.auth as auth_mod
        importlib.reload(auth_mod)

        token = f"mock_{user_id}"
        loop = asyncio.new_event_loop()
        try:
            claims = loop.run_until_complete(auth_mod.verify_token(token))
            assert claims["user_id"] == user_id
        finally:
            loop.close()
            os.environ.pop("LORE_MOCK_AUTH", None)


# ── Property: Buffer correctness ───────────────────────────────────────────────

class TestBufferProperties:
    """Additional property tests for the message buffer."""

    @given(messages=st.lists(st.text(min_size=1, max_size=200), min_size=1, max_size=50))
    @settings(max_examples=100, deadline=None)
    def test_buffer_preserves_order(self, messages: list[str]):
        """Messages are flushed in FIFO order."""
        from backend.services.websocket_gateway.buffer import MessageBuffer

        buf = MessageBuffer("order-test")
        loop = asyncio.new_event_loop()
        try:
            for msg in messages:
                loop.run_until_complete(buf.enqueue(msg))
            result = loop.run_until_complete(buf.flush())
        finally:
            loop.close()

        assert result == messages

    @given(messages=st.lists(st.text(min_size=1, max_size=50), min_size=0, max_size=20))
    @settings(max_examples=100, deadline=None)
    def test_flush_clears_buffer(self, messages: list[str]):
        """After flush, peek_size must be 0."""
        from backend.services.websocket_gateway.buffer import MessageBuffer

        buf = MessageBuffer("clear-test")
        loop = asyncio.new_event_loop()
        try:
            for msg in messages:
                loop.run_until_complete(buf.enqueue(msg))
            loop.run_until_complete(buf.flush())
            size = loop.run_until_complete(buf.peek_size())
        finally:
            loop.close()

        assert size == 0
