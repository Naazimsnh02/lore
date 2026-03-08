"""Property test for frame rate maintenance (Property 25).

Feature: lore-multimodal-documentary-app, Property 25: Frame Rate Maintenance

Validates: Requirement 2.1
  "For any SightMode or LoreMode session, the system shall capture and process
  camera frames at a rate of at least 1 frame per second when camera is active."

This test verifies that the SightModeHandler can process frames at ≥ 1 fps
by generating random frame data and measuring processing throughput.
"""

from __future__ import annotations

import asyncio
import base64
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.services.location_recognizer.models import LocationResult
from backend.services.sight_mode.handler import SightModeHandler
from backend.services.sight_mode.models import SightModeEvent


def _mock_recognizer_fast() -> MagicMock:
    """Create a recognizer mock that returns instantly (unrecognised)."""
    recognizer = MagicMock()
    recognizer.recognize_location = AsyncMock(
        return_value=LocationResult(
            recognized=False,
            confidence=0.1,
            processing_time=0.01,
        )
    )
    return recognizer


# Strategy: generate random "bright" frame bytes (values ≥ 128 so lighting check passes)
bright_frame_bytes = st.binary(min_size=100, max_size=10_000).map(
    lambda b: bytes(max(v, 128) for v in b)
)


@settings(
    max_examples=100,
    deadline=5000,  # 5 s per example is very generous
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(frame_data=bright_frame_bytes)
@pytest.mark.asyncio
async def test_frame_processing_rate_at_least_1fps(frame_data: bytes):
    """Property 25: Frame processing must complete in < 1 second per frame.

    If processing a single frame takes < 1 s, then the system can maintain
    ≥ 1 fps (given frames arrive at 1 fps from the client).
    """
    recognizer = _mock_recognizer_fast()
    handler = SightModeHandler(location_recognizer=recognizer)
    frame_b64 = base64.b64encode(frame_data).decode()

    start = time.monotonic()
    response = await handler.process_frame(frame_b64)
    elapsed = time.monotonic() - start

    # Processing must complete within 1 second to sustain ≥ 1 fps
    assert elapsed < 1.0, (
        f"Frame processing took {elapsed:.3f}s — exceeds 1 fps budget"
    )

    # The response should be valid (not an error)
    assert response.event in {
        SightModeEvent.FRAME_BUFFERED,
        SightModeEvent.DOCUMENTARY_TRIGGER,
        SightModeEvent.FLASH_SUGGESTION,
        SightModeEvent.VOICE_CLARIFICATION,
        SightModeEvent.RECOGNITION_FAILED,
    }


@settings(
    max_examples=10,
    deadline=30000,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(num_frames=st.integers(min_value=5, max_value=20))
@pytest.mark.asyncio
async def test_burst_frame_processing_maintains_rate(num_frames: int):
    """Verify that processing a burst of frames maintains ≥ 1 fps average."""
    recognizer = _mock_recognizer_fast()
    handler = SightModeHandler(location_recognizer=recognizer)
    frame_data = bytes([180] * 5000)  # bright frame
    frame_b64 = base64.b64encode(frame_data).decode()

    start = time.monotonic()
    for _ in range(num_frames):
        await handler.process_frame(frame_b64)
    elapsed = time.monotonic() - start

    # Average per-frame time should be < 1 second
    avg_time = elapsed / num_frames
    assert avg_time < 1.0, (
        f"Average frame processing: {avg_time:.3f}s across {num_frames} frames"
    )
