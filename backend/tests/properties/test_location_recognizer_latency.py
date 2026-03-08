"""Property-based test for camera frame processing latency.

Feature: lore-multimodal-documentary-app
Property 2: Camera Frame Processing Latency
Validates: Requirements 2.2

Tests that location identification completes within 3 seconds for any
camera frame input.  The Gemini and Places API clients are replaced with
in-process synchronous stubs so the test measures only Python overhead, not
real network latency (network latency is validated in integration tests).

Run 10+ iterations (Hypothesis default) with diverse random inputs.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.services.location_recognizer.recognizer import (
    RECOGNITION_TIMEOUT_SECONDS,
    LocationRecognizer,
)

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Generate random JPEG-like byte strings (at least 4 bytes for JPEG header)
_image_bytes_st = st.binary(min_size=4, max_size=2048)

# Random Gemini response payloads (valid and edge-case)
_gemini_payload_st = st.fixed_dictionaries(
    {
        "description": st.text(min_size=0, max_size=120),
        "landmark_name": st.one_of(st.none(), st.text(min_size=1, max_size=80)),
        "architectural_style": st.one_of(st.none(), st.text(max_size=40)),
        "text_detected": st.lists(st.text(max_size=30), max_size=5),
        "location_hint": st.one_of(st.none(), st.text(max_size=60)),
        "confidence": st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    }
)

# Random Places API response (0 or 1 place)
_places_payload_st = st.one_of(
    st.just({"places": []}),
    st.fixed_dictionaries(
        {
            "places": st.lists(
                st.fixed_dictionaries(
                    {
                        "id": st.text(min_size=1, max_size=40),
                        "displayName": st.fixed_dictionaries(
                            {
                                "text": st.text(min_size=1, max_size=80),
                                "languageCode": st.just("en"),
                            }
                        ),
                        "location": st.fixed_dictionaries(
                            {
                                "latitude": st.floats(
                                    min_value=-90.0,
                                    max_value=90.0,
                                    allow_nan=False,
                                    allow_infinity=False,
                                ),
                                "longitude": st.floats(
                                    min_value=-180.0,
                                    max_value=180.0,
                                    allow_nan=False,
                                    allow_infinity=False,
                                ),
                            }
                        ),
                        "types": st.lists(st.text(max_size=30), max_size=4),
                        "editorialSummary": st.fixed_dictionaries(
                            {"text": st.text(max_size=200)}
                        ),
                        "formattedAddress": st.text(max_size=100),
                        "photos": st.just([]),
                    }
                ),
                min_size=1,
                max_size=1,
            )
        }
    ),
)

# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@settings(max_examples=10, deadline=5000)  # 5 s Hypothesis deadline per example
@given(
    frame_bytes=_image_bytes_st,
    gemini_payload=_gemini_payload_st,
    places_payload=_places_payload_st,
)
def test_recognition_latency_under_3_seconds(
    frame_bytes: bytes,
    gemini_payload: dict[str, Any],
    places_payload: dict[str, Any],
) -> None:
    """Property 2: Location identification SHALL complete within 3 seconds.

    For every possible camera frame (represented by random byte strings) and
    every possible API response shape, the processing_time returned by
    recognize_location must be strictly less than RECOGNITION_TIMEOUT_SECONDS.
    """
    asyncio.run(
        _run_recognition(frame_bytes, gemini_payload, places_payload)
    )


async def _run_recognition(
    frame_bytes: bytes,
    gemini_payload: dict[str, Any],
    places_payload: dict[str, Any],
) -> None:
    """Inner coroutine so we can use asyncio.run() in the sync Hypothesis test."""
    import sys
    from unittest.mock import patch

    # Stub Gemini client
    gemini_client = MagicMock()
    gemini_response = MagicMock()
    gemini_response.text = json.dumps(gemini_payload)
    gemini_client.models.generate_content.return_value = gemini_response

    # Stub aiohttp session for Places API
    http_response = AsyncMock()
    http_response.status = 200
    http_response.json = AsyncMock(return_value=places_payload)
    http_response.__aenter__ = AsyncMock(return_value=http_response)
    http_response.__aexit__ = AsyncMock(return_value=False)

    http_session = MagicMock()
    http_session.post = MagicMock(return_value=http_response)

    # Minimal mock for google.genai.types
    genai_types = MagicMock()
    genai_types.Content = MagicMock(side_effect=lambda **kw: kw)
    genai_types.Part = MagicMock(side_effect=lambda **kw: kw)
    genai_types.Blob = MagicMock(side_effect=lambda **kw: kw)
    genai_types.GenerateContentConfig = MagicMock(side_effect=lambda **kw: kw)

    with patch.dict(
        sys.modules,
        {
            "google.genai": MagicMock(),
            "google.genai.types": genai_types,
        },
    ):
        recognizer = LocationRecognizer(
            gemini_client=gemini_client,
            places_api_key="test-key",
            http_session=http_session,
            timeout=RECOGNITION_TIMEOUT_SECONDS,
        )
        start = time.monotonic()
        result = await recognizer.recognize_location(frame_bytes)
        wall_clock = time.monotonic() - start

    # Property assertion: processing_time must be within the timeout window
    assert result.processing_time < RECOGNITION_TIMEOUT_SECONDS, (
        f"processing_time {result.processing_time:.3f}s exceeded "
        f"timeout {RECOGNITION_TIMEOUT_SECONDS}s"
    )

    # Wall-clock sanity check (test overhead should be negligible with stubs)
    assert wall_clock < RECOGNITION_TIMEOUT_SECONDS + 0.5, (
        f"Wall-clock time {wall_clock:.3f}s far exceeded timeout"
    )

    # Result must always be a valid LocationResult (no unhandled exceptions)
    assert result is not None
    assert isinstance(result.recognized, bool)
    assert 0.0 <= result.confidence <= 1.0
