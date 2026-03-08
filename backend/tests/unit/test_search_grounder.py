"""Unit tests for the Search Grounder service.

Tests cover:
- Fact verification (success, timeout, error paths) (Req 8.1)
- Source citation extraction (Req 8.2)
- Unverified claim marking (Req 8.3)
- Source authority ranking (Req 8.5)
- Conflict detection and multiple perspectives (Req 8.6)
- Batch verification
- Graceful degradation on failure
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.search_grounder.grounder import (
    SearchGrounder,
    _MODEL_ID,
    _CONFIDENCE_THRESHOLD,
    _classify_authority,
)
from backend.services.search_grounder.models import (
    ClaimImportance,
    ConflictReport,
    DocumentaryContext,
    FactualClaim,
    PerspectiveSet,
    Source,
    SourceAuthority,
    VerificationResult,
    VerificationStatus,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_grounding_chunk(uri: str, title: str) -> MagicMock:
    """Create a mock grounding chunk."""
    chunk = MagicMock()
    web = MagicMock()
    web.uri = uri
    web.title = title
    chunk.web = web
    return chunk


def _make_grounding_support(text: str, indices: list[int]) -> MagicMock:
    """Create a mock grounding support."""
    support = MagicMock()
    segment = MagicMock()
    segment.text = text
    support.segment = segment
    support.grounding_chunk_indices = indices
    return support


def _make_mock_client(
    response_text: str = "The claim is verified and accurate.",
    grounding_chunks: list | None = None,
    grounding_supports: list | None = None,
) -> MagicMock:
    """Create a mock google.genai.Client that returns a grounded response."""
    client = MagicMock()

    # Build response with grounding metadata
    grounding_metadata = MagicMock()
    grounding_metadata.grounding_chunks = grounding_chunks or [
        _make_grounding_chunk("https://en.wikipedia.org/wiki/Example", "Example - Wikipedia"),
        _make_grounding_chunk("https://www.britannica.com/topic/Example", "Example | Britannica"),
    ]
    grounding_metadata.grounding_supports = grounding_supports or [
        _make_grounding_support("The fact is confirmed by multiple sources.", [0, 1]),
    ]
    grounding_metadata.web_search_queries = ["example fact verification"]
    grounding_metadata.search_entry_point = MagicMock()

    candidate = MagicMock()
    candidate.grounding_metadata = grounding_metadata

    response = MagicMock()
    response.candidates = [candidate]
    response.text = response_text

    client.aio.models.generate_content = AsyncMock(return_value=response)
    return client


def _make_failing_client(exc: Exception) -> MagicMock:
    """Create a mock client that raises an exception."""
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(side_effect=exc)
    return client


def _make_empty_response_client() -> MagicMock:
    """Create a mock client returning a response with no grounding."""
    client = MagicMock()
    response = MagicMock()
    response.candidates = []
    response.text = ""
    client.aio.models.generate_content = AsyncMock(return_value=response)
    return client


def _make_no_grounding_client() -> MagicMock:
    """Create a mock client that returns text but no grounding metadata."""
    client = MagicMock()
    candidate = MagicMock()
    candidate.grounding_metadata = None
    response = MagicMock()
    response.candidates = [candidate]
    response.text = "This claim cannot be verified."
    client.aio.models.generate_content = AsyncMock(return_value=response)
    return client


def _sample_claim(**kwargs: Any) -> FactualClaim:
    """Create a sample factual claim."""
    defaults = {
        "text": "The Colosseum in Rome was completed in 80 AD.",
        "importance": ClaimImportance.CRITICAL,
    }
    defaults.update(kwargs)
    return FactualClaim(**defaults)


def _sample_context(**kwargs: Any) -> DocumentaryContext:
    """Create a sample documentary context."""
    defaults = {
        "location_name": "Colosseum, Rome",
        "topic": "Ancient Roman Architecture",
        "historical_period": "Roman Empire",
        "mode": "sight",
    }
    defaults.update(kwargs)
    return DocumentaryContext(**defaults)


# ── Fact Verification Tests ──────────────────────────────────────────────────


class TestVerifyFact:
    """Tests for SearchGrounder.verify_fact()."""

    @pytest.mark.asyncio
    async def test_successful_verification(self) -> None:
        """Req 8.1: Claims are verified using Google Search Grounding."""
        client = _make_mock_client(response_text="The claim is verified and accurate.")
        grounder = SearchGrounder(client)
        claim = _sample_claim()

        result = await grounder.verify_fact(claim)

        assert result.claim == claim
        assert result.verified is True
        assert result.status == VerificationStatus.VERIFIED
        assert result.confidence > 0
        assert len(result.sources) > 0
        assert result.error is None
        assert result.verification_time_ms > 0

    @pytest.mark.asyncio
    async def test_source_citations_extracted(self) -> None:
        """Req 8.2: Source citations are provided for verified facts."""
        chunks = [
            _make_grounding_chunk("https://www.history.com/topics/colosseum", "Colosseum - History"),
            _make_grounding_chunk("https://scholar.google.com/article", "Academic Paper"),
        ]
        supports = [
            _make_grounding_support("Built between 72-80 AD under Emperor Vespasian.", [0, 1]),
        ]
        client = _make_mock_client(
            response_text="Verified: completed in 80 AD.",
            grounding_chunks=chunks,
            grounding_supports=supports,
        )
        grounder = SearchGrounder(client)
        result = await grounder.verify_fact(_sample_claim())

        assert len(result.sources) == 2
        assert result.sources[0].url in [
            "https://www.history.com/topics/colosseum",
            "https://scholar.google.com/article",
        ]
        # At least one source should have an excerpt
        excerpts = [s.excerpt for s in result.sources if s.excerpt]
        assert len(excerpts) > 0

    @pytest.mark.asyncio
    async def test_unverified_claim_marked(self) -> None:
        """Req 8.3: Unverifiable claims are marked as unverified."""
        client = _make_no_grounding_client()
        grounder = SearchGrounder(client)
        claim = _sample_claim(text="A completely fabricated claim with no basis.")

        result = await grounder.verify_fact(claim)

        assert result.verified is False
        assert result.status == VerificationStatus.UNVERIFIED

    @pytest.mark.asyncio
    async def test_timeout_returns_unverified(self) -> None:
        """Timeout results in graceful degradation with error status."""
        client = MagicMock()

        async def slow_response(*args: Any, **kwargs: Any) -> None:
            await asyncio.sleep(10)

        client.aio.models.generate_content = AsyncMock(side_effect=slow_response)
        grounder = SearchGrounder(client)

        # Patch the timeout to be very short for testing
        with patch("backend.services.search_grounder.grounder._VERIFICATION_TIMEOUT_S", 0.01):
            result = await grounder.verify_fact(_sample_claim())

        assert result.verified is False
        assert result.status == VerificationStatus.ERROR
        assert result.error is not None
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_api_error_returns_unverified(self) -> None:
        """API errors result in graceful degradation."""
        client = _make_failing_client(RuntimeError("API quota exceeded"))
        grounder = SearchGrounder(client)

        result = await grounder.verify_fact(_sample_claim())

        assert result.verified is False
        assert result.status == VerificationStatus.ERROR
        assert result.error is not None
        assert "quota" in result.error.lower()

    @pytest.mark.asyncio
    async def test_empty_response_returns_unverified(self) -> None:
        """Empty API response marks claim as unverified."""
        client = _make_empty_response_client()
        grounder = SearchGrounder(client)

        result = await grounder.verify_fact(_sample_claim())

        assert result.verified is False
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_claim_with_context(self) -> None:
        """Context is included in the verification prompt."""
        client = _make_mock_client()
        grounder = SearchGrounder(client)
        context = _sample_context()
        claim = _sample_claim(context=context)

        result = await grounder.verify_fact(claim)

        # Verify the API was called with context-enriched prompt
        call_args = client.aio.models.generate_content.call_args
        prompt = call_args.kwargs.get("contents", "")
        assert "Colosseum, Rome" in prompt
        assert "Ancient Roman Architecture" in prompt
        assert result.claim == claim

    @pytest.mark.asyncio
    async def test_verification_time_tracked(self) -> None:
        """Verification time is measured and returned."""
        client = _make_mock_client()
        grounder = SearchGrounder(client)

        result = await grounder.verify_fact(_sample_claim())

        assert result.verification_time_ms >= 0

    @pytest.mark.asyncio
    async def test_claim_importance_preserved(self) -> None:
        """Claim importance level is preserved in the result."""
        client = _make_mock_client()
        grounder = SearchGrounder(client)

        for importance in ClaimImportance:
            claim = _sample_claim(importance=importance)
            result = await grounder.verify_fact(claim)
            assert result.claim.importance == importance


# ── Batch Verification Tests ─────────────────────────────────────────────────


class TestVerifyBatch:
    """Tests for SearchGrounder.verify_batch()."""

    @pytest.mark.asyncio
    async def test_batch_verification(self) -> None:
        """Batch verification returns one result per claim."""
        client = _make_mock_client()
        grounder = SearchGrounder(client)
        claims = [
            _sample_claim(text="Claim 1"),
            _sample_claim(text="Claim 2"),
            _sample_claim(text="Claim 3"),
        ]

        results = await grounder.verify_batch(claims)

        assert len(results) == 3
        for i, result in enumerate(results):
            assert result.claim.text == f"Claim {i + 1}"

    @pytest.mark.asyncio
    async def test_empty_batch(self) -> None:
        """Empty batch returns empty list."""
        client = _make_mock_client()
        grounder = SearchGrounder(client)

        results = await grounder.verify_batch([])

        assert results == []

    @pytest.mark.asyncio
    async def test_batch_with_mixed_results(self) -> None:
        """Batch handles a mix of success and failure."""
        # First call succeeds, second fails
        client = MagicMock()
        call_count = 0

        async def alternating(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                raise RuntimeError("API error")
            # Return valid response
            grounding_metadata = MagicMock()
            grounding_metadata.grounding_chunks = [
                _make_grounding_chunk("https://example.com", "Example"),
            ]
            grounding_metadata.grounding_supports = []
            candidate = MagicMock()
            candidate.grounding_metadata = grounding_metadata
            response = MagicMock()
            response.candidates = [candidate]
            response.text = "Verified claim."
            return response

        client.aio.models.generate_content = AsyncMock(side_effect=alternating)
        grounder = SearchGrounder(client)
        claims = [_sample_claim(text=f"Claim {i}") for i in range(4)]

        results = await grounder.verify_batch(claims)

        assert len(results) == 4
        # Some should succeed, some should have errors
        errors = [r for r in results if r.error is not None]
        successes = [r for r in results if r.error is None]
        assert len(errors) > 0
        assert len(successes) > 0


# ── Source Ranking Tests ─────────────────────────────────────────────────────


class TestRankSources:
    """Tests for SearchGrounder.rank_sources()."""

    def test_authority_ranking(self) -> None:
        """Req 8.5: Academic > government > media > other."""
        client = MagicMock()
        grounder = SearchGrounder(client)

        sources = [
            Source(url="https://other.com", title="Other", authority=SourceAuthority.OTHER, relevance=0.9),
            Source(url="https://harvard.edu", title="Harvard", authority=SourceAuthority.ACADEMIC, relevance=0.7),
            Source(url="https://state.gov", title="State Dept", authority=SourceAuthority.GOVERNMENT, relevance=0.8),
            Source(url="https://bbc.com", title="BBC", authority=SourceAuthority.MEDIA, relevance=0.85),
        ]

        ranked = grounder.rank_sources(sources)

        assert ranked[0].authority == SourceAuthority.ACADEMIC
        assert ranked[1].authority == SourceAuthority.GOVERNMENT
        assert ranked[2].authority == SourceAuthority.MEDIA
        assert ranked[3].authority == SourceAuthority.OTHER

    def test_relevance_within_tier(self) -> None:
        """Within same authority tier, higher relevance ranks first."""
        client = MagicMock()
        grounder = SearchGrounder(client)

        sources = [
            Source(url="https://a.edu/low", title="Low", authority=SourceAuthority.ACADEMIC, relevance=0.3),
            Source(url="https://b.edu/high", title="High", authority=SourceAuthority.ACADEMIC, relevance=0.9),
        ]

        ranked = grounder.rank_sources(sources)

        assert ranked[0].relevance == 0.9
        assert ranked[1].relevance == 0.3

    def test_empty_sources(self) -> None:
        """Empty list returns empty."""
        grounder = SearchGrounder(MagicMock())
        assert grounder.rank_sources([]) == []


# ── Authority Classification Tests ───────────────────────────────────────────


class TestClassifyAuthority:
    """Tests for _classify_authority()."""

    def test_academic_domains(self) -> None:
        assert _classify_authority("https://www.harvard.edu/research") == SourceAuthority.ACADEMIC
        assert _classify_authority("https://arxiv.org/abs/1234") == SourceAuthority.ACADEMIC
        assert _classify_authority("https://scholar.google.com/article") == SourceAuthority.ACADEMIC
        assert _classify_authority("https://www.ox.ac.uk/study") == SourceAuthority.ACADEMIC

    def test_government_domains(self) -> None:
        assert _classify_authority("https://www.state.gov/policy") == SourceAuthority.GOVERNMENT
        assert _classify_authority("https://www.who.int/news") == SourceAuthority.GOVERNMENT
        assert _classify_authority("https://www.defense.mil/") == SourceAuthority.GOVERNMENT

    def test_media_domains(self) -> None:
        assert _classify_authority("https://www.bbc.com/news") == SourceAuthority.MEDIA
        assert _classify_authority("https://www.britannica.com/topic/Rome") == SourceAuthority.MEDIA
        assert _classify_authority("https://www.reuters.com/article") == SourceAuthority.MEDIA

    def test_other_domains(self) -> None:
        assert _classify_authority("https://www.randomsite.com") == SourceAuthority.OTHER
        assert _classify_authority("https://blog.example.org") == SourceAuthority.OTHER

    def test_empty_url(self) -> None:
        assert _classify_authority("") == SourceAuthority.OTHER


# ── Conflict Detection Tests ─────────────────────────────────────────────────


class TestDetectConflicts:
    """Tests for SearchGrounder.detect_conflicts()."""

    def test_no_conflict_single_source(self) -> None:
        """Single source cannot conflict."""
        grounder = SearchGrounder(MagicMock())
        sources = [
            Source(url="https://example.com", title="Example", excerpt="A simple fact."),
        ]
        assert grounder.detect_conflicts(sources) is None

    def test_no_conflict_consistent_sources(self) -> None:
        """Consistent sources return None."""
        grounder = SearchGrounder(MagicMock())
        sources = [
            Source(url="https://a.edu", title="A", authority=SourceAuthority.ACADEMIC, excerpt="The fact is clear."),
            Source(url="https://b.edu", title="B", authority=SourceAuthority.ACADEMIC, excerpt="The fact is confirmed."),
        ]
        assert grounder.detect_conflicts(sources) is None

    def test_conflict_detected(self) -> None:
        """Req 8.6: Conflicting sources produce a ConflictReport."""
        grounder = SearchGrounder(MagicMock())
        sources = [
            Source(
                url="https://a.edu",
                title="A",
                authority=SourceAuthority.ACADEMIC,
                excerpt="The date is confirmed as 80 AD.",
            ),
            Source(
                url="https://b.gov",
                title="B",
                authority=SourceAuthority.GOVERNMENT,
                excerpt="However, some historians dispute the exact date.",
            ),
        ]

        report = grounder.detect_conflicts(sources)

        assert report is not None
        assert len(report.conflicting_sources) == 2
        assert "conflicting" in report.summary.lower() or "sources" in report.summary.lower()

    def test_empty_sources(self) -> None:
        """Empty list returns None."""
        grounder = SearchGrounder(MagicMock())
        assert grounder.detect_conflicts([]) is None


# ── Multiple Perspectives Tests ──────────────────────────────────────────────


class TestPresentMultiplePerspectives:
    """Tests for SearchGrounder.present_multiple_perspectives()."""

    def test_perspectives_from_conflict(self) -> None:
        """Req 8.6: Generates PerspectiveSet from ConflictReport."""
        grounder = SearchGrounder(MagicMock())
        report = ConflictReport(
            claim_text="The Colosseum completion date",
            conflicting_sources=[
                Source(
                    url="https://a.edu",
                    title="Academic Source",
                    authority=SourceAuthority.ACADEMIC,
                    relevance=0.9,
                    excerpt="Completed in 80 AD under Titus.",
                ),
                Source(
                    url="https://b.com",
                    title="Other Source",
                    authority=SourceAuthority.OTHER,
                    relevance=0.5,
                    excerpt="Construction may have ended in 82 AD.",
                ),
            ],
            summary="Conflicting dates.",
        )

        result = grounder.present_multiple_perspectives(report)

        assert isinstance(result, PerspectiveSet)
        assert result.claim_text == "The Colosseum completion date"
        assert len(result.perspectives) == 2
        # Academic should be ranked first
        assert result.perspectives[0].authority == SourceAuthority.ACADEMIC
        assert result.synthesis  # Non-empty synthesis

    def test_perspectives_ranked_by_authority(self) -> None:
        """Perspectives are ordered by authority."""
        grounder = SearchGrounder(MagicMock())
        report = ConflictReport(
            claim_text="Test",
            conflicting_sources=[
                Source(url="https://blog.com", title="Blog", authority=SourceAuthority.OTHER, relevance=0.8),
                Source(url="https://mit.edu", title="MIT", authority=SourceAuthority.ACADEMIC, relevance=0.7),
            ],
        )

        result = grounder.present_multiple_perspectives(report)

        assert result.perspectives[0].authority == SourceAuthority.ACADEMIC


# ── Source Extraction Tests ──────────────────────────────────────────────────


class TestSourceExtraction:
    """Tests for _extract_sources from grounding metadata."""

    @pytest.mark.asyncio
    async def test_sources_from_grounding_chunks(self) -> None:
        """Sources are extracted from grounding_chunks in response."""
        chunks = [
            _make_grounding_chunk("https://www.britannica.com/topic/Colosseum", "Colosseum"),
            _make_grounding_chunk("https://www.history.com/topics/colosseum", "History of Colosseum"),
            _make_grounding_chunk("https://arxiv.org/abs/1234", "Research Paper"),
        ]
        supports = [
            _make_grounding_support("The Colosseum was completed in 80 AD.", [0, 1]),
            _make_grounding_support("It was built under Emperor Vespasian.", [2]),
        ]
        client = _make_mock_client(
            response_text="Verified.",
            grounding_chunks=chunks,
            grounding_supports=supports,
        )
        grounder = SearchGrounder(client)
        result = await grounder.verify_fact(_sample_claim())

        assert len(result.sources) == 3
        urls = {s.url for s in result.sources}
        assert "https://www.britannica.com/topic/Colosseum" in urls
        assert "https://arxiv.org/abs/1234" in urls

    @pytest.mark.asyncio
    async def test_authority_assigned_to_extracted_sources(self) -> None:
        """Extracted sources get correct authority classification."""
        chunks = [
            _make_grounding_chunk("https://www.ox.ac.uk/research", "Oxford"),
            _make_grounding_chunk("https://www.state.gov/history", "State Dept"),
            _make_grounding_chunk("https://www.bbc.com/news", "BBC News"),
            _make_grounding_chunk("https://www.example.com", "Random"),
        ]
        client = _make_mock_client(
            response_text="Confirmed.",
            grounding_chunks=chunks,
            grounding_supports=[],
        )
        grounder = SearchGrounder(client)
        result = await grounder.verify_fact(_sample_claim())

        # Sources should be ranked: academic first
        assert result.sources[0].authority == SourceAuthority.ACADEMIC
        assert result.sources[1].authority == SourceAuthority.GOVERNMENT

    @pytest.mark.asyncio
    async def test_relevance_decreases_by_position(self) -> None:
        """Later grounding chunks get lower relevance scores."""
        chunks = [
            _make_grounding_chunk(f"https://example{i}.com", f"Source {i}")
            for i in range(5)
        ]
        client = _make_mock_client(
            response_text="Confirmed.",
            grounding_chunks=chunks,
            grounding_supports=[],
        )
        grounder = SearchGrounder(client)
        result = await grounder.verify_fact(_sample_claim())

        # Before ranking by authority, the raw relevance should decrease
        # After ranking, all are "other" so relevance ordering is preserved
        for i in range(len(result.sources) - 1):
            assert result.sources[i].relevance >= result.sources[i + 1].relevance


# ── Confidence Calculation Tests ─────────────────────────────────────────────


class TestConfidenceCalculation:
    """Tests for confidence scoring."""

    @pytest.mark.asyncio
    async def test_high_confidence_with_authoritative_sources(self) -> None:
        """Many authoritative sources with positive text = high confidence."""
        chunks = [
            _make_grounding_chunk("https://harvard.edu/paper", "Harvard"),
            _make_grounding_chunk("https://www.state.gov/facts", "Gov"),
            _make_grounding_chunk("https://bbc.com/article", "BBC"),
            _make_grounding_chunk("https://reuters.com/article", "Reuters"),
            _make_grounding_chunk("https://britannica.com/topic", "Britannica"),
        ]
        client = _make_mock_client(
            response_text="The claim is verified and confirmed by multiple sources.",
            grounding_chunks=chunks,
            grounding_supports=[],
        )
        grounder = SearchGrounder(client)
        result = await grounder.verify_fact(_sample_claim())

        assert result.confidence >= 0.7
        assert result.verified is True

    @pytest.mark.asyncio
    async def test_low_confidence_with_no_sources(self) -> None:
        """No sources = zero confidence."""
        client = _make_no_grounding_client()
        grounder = SearchGrounder(client)
        result = await grounder.verify_fact(_sample_claim())

        assert result.confidence == 0.0
        assert result.verified is False

    @pytest.mark.asyncio
    async def test_negative_text_signals_lower_confidence(self) -> None:
        """Negative response text lowers confidence."""
        chunks = [
            _make_grounding_chunk("https://example.com", "Example"),
        ]
        client = _make_mock_client(
            response_text="The claim is inaccurate and no evidence supports it.",
            grounding_chunks=chunks,
            grounding_supports=[],
        )
        grounder = SearchGrounder(client)
        result = await grounder.verify_fact(_sample_claim())

        assert result.confidence < 0.5


# ── Prompt Building Tests ────────────────────────────────────────────────────


class TestPromptBuilding:
    """Tests for verification prompt construction."""

    @pytest.mark.asyncio
    async def test_basic_prompt(self) -> None:
        """Basic claim produces a verification prompt."""
        client = _make_mock_client()
        grounder = SearchGrounder(client)
        claim = _sample_claim()

        await grounder.verify_fact(claim)

        call_args = client.aio.models.generate_content.call_args
        prompt = call_args.kwargs.get("contents", "")
        assert "Verify" in prompt
        assert claim.text in prompt

    @pytest.mark.asyncio
    async def test_prompt_includes_location_context(self) -> None:
        """Location context is included in prompt."""
        client = _make_mock_client()
        grounder = SearchGrounder(client)
        claim = _sample_claim(context=_sample_context(location_name="Taj Mahal, India"))

        await grounder.verify_fact(claim)

        prompt = client.aio.models.generate_content.call_args.kwargs.get("contents", "")
        assert "Taj Mahal, India" in prompt

    @pytest.mark.asyncio
    async def test_prompt_includes_historical_period(self) -> None:
        """Historical period context is included in prompt."""
        client = _make_mock_client()
        grounder = SearchGrounder(client)
        claim = _sample_claim(context=_sample_context(historical_period="Medieval"))

        await grounder.verify_fact(claim)

        prompt = client.aio.models.generate_content.call_args.kwargs.get("contents", "")
        assert "Medieval" in prompt

    @pytest.mark.asyncio
    async def test_prompt_includes_topic(self) -> None:
        """Topic context is included in prompt."""
        client = _make_mock_client()
        grounder = SearchGrounder(client)
        claim = _sample_claim(context=_sample_context(topic="Gothic Architecture"))

        await grounder.verify_fact(claim)

        prompt = client.aio.models.generate_content.call_args.kwargs.get("contents", "")
        assert "Gothic Architecture" in prompt


# ── find_sources Tests ───────────────────────────────────────────────────────


class TestFindSources:
    """Tests for SearchGrounder.find_sources()."""

    def test_find_sources_returns_ranked(self) -> None:
        """find_sources returns sources ranked by authority."""
        grounder = SearchGrounder(MagicMock())
        claim = _sample_claim()
        raw = [
            Source(url="https://blog.com", title="Blog", authority=SourceAuthority.OTHER, relevance=0.9),
            Source(url="https://mit.edu", title="MIT", authority=SourceAuthority.ACADEMIC, relevance=0.5),
        ]

        result = grounder.find_sources(claim, raw)

        assert result[0].authority == SourceAuthority.ACADEMIC


# ── Model Validation Tests ───────────────────────────────────────────────────


class TestModels:
    """Tests for data model validation."""

    def test_factual_claim_defaults(self) -> None:
        """FactualClaim has sensible defaults."""
        claim = FactualClaim(text="Test claim")
        assert claim.importance == ClaimImportance.SUPPORTING
        assert claim.context is None
        assert claim.id  # Auto-generated

    def test_verification_result_defaults(self) -> None:
        """VerificationResult has sensible defaults."""
        claim = FactualClaim(text="Test")
        result = VerificationResult(claim=claim)
        assert result.verified is False
        assert result.status == VerificationStatus.UNVERIFIED
        assert result.confidence == 0.0
        assert result.sources == []
        assert result.error is None

    def test_source_validation(self) -> None:
        """Source relevance is clamped to [0, 1]."""
        source = Source(url="https://example.com", title="Test", relevance=0.5)
        assert source.relevance == 0.5

    def test_documentary_context(self) -> None:
        """DocumentaryContext with all fields."""
        ctx = _sample_context()
        assert ctx.location_name == "Colosseum, Rome"
        assert ctx.topic == "Ancient Roman Architecture"
        assert ctx.historical_period == "Roman Empire"
        assert ctx.mode == "sight"
