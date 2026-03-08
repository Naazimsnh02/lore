"""Unit tests for the Nano Illustrator service.

Tests cover:
- Illustration generation (success, timeout, error paths)
- Style determination and consistency (Req 7.4, 7.6)
- Prompt building with style directives
- Batch generation
- Media Store integration (Req 7.5)
- Graceful degradation on failure
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.nano_illustrator.illustrator import (
    NanoIllustrator,
    _MODEL_ID,
    _STYLE_PROMPTS,
    _PLACE_TYPE_STYLE_MAP,
)
from backend.services.nano_illustrator.models import (
    ConceptDescription,
    DepthLevel,
    DocumentaryContext,
    Illustration,
    IllustrationGenerationError,
    IllustrationResult,
    IllustrationTimeoutError,
    VisualStyle,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_mock_client(image_bytes: bytes = b"\x89PNG_FAKE_IMAGE_DATA") -> MagicMock:
    """Create a mock google.genai.Client that returns image data."""
    client = MagicMock()

    # Build a response that looks like a real Gemini response
    inline_data = MagicMock()
    inline_data.data = image_bytes
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


def _make_failing_client(exc: Exception) -> MagicMock:
    """Create a mock client that raises an exception."""
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(side_effect=exc)
    return client


def _make_empty_response_client() -> MagicMock:
    """Create a mock client that returns a response with no image."""
    client = MagicMock()
    response = MagicMock()
    response.candidates = []
    client.aio.models.generate_content = AsyncMock(return_value=response)
    return client


def _make_concept(
    prompt: str = "A panoramic view of the Roman Colosseum",
    session_id: str = "sess_123",
    **kwargs: Any,
) -> ConceptDescription:
    """Create a ConceptDescription with sensible defaults."""
    return ConceptDescription(prompt=prompt, **kwargs)


def _make_context(
    session_id: str = "sess_123",
    place_types: list[str] | None = None,
    historical_period: str | None = None,
    previous_styles: list[VisualStyle] | None = None,
) -> DocumentaryContext:
    return DocumentaryContext(
        session_id=session_id,
        place_types=place_types or [],
        historical_period=historical_period,
        previous_styles=previous_styles or [],
    )


# ── Test: Successful generation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_illustration_success():
    """Req 7.1: Generate illustration using Gemini 3.1 Flash Image Preview."""
    client = _make_mock_client(b"\x89PNG_DATA")
    illustrator = NanoIllustrator(client=client)
    concept = _make_concept()

    result = await illustrator.generate_illustration(concept, user_id="u1", session_id="s1")

    assert result.error is None
    assert result.illustration.image_data == b"\x89PNG_DATA"
    assert result.illustration.mime_type == "image/png"
    assert result.illustration.resolution == "1024x1024"
    assert result.illustration.generation_time_ms > 0
    client.aio.models.generate_content.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_illustration_uses_correct_model():
    """Req 7.1: Uses gemini-3.1-flash-image-preview model."""
    client = _make_mock_client()
    illustrator = NanoIllustrator(client=client)
    concept = _make_concept()

    await illustrator.generate_illustration(concept)

    call_kwargs = client.aio.models.generate_content.call_args
    assert call_kwargs.kwargs["model"] == _MODEL_ID


@pytest.mark.asyncio
async def test_generate_illustration_caption_set():
    """Caption should be set from the concept prompt."""
    client = _make_mock_client()
    illustrator = NanoIllustrator(client=client)
    concept = _make_concept(prompt="The Eiffel Tower at sunset")

    result = await illustrator.generate_illustration(concept)

    assert result.illustration.caption == "The Eiffel Tower at sunset"


# ── Test: Timeout handling ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_illustration_timeout_fallback():
    """Req 7.2: Graceful fallback on timeout."""
    client = MagicMock()

    async def slow_generate(**kwargs):
        await asyncio.sleep(10)

    client.aio.models.generate_content = AsyncMock(side_effect=slow_generate)
    illustrator = NanoIllustrator(client=client)
    concept = _make_concept()

    # Patch the timeout to be very short for testing
    with patch("backend.services.nano_illustrator.illustrator._GENERATION_TIMEOUT_S", 0.01):
        result = await illustrator.generate_illustration(concept)

    assert result.error is not None
    assert "timed out" in result.error.lower()
    assert result.illustration.image_data is None
    assert result.stored is False


# ── Test: API error handling ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_illustration_api_error_fallback():
    """Graceful degradation on API failure."""
    client = _make_failing_client(RuntimeError("API quota exceeded"))
    illustrator = NanoIllustrator(client=client)
    concept = _make_concept()

    result = await illustrator.generate_illustration(concept)

    assert result.error is not None
    assert "API quota exceeded" in result.error
    assert result.illustration.image_data is None


@pytest.mark.asyncio
async def test_generate_illustration_empty_response():
    """Graceful fallback when model returns no image."""
    client = _make_empty_response_client()
    illustrator = NanoIllustrator(client=client)
    concept = _make_concept()

    result = await illustrator.generate_illustration(concept)

    assert result.error is not None
    assert "no image data" in result.error.lower()


# ── Test: Style determination ─────────────────────────────────────────────────


def test_determine_style_historical_period():
    """Req 7.4: Historical period → HISTORICAL style."""
    illustrator = NanoIllustrator(client=MagicMock())
    context = _make_context(historical_period="Ancient Rome")

    style = illustrator.determine_style(context)

    assert style == VisualStyle.HISTORICAL


def test_determine_style_museum_place_type():
    """Place type 'museum' → ARTISTIC style."""
    illustrator = NanoIllustrator(client=MagicMock())
    context = _make_context(place_types=["museum"])

    style = illustrator.determine_style(context)

    assert style == VisualStyle.ARTISTIC


def test_determine_style_park_place_type():
    """Place type 'park' → PHOTOREALISTIC style."""
    illustrator = NanoIllustrator(client=MagicMock())
    context = _make_context(place_types=["park"])

    style = illustrator.determine_style(context)

    assert style == VisualStyle.PHOTOREALISTIC


def test_determine_style_castle_place_type():
    """Place type 'castle' → HISTORICAL style."""
    illustrator = NanoIllustrator(client=MagicMock())
    context = _make_context(place_types=["castle"])

    style = illustrator.determine_style(context)

    assert style == VisualStyle.HISTORICAL


def test_determine_style_university():
    """Place type 'university' → TECHNICAL style."""
    illustrator = NanoIllustrator(client=MagicMock())
    context = _make_context(place_types=["university"])

    style = illustrator.determine_style(context)

    assert style == VisualStyle.TECHNICAL


def test_determine_style_default_illustrated():
    """Default style when no signals → ILLUSTRATED."""
    illustrator = NanoIllustrator(client=MagicMock())
    context = _make_context(place_types=["restaurant"])

    style = illustrator.determine_style(context)

    assert style == VisualStyle.ILLUSTRATED


def test_determine_style_majority_vote():
    """Previous styles majority vote when no other signals match."""
    illustrator = NanoIllustrator(client=MagicMock())
    context = _make_context(
        previous_styles=[
            VisualStyle.PHOTOREALISTIC,
            VisualStyle.PHOTOREALISTIC,
            VisualStyle.ARTISTIC,
        ]
    )

    style = illustrator.determine_style(context)

    assert style == VisualStyle.PHOTOREALISTIC


# ── Test: Style consistency (Req 7.6) ────────────────────────────────────────


def test_style_consistency_within_session():
    """Req 7.6: Same session → same style across requests."""
    illustrator = NanoIllustrator(client=MagicMock())

    # First call determines style
    ctx1 = _make_context(session_id="s1", place_types=["museum"])
    style1 = illustrator.determine_style(ctx1)

    # Second call with different place types should still return cached style
    ctx2 = _make_context(session_id="s1", place_types=["park"])
    style2 = illustrator.determine_style(ctx2)

    assert style1 == style2 == VisualStyle.ARTISTIC


def test_different_sessions_different_styles():
    """Different sessions can have different styles."""
    illustrator = NanoIllustrator(client=MagicMock())

    style1 = illustrator.determine_style(_make_context(session_id="s1", place_types=["museum"]))
    style2 = illustrator.determine_style(_make_context(session_id="s2", place_types=["park"]))

    assert style1 == VisualStyle.ARTISTIC
    assert style2 == VisualStyle.PHOTOREALISTIC


def test_maintain_style_consistency():
    """maintain_style_consistency returns the cached style."""
    illustrator = NanoIllustrator(client=MagicMock())

    # No style yet
    assert illustrator.maintain_style_consistency("s1") is None

    # After determining
    illustrator.determine_style(_make_context(session_id="s1", place_types=["castle"]))
    assert illustrator.maintain_style_consistency("s1") == VisualStyle.HISTORICAL


def test_clear_session_style():
    """clear_session_style removes the cached style."""
    illustrator = NanoIllustrator(client=MagicMock())
    illustrator.determine_style(_make_context(session_id="s1", place_types=["castle"]))

    illustrator.clear_session_style("s1")

    assert illustrator.maintain_style_consistency("s1") is None


@pytest.mark.asyncio
async def test_generate_illustration_caches_style_for_session():
    """Req 7.6: Generating an illustration caches the style for the session."""
    client = _make_mock_client()
    illustrator = NanoIllustrator(client=client)
    concept = _make_concept(historical_period="Medieval")

    await illustrator.generate_illustration(concept, session_id="s1")

    assert illustrator.maintain_style_consistency("s1") == VisualStyle.HISTORICAL


# ── Test: Style override ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_style_override():
    """Explicit style_override takes priority."""
    client = _make_mock_client()
    illustrator = NanoIllustrator(client=client)
    concept = _make_concept(style_override=VisualStyle.TECHNICAL)

    result = await illustrator.generate_illustration(concept)

    assert result.illustration.style == VisualStyle.TECHNICAL


# ── Test: Prompt building ─────────────────────────────────────────────────────


def test_build_prompt_includes_style():
    """Prompt includes style description."""
    illustrator = NanoIllustrator(client=MagicMock())
    concept = _make_concept(prompt="A view of Paris")

    prompt = illustrator._build_prompt(concept, VisualStyle.PHOTOREALISTIC)

    assert "photorealistic" in prompt.lower()
    assert "A view of Paris" in prompt


def test_build_prompt_includes_historical_period():
    """Req 7.4: Historical period injected into prompt."""
    illustrator = NanoIllustrator(client=MagicMock())
    concept = _make_concept(prompt="Forum", historical_period="Ancient Rome")

    prompt = illustrator._build_prompt(concept, VisualStyle.HISTORICAL)

    assert "Ancient Rome" in prompt
    assert "period-appropriate" in prompt


def test_build_prompt_expert_depth():
    """Expert depth adds detailed annotation directive."""
    illustrator = NanoIllustrator(client=MagicMock())
    concept = _make_concept(prompt="DNA structure", complexity=DepthLevel.EXPERT)

    prompt = illustrator._build_prompt(concept, VisualStyle.TECHNICAL)

    assert "detailed annotations" in prompt.lower()


def test_build_prompt_scholar_depth():
    """Scholar depth adds balance directive."""
    illustrator = NanoIllustrator(client=MagicMock())
    concept = _make_concept(prompt="Cell structure", complexity=DepthLevel.SCHOLAR)

    prompt = illustrator._build_prompt(concept, VisualStyle.TECHNICAL)

    assert "balance" in prompt.lower()


def test_build_prompt_no_watermark():
    """Prompt instructs no text overlays or watermarks."""
    illustrator = NanoIllustrator(client=MagicMock())
    concept = _make_concept()

    prompt = illustrator._build_prompt(concept, VisualStyle.ILLUSTRATED)

    assert "no text overlays" in prompt.lower()


# ── Test: Batch generation ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_batch():
    """Batch generation returns one result per concept."""
    client = _make_mock_client()
    illustrator = NanoIllustrator(client=client)
    concepts = [
        _make_concept(prompt="Concept A"),
        _make_concept(prompt="Concept B"),
        _make_concept(prompt="Concept C"),
    ]

    results = await illustrator.generate_batch(concepts, session_id="s1")

    assert len(results) == 3
    assert all(r.error is None for r in results)
    assert all(r.illustration.image_data is not None for r in results)


@pytest.mark.asyncio
async def test_generate_batch_empty():
    """Batch with no concepts returns empty list."""
    client = _make_mock_client()
    illustrator = NanoIllustrator(client=client)

    results = await illustrator.generate_batch([], session_id="s1")

    assert results == []


# ── Test: Media Store integration ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_in_media_store():
    """Req 7.5: Illustration stored in Media Store when available."""
    client = _make_mock_client()
    media_store = MagicMock()
    media_store.store_media = AsyncMock(return_value="https://storage.example.com/signed-url")

    illustrator = NanoIllustrator(client=client, media_store=media_store)
    concept = _make_concept()

    result = await illustrator.generate_illustration(
        concept, user_id="u1", session_id="s1"
    )

    assert result.stored is True
    assert result.media_id is not None
    assert result.media_url == "https://storage.example.com/signed-url"
    assert result.illustration.url == "https://storage.example.com/signed-url"
    media_store.store_media.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_store_without_media_store():
    """No storage attempt if media_store is None."""
    client = _make_mock_client()
    illustrator = NanoIllustrator(client=client, media_store=None)
    concept = _make_concept()

    result = await illustrator.generate_illustration(
        concept, user_id="u1", session_id="s1"
    )

    assert result.stored is False
    assert result.media_id is None


@pytest.mark.asyncio
async def test_no_store_without_user_id():
    """No storage attempt if user_id is missing."""
    client = _make_mock_client()
    media_store = MagicMock()
    media_store.store_media = AsyncMock(return_value="url")

    illustrator = NanoIllustrator(client=client, media_store=media_store)
    concept = _make_concept()

    result = await illustrator.generate_illustration(concept, session_id="s1")

    assert result.stored is False
    media_store.store_media.assert_not_awaited()


@pytest.mark.asyncio
async def test_store_failure_does_not_fail_generation():
    """Media Store failure should not break the illustration result."""
    client = _make_mock_client()
    media_store = MagicMock()
    media_store.store_media = AsyncMock(side_effect=RuntimeError("GCS error"))

    illustrator = NanoIllustrator(client=client, media_store=media_store)
    concept = _make_concept()

    result = await illustrator.generate_illustration(
        concept, user_id="u1", session_id="s1"
    )

    # Generation succeeded even though storage failed
    assert result.illustration.image_data is not None
    assert result.stored is False
    assert result.error is None  # error field is for generation errors only


# ── Test: Session ID resolution from context ──────────────────────────────────


@pytest.mark.asyncio
async def test_session_id_from_context():
    """Session ID resolved from concept.context if not explicitly provided."""
    client = _make_mock_client()
    illustrator = NanoIllustrator(client=client)
    concept = _make_concept(
        context=_make_context(session_id="ctx_session")
    )

    await illustrator.generate_illustration(concept, user_id="u1")

    assert illustrator.maintain_style_consistency("ctx_session") is not None


# ── Test: Model data validation ───────────────────────────────────────────────


def test_concept_description_required_prompt():
    """ConceptDescription requires a prompt."""
    with pytest.raises(Exception):
        ConceptDescription()


def test_concept_description_defaults():
    """ConceptDescription has sensible defaults."""
    c = ConceptDescription(prompt="test")
    assert c.complexity == DepthLevel.EXPLORER
    assert c.aspect_ratio == "1:1"
    assert c.style_override is None
    assert c.historical_period is None


def test_illustration_defaults():
    """Illustration model defaults."""
    i = Illustration()
    assert i.mime_type == "image/png"
    assert i.resolution == "1024x1024"
    assert i.style == VisualStyle.ILLUSTRATED
    assert i.id  # auto-generated


def test_visual_style_enum_values():
    """All expected style values exist."""
    assert VisualStyle.PHOTOREALISTIC.value == "photorealistic"
    assert VisualStyle.ILLUSTRATED.value == "illustrated"
    assert VisualStyle.HISTORICAL.value == "historical"
    assert VisualStyle.TECHNICAL.value == "technical"
    assert VisualStyle.ARTISTIC.value == "artistic"


def test_illustration_result_error_field():
    """IllustrationResult can carry an error."""
    r = IllustrationResult(
        illustration=Illustration(),
        error="Something went wrong",
    )
    assert r.error == "Something went wrong"
    assert r.stored is False


# ── Test: Resolve style from concept ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_style_historical_period_on_concept():
    """Historical period on concept → HISTORICAL."""
    client = _make_mock_client()
    illustrator = NanoIllustrator(client=client)
    concept = _make_concept(historical_period="Victorian Era")

    result = await illustrator.generate_illustration(concept)

    assert result.illustration.style == VisualStyle.HISTORICAL


@pytest.mark.asyncio
async def test_resolve_style_from_context_place_types():
    """Style resolved from context place_types."""
    client = _make_mock_client()
    illustrator = NanoIllustrator(client=client)
    concept = _make_concept(
        context=_make_context(session_id="s_new", place_types=["zoo"])
    )

    result = await illustrator.generate_illustration(concept)

    assert result.illustration.style == VisualStyle.ILLUSTRATED


@pytest.mark.asyncio
async def test_resolve_style_default_without_context():
    """Default ILLUSTRATED when no context or overrides."""
    client = _make_mock_client()
    illustrator = NanoIllustrator(client=client)
    concept = _make_concept()

    result = await illustrator.generate_illustration(concept)

    assert result.illustration.style == VisualStyle.ILLUSTRATED
