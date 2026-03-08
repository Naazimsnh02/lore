"""Property test: Illustration Style Consistency (Property 10).

Feature: lore-multimodal-documentary-app, Property 10: Illustration Style Consistency
Validates: Requirements 7.6

For any documentary stream session, all illustrations generated within that
session shall maintain consistent visual style.

Strategy: Generate multiple illustrations per session with varying concepts
and verify all share the same VisualStyle.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.services.nano_illustrator.illustrator import NanoIllustrator
from backend.services.nano_illustrator.models import (
    ConceptDescription,
    DepthLevel,
    DocumentaryContext,
    VisualStyle,
)


# ── Strategies ────────────────────────────────────────────────────────────────

# Generate a list of 2–5 prompts per session
prompt_list_strategy = st.lists(
    st.text(min_size=5, max_size=100, alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z"))),
    min_size=2,
    max_size=5,
)

# Random place types for the first concept (determines session style)
place_types_strategy = st.lists(
    st.sampled_from([
        "museum", "castle", "park", "university", "zoo",
        "church", "temple", "restaurant", "cafe", "library",
    ]),
    min_size=0,
    max_size=3,
)

session_id_strategy = st.text(
    min_size=5, max_size=10,
    alphabet=st.characters(whitelist_categories=("L", "N")),
).map(lambda s: f"sess_{s}")


def _make_mock_client() -> MagicMock:
    """Mock client returning a valid image response."""
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


# ── Property 10: Illustration Style Consistency ──────────────────────────────


@given(
    prompts=prompt_list_strategy,
    place_types=place_types_strategy,
    session_id=session_id_strategy,
)
@settings(max_examples=100, deadline=10000, suppress_health_check=[HealthCheck.function_scoped_fixture])
@pytest.mark.asyncio
async def test_all_illustrations_in_session_have_consistent_style(
    prompts: list[str],
    place_types: list[str],
    session_id: str,
):
    """Property 10: All illustrations in a session SHALL maintain consistent style.

    Feature: lore-multimodal-documentary-app, Property 10: Illustration Style Consistency
    """
    client = _make_mock_client()
    illustrator = NanoIllustrator(client=client)

    # First concept includes a documentary context to determine style
    first_context = DocumentaryContext(
        session_id=session_id,
        place_types=place_types,
    )

    results = []
    for i, prompt in enumerate(prompts):
        concept = ConceptDescription(
            prompt=prompt,
            # Only the first concept provides context; subsequent ones may vary
            context=first_context if i == 0 else DocumentaryContext(
                session_id=session_id,
                place_types=["different_type"],  # Should not change cached style
            ),
        )
        result = await illustrator.generate_illustration(
            concept, session_id=session_id
        )
        results.append(result)

    # All successful illustrations must share the same style
    styles = [r.illustration.style for r in results if r.error is None]
    assert len(set(styles)) <= 1, (
        f"Inconsistent styles in session {session_id}: {styles}"
    )


@pytest.mark.asyncio
async def test_style_consistency_across_many_requests():
    """Deterministic test: 10 requests in one session all get the same style."""
    client = _make_mock_client()
    illustrator = NanoIllustrator(client=client)

    context = DocumentaryContext(session_id="det_sess", place_types=["castle"])
    styles = []

    for i in range(10):
        concept = ConceptDescription(
            prompt=f"Illustration {i}",
            context=context if i == 0 else DocumentaryContext(
                session_id="det_sess", place_types=["park"]
            ),
        )
        result = await illustrator.generate_illustration(concept, session_id="det_sess")
        styles.append(result.illustration.style)

    assert all(s == VisualStyle.HISTORICAL for s in styles)
