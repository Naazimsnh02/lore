"""Property test: Fact Verification Completeness (Property 11).

Feature: lore-multimodal-documentary-app, Property 11: Fact Verification Completeness
Validates: Requirements 8.1, 8.2

For any factual claim presented in a documentary stream, either the
Search_Grounder has verified the claim with source citations, or the claim
is explicitly marked as unverified.

Strategy: Generate random factual claims and verify that every result
either (a) has verified=True with non-empty sources, or (b) has
verified=False with an explicit unverified/error status.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.services.search_grounder.grounder import SearchGrounder
from backend.services.search_grounder.models import (
    ClaimImportance,
    DocumentaryContext,
    FactualClaim,
    VerificationStatus,
)


# ── Strategies ────────────────────────────────────────────────────────────────

# Printable text for claims (avoid control chars that would break prompts)
_printable = st.characters(whitelist_categories=("L", "N", "P", "Z"))

claim_text_strategy = st.text(min_size=5, max_size=300, alphabet=_printable)
importance_strategy = st.sampled_from(list(ClaimImportance))

context_strategy = st.one_of(
    st.none(),
    st.builds(
        DocumentaryContext,
        location_name=st.one_of(st.none(), st.text(min_size=1, max_size=50, alphabet=_printable)),
        topic=st.one_of(st.none(), st.text(min_size=1, max_size=50, alphabet=_printable)),
        historical_period=st.one_of(st.none(), st.text(min_size=1, max_size=50, alphabet=_printable)),
        mode=st.one_of(st.none(), st.sampled_from(["sight", "voice", "lore"])),
    ),
)

claim_strategy = st.builds(
    FactualClaim,
    text=claim_text_strategy,
    importance=importance_strategy,
    context=context_strategy,
)


# ── Mock helpers ──────────────────────────────────────────────────────────────


def _make_grounding_chunk(uri: str, title: str) -> MagicMock:
    chunk = MagicMock()
    web = MagicMock()
    web.uri = uri
    web.title = title
    chunk.web = web
    return chunk


def _make_verified_client() -> MagicMock:
    """Client that returns a grounded, verified response."""
    client = MagicMock()

    grounding_metadata = MagicMock()
    grounding_metadata.grounding_chunks = [
        _make_grounding_chunk("https://en.wikipedia.org/wiki/Test", "Test - Wikipedia"),
        _make_grounding_chunk("https://www.britannica.com/topic/test", "Test | Britannica"),
    ]
    support = MagicMock()
    segment = MagicMock()
    segment.text = "The claim is supported by evidence."
    support.segment = segment
    support.grounding_chunk_indices = [0, 1]
    grounding_metadata.grounding_supports = [support]
    grounding_metadata.web_search_queries = ["test"]

    candidate = MagicMock()
    candidate.grounding_metadata = grounding_metadata

    response = MagicMock()
    response.candidates = [candidate]
    response.text = "The claim is verified and accurate."

    client.aio.models.generate_content = AsyncMock(return_value=response)
    return client


def _make_unverified_client() -> MagicMock:
    """Client that returns no grounding metadata (unverifiable)."""
    client = MagicMock()
    candidate = MagicMock()
    candidate.grounding_metadata = None
    response = MagicMock()
    response.candidates = [candidate]
    response.text = "Unable to verify."
    client.aio.models.generate_content = AsyncMock(return_value=response)
    return client


def _make_error_client() -> MagicMock:
    """Client that raises an error."""
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(
        side_effect=RuntimeError("API error")
    )
    return client


# ── Property Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    deadline=None,
)
@given(claim=claim_strategy)
async def test_property_11_verified_claims_have_sources(claim: FactualClaim) -> None:
    """Property 11: If a claim is verified, it MUST have source citations.

    Feature: lore-multimodal-documentary-app
    Property 11: Fact Verification Completeness
    Validates: Requirements 8.1, 8.2
    """
    client = _make_verified_client()
    grounder = SearchGrounder(client)

    result = await grounder.verify_fact(claim)

    if result.verified:
        # Req 8.2: verified facts must have sources
        assert len(result.sources) > 0, (
            f"Verified claim has no sources: {claim.text!r}"
        )
        assert result.status in (
            VerificationStatus.VERIFIED,
            VerificationStatus.CONFLICTING,
        )
        assert result.confidence > 0


@pytest.mark.asyncio
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    deadline=None,
)
@given(claim=claim_strategy)
async def test_property_11_unverified_claims_are_marked(claim: FactualClaim) -> None:
    """Property 11: If a claim cannot be verified, it MUST be marked as unverified.

    Feature: lore-multimodal-documentary-app
    Property 11: Fact Verification Completeness
    Validates: Requirements 8.1, 8.3
    """
    client = _make_unverified_client()
    grounder = SearchGrounder(client)

    result = await grounder.verify_fact(claim)

    # Req 8.3: unverifiable claims must be explicitly marked
    assert result.verified is False, (
        f"Claim without grounding should not be verified: {claim.text!r}"
    )
    assert result.status in (
        VerificationStatus.UNVERIFIED,
        VerificationStatus.ERROR,
    )


@pytest.mark.asyncio
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    deadline=None,
)
@given(claim=claim_strategy)
async def test_property_11_errors_are_marked_unverified(claim: FactualClaim) -> None:
    """Property 11: API errors MUST result in unverified status with error detail.

    Feature: lore-multimodal-documentary-app
    Property 11: Fact Verification Completeness
    Validates: Requirements 8.3
    """
    client = _make_error_client()
    grounder = SearchGrounder(client)

    result = await grounder.verify_fact(claim)

    assert result.verified is False
    assert result.status == VerificationStatus.ERROR
    assert result.error is not None and len(result.error) > 0


@pytest.mark.asyncio
@settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    deadline=None,
)
@given(claim=claim_strategy)
async def test_property_11_completeness_invariant(claim: FactualClaim) -> None:
    """Property 11 (completeness): Every claim MUST have a definitive status.

    For any factual claim, the result must satisfy exactly one of:
    - verified=True, status in {VERIFIED, CONFLICTING}, sources non-empty
    - verified=False, status in {UNVERIFIED, ERROR, CONFLICTING}

    No claim should remain in an ambiguous state.
    """
    client = _make_verified_client()
    grounder = SearchGrounder(client)

    result = await grounder.verify_fact(claim)

    # The result must have a definitive status
    assert result.status in (
        VerificationStatus.VERIFIED,
        VerificationStatus.UNVERIFIED,
        VerificationStatus.CONFLICTING,
        VerificationStatus.ERROR,
    ), f"Unexpected status: {result.status}"

    # Consistency checks
    if result.verified:
        assert result.confidence > 0
        assert len(result.sources) > 0
    else:
        assert result.status in (
            VerificationStatus.UNVERIFIED,
            VerificationStatus.ERROR,
            VerificationStatus.CONFLICTING,
        )
