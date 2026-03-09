"""Property-based tests for Barge-In Handler response latency.

Property 17: Barge-In Response Latency
Validates: Requirement 19.2 - Pause playback within 200ms of speech detection

This test verifies that the BargeInHandler consistently acknowledges
interruptions within the 200ms requirement across various scenarios.

Design reference: LORE design.md, Section 9 (Barge-In Handler).
Requirements: 19.2 (pause within 200ms).
"""

from __future__ import annotations

import asyncio
import base64
import time
from unittest.mock import AsyncMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.services.barge_in.handler import BargeInHandler
from backend.services.barge_in.models import Interruption
from backend.services.voice_mode.models import (
    VoiceModeContext,
    VoiceModeResponse,
    VoiceModeEvent,
)


# ── Hypothesis Strategies ────────────────────────────────────────────────────


@st.composite
def interruption_strategy(draw):
    """Generate random interruption events."""
    # Generate random audio data (simulating various audio lengths)
    audio_length = draw(st.integers(min_value=100, max_value=10000))
    audio_data = base64.b64encode(b"x" * audio_length).decode("ascii")
    
    # Random stream position
    stream_position = draw(st.floats(min_value=0.0, max_value=3600.0, allow_nan=False))
    
    # Random client and session IDs
    client_id = draw(st.text(min_size=5, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"))))
    session_id = draw(st.text(min_size=5, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"))))
    
    return Interruption(
        audio_data=audio_data,
        stream_position=stream_position,
        client_id=client_id,
        session_id=session_id,
    )


# ── Property Tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
@given(interruption=interruption_strategy())
@settings(max_examples=100, deadline=5000)
async def test_property_acknowledgment_within_200ms(interruption):
    """Property 17: Barge-in acknowledgment must occur within 200ms.
    
    Requirement 19.2: WHEN user speech is detected during narration,
    THE Barge_In_Handler SHALL pause Documentary_Stream within 200 milliseconds.
    
    This property test verifies that regardless of:
    - Audio data size
    - Stream position
    - Client ID
    - Session ID
    
    The acknowledgment time is always < 200ms.
    """
    # Setup mock voice handler
    mock_voice_handler = AsyncMock()
    mock_voice_handler.process_voice_input.return_value = VoiceModeResponse(
        event=VoiceModeEvent.TOPIC_DETECTED,
        topic="test",
        payload={"context": {
            "topic": "test",
            "original_query": "test query",
            "language": "en",
            "mode": "voice",
            "intent": "new_topic",
            "confidence": 0.9,
            "noise_cancelled": False,
        }},
    )
    
    handler = BargeInHandler(voice_handler=mock_voice_handler)
    
    # Process interruption
    result = await handler.process_interruption(interruption)
    
    # Verify acknowledgment timing
    assert result.acknowledged is True
    assert result.acknowledgment_time_ms < 200.0, (
        f"Acknowledgment took {result.acknowledgment_time_ms:.2f}ms, "
        f"exceeds 200ms requirement (Req 19.2)"
    )


@pytest.mark.asyncio
@given(
    interruptions=st.lists(
        interruption_strategy(),
        min_size=2,
        max_size=5,
    )
)
@settings(max_examples=50, deadline=10000)
async def test_property_multiple_interruptions_all_within_200ms(interruptions):
    """Property 17 (Extended): Multiple interruptions all acknowledged within 200ms.
    
    Verifies that the handler maintains consistent performance even when
    processing multiple interruptions in sequence.
    """
    mock_voice_handler = AsyncMock()
    mock_voice_handler.process_voice_input.return_value = VoiceModeResponse(
        event=VoiceModeEvent.TOPIC_DETECTED,
        topic="test", payload={"context": {"topic": "test", "original_query": "test", "language": "en", "mode": "voice", "intent": "new_topic", "confidence": 0.9, "noise_cancelled": False}},
    )
    
    handler = BargeInHandler(voice_handler=mock_voice_handler)
    
    # Process all interruptions
    results = []
    for interruption in interruptions:
        result = await handler.process_interruption(interruption)
        results.append(result)
        
        # Small delay between interruptions
        await asyncio.sleep(0.01)
    
    # Verify all acknowledgments were within 200ms
    for i, result in enumerate(results):
        assert result.acknowledged is True
        assert result.acknowledgment_time_ms < 200.0, (
            f"Interruption {i} took {result.acknowledgment_time_ms:.2f}ms, "
            f"exceeds 200ms requirement"
        )


@pytest.mark.asyncio
@given(interruption=interruption_strategy())
@settings(max_examples=100, deadline=5000)
async def test_property_playback_paused_immediately(interruption):
    """Property 17 (Behavioral): Playback state is paused immediately.
    
    Verifies that the playback state is updated to paused as part of the
    acknowledgment process, not deferred to later processing.
    """
    mock_voice_handler = AsyncMock()
    mock_voice_handler.process_voice_input.return_value = VoiceModeResponse(
        event=VoiceModeEvent.TOPIC_DETECTED,
        topic="test", payload={"context": {"topic": "test", "original_query": "test", "language": "en", "mode": "voice", "intent": "new_topic", "confidence": 0.9, "noise_cancelled": False}},
    )
    
    handler = BargeInHandler(voice_handler=mock_voice_handler)
    
    # Setup initial playback state
    handler.update_playback_position(
        interruption.client_id,
        interruption.stream_position - 1.0,  # Playing before interruption
    )
    
    # Process interruption
    result = await handler.process_interruption(interruption)
    
    # Verify playback is paused
    assert result.acknowledged is True
    assert handler.is_paused(interruption.client_id) is True
    
    # Verify position is updated to interruption point
    state = handler.get_playback_state(interruption.client_id)
    assert state is not None
    assert state.current_position == interruption.stream_position


@pytest.mark.asyncio
@given(
    stream_position=st.floats(min_value=0.0, max_value=3600.0, allow_nan=False),
    audio_size=st.integers(min_value=100, max_value=50000),
)
@settings(max_examples=100, deadline=5000)
async def test_property_acknowledgment_independent_of_audio_size(stream_position, audio_size):
    """Property 17 (Independence): Acknowledgment time independent of audio size.
    
    Verifies that acknowledgment happens quickly regardless of the size of
    the audio data being processed. The acknowledgment should not wait for
    full audio transcription.
    """
    mock_voice_handler = AsyncMock()
    
    # Simulate variable transcription time based on audio size
    async def mock_transcribe(*args, **kwargs):
        # Simulate processing delay proportional to audio size
        await asyncio.sleep(audio_size / 100000.0)  # Up to 0.5s for large audio
        return VoiceModeResponse(
            event=VoiceModeEvent.TOPIC_DETECTED,
            topic="test", payload={"context": {"topic": "test", "original_query": "test", "language": "en", "mode": "voice", "intent": "new_topic", "confidence": 0.9, "noise_cancelled": False}},
        )
    
    mock_voice_handler.process_voice_input = mock_transcribe
    
    handler = BargeInHandler(voice_handler=mock_voice_handler)
    
    audio_data = base64.b64encode(b"x" * audio_size).decode("ascii")
    interruption = Interruption(
        audio_data=audio_data,
        stream_position=stream_position,
        client_id="test_client",
        session_id="test_session",
    )
    
    result = await handler.process_interruption(interruption)
    
    # Acknowledgment should still be fast even if transcription is slow
    assert result.acknowledged is True
    assert result.acknowledgment_time_ms < 200.0, (
        f"Acknowledgment took {result.acknowledgment_time_ms:.2f}ms with "
        f"audio size {audio_size} bytes"
    )


@pytest.mark.asyncio
@given(interruption=interruption_strategy())
@settings(max_examples=50, deadline=5000)
async def test_property_pause_callback_invoked_within_200ms(interruption):
    """Property 17 (Callback): Pause callback invoked within acknowledgment window.
    
    Verifies that if a pause callback is registered, it is invoked as part
    of the acknowledgment process (within 200ms).
    """
    mock_voice_handler = AsyncMock()
    mock_voice_handler.process_voice_input.return_value = VoiceModeResponse(
        event=VoiceModeEvent.TOPIC_DETECTED,
        topic="test", payload={"context": {"topic": "test", "original_query": "test", "language": "en", "mode": "voice", "intent": "new_topic", "confidence": 0.9, "noise_cancelled": False}},
    )
    
    callback_invoked = False
    callback_time = None
    start_time = None
    
    def pause_callback(client_id, position):
        nonlocal callback_invoked, callback_time
        callback_invoked = True
        callback_time = (time.monotonic() - start_time) * 1000.0
    
    handler = BargeInHandler(
        voice_handler=mock_voice_handler,
        on_pause_callback=pause_callback,
    )
    
    start_time = time.monotonic()
    result = await handler.process_interruption(interruption)
    
    assert result.acknowledged is True
    assert callback_invoked is True
    assert callback_time is not None
    assert callback_time < 200.0, (
        f"Pause callback invoked at {callback_time:.2f}ms, "
        f"exceeds 200ms requirement"
    )


# ── Statistical Analysis Tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_acknowledgment_latency_statistics():
    """Statistical analysis of acknowledgment latency.
    
    Runs 200 interruptions and verifies:
    - All are < 200ms (requirement)
    - Mean is significantly below 200ms
    - 99th percentile is < 200ms
    """
    mock_voice_handler = AsyncMock()
    mock_voice_handler.process_voice_input.return_value = VoiceModeResponse(
        event=VoiceModeEvent.TOPIC_DETECTED,
        topic="test", payload={"context": {"topic": "test", "original_query": "test", "language": "en", "mode": "voice", "intent": "new_topic", "confidence": 0.9, "noise_cancelled": False}},
    )
    
    handler = BargeInHandler(voice_handler=mock_voice_handler)
    
    latencies = []
    
    for i in range(200):
        audio_data = base64.b64encode(b"x" * (i * 50)).decode("ascii")
        interruption = Interruption(
            audio_data=audio_data,
            stream_position=float(i),
            client_id=f"client_{i}",
            session_id=f"session_{i}",
        )
        
        result = await handler.process_interruption(interruption)
        latencies.append(result.acknowledgment_time_ms)
    
    # Statistical checks
    max_latency = max(latencies)
    mean_latency = sum(latencies) / len(latencies)
    latencies_sorted = sorted(latencies)
    p99_latency = latencies_sorted[int(len(latencies) * 0.99)]
    
    assert max_latency < 200.0, f"Max latency {max_latency:.2f}ms exceeds 200ms"
    assert mean_latency < 100.0, f"Mean latency {mean_latency:.2f}ms should be well below 200ms"
    assert p99_latency < 200.0, f"99th percentile {p99_latency:.2f}ms exceeds 200ms"
    
    print(f"\nLatency Statistics (n=200):")
    print(f"  Max: {max_latency:.2f}ms")
    print(f"  Mean: {mean_latency:.2f}ms")
    print(f"  P99: {p99_latency:.2f}ms")
