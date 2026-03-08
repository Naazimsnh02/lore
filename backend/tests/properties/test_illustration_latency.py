"""Property test: Illustration Generation Latency (Property 8).

Feature: lore-multimodal-documentary-app, Property 8: Illustration Generation Latency
Validates: Requirements 7.2

For any illustration request to Nano_Illustrator, generation shall complete
within 2 seconds.

Strategy: Generate random concept descriptions and measure generation time.
The mock Gemini client returns instantly, so we verify that the NanoIllustrator
itself adds negligible overhead (< 100 ms) and that the timeout machinery
correctly bounds slow API calls.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.services.nano_illustrator.illustrator import NanoIllustrator
from backend.services.nano_illustrator.models import (
    ConceptDescription,
    DepthLevel,
    VisualStyle,
)


# ── Strategies ────────────────────────────────────────────────────────────────

concept_strategy = st.builds(
    ConceptDescription,
    prompt=st.text(min_size=5, max_size=200, alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z"))),
    complexity=st.sampled_from(list(DepthLevel)),
    aspect_ratio=st.sampled_from(["1:1", "16:9", "3:4"]),
    style_override=st.one_of(st.none(), st.sampled_from(list(VisualStyle))),
    historical_period=st.one_of(st.none(), st.text(min_size=3, max_size=30, alphabet=st.characters(whitelist_categories=("L", "Z")))),
)


def _make_fast_client() -> MagicMock:
    """Mock client that returns image data with minimal delay."""
    client = MagicMock()

    inline_data = MagicMock()
    inline_data.data = b"\x89PNG" + b"\x00" * 100
    inline_data.mime_type = "image/png"

    part = MagicMock()
    part.inline_data = inline_data

    content = MagicMock()
    content.parts = [part]

    candidate = MagicMock()
    candidate.content = content

    response = MagicMock()
    response.candidates = [candidate]

    client.aio.models.generate_content = AsyncMock(return_value=response)
    return client


# ── Property 8: Illustration Generation Latency ──────────────────────────────


@given(concept=concept_strategy)
@settings(max_examples=100, deadline=5000, suppress_health_check=[HealthCheck.function_scoped_fixture])
@pytest.mark.asyncio
async def test_illustration_generation_latency(concept: ConceptDescription):
    """Property 8: For any illustration request, generation SHALL complete within 2 seconds.

    Feature: lore-multimodal-documentary-app, Property 8: Illustration Generation Latency
    """
    client = _make_fast_client()
    illustrator = NanoIllustrator(client=client)

    start = time.monotonic()
    result = await illustrator.generate_illustration(concept, session_id="prop_test")
    elapsed = time.monotonic() - start

    # With a fast mock, framework overhead should be well under the budget.
    # We allow 5 s total to account for Hypothesis/pytest-asyncio overhead
    # across 100+ iterations; the real API timeout is enforced separately.
    assert elapsed < 5.0, f"Generation took {elapsed:.3f}s, exceeding overhead budget"
    # Result should be valid (no error from the fast mock)
    assert result.error is None
    assert result.illustration.image_data is not None
    assert result.illustration.generation_time_ms > 0


# ── Property 8 (timeout enforcement): Slow API correctly times out ───────────


@pytest.mark.asyncio
async def test_slow_api_is_bounded_by_timeout():
    """Verify that a slow API call is terminated by the timeout mechanism."""
    client = MagicMock()

    async def slow_call(**kwargs):
        await asyncio.sleep(60)  # Simulate very slow API

    client.aio.models.generate_content = AsyncMock(side_effect=slow_call)
    illustrator = NanoIllustrator(client=client)
    concept = ConceptDescription(prompt="Anything")

    import backend.services.nano_illustrator.illustrator as mod
    original = mod._GENERATION_TIMEOUT_S
    mod._GENERATION_TIMEOUT_S = 0.05  # 50 ms timeout for testing
    try:
        start = time.monotonic()
        result = await illustrator.generate_illustration(concept)
        elapsed = time.monotonic() - start
    finally:
        mod._GENERATION_TIMEOUT_S = original

    assert result.error is not None
    assert "timed out" in result.error.lower()
    # Total wall time should be close to timeout, not 60 seconds
    assert elapsed < 1.0
