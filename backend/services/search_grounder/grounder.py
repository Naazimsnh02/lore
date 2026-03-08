"""Search Grounder – Google Search Grounding API integration for LORE.

Design reference: LORE design.md, Section 6 – Search Grounder.
Requirements: 8.1–8.6.

Architecture notes
------------------
- Uses ``google-genai`` SDK ``generate_content`` with
  ``Tool(google_search=GoogleSearch())`` to verify factual claims via
  Google Search Grounding (Req 8.1).
- Extracts grounding metadata (chunks, supports, search queries) from the
  response to build source citations (Req 8.2).
- Unverifiable claims are explicitly marked as unverified (Req 8.3).
- Sources are ranked by authority: academic > government > media > other (Req 8.5).
- Conflicting sources are detected and presented as multiple perspectives (Req 8.6).
- Constructor accepts an injected ``genai.Client`` for testability.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Optional
from urllib.parse import urlparse

from .models import (
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

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Gemini model for search-grounded verification
_MODEL_ID = "gemini-3-flash-preview"

# Hard timeout per verification request (< 1s target per design.md)
_VERIFICATION_TIMEOUT_S = 5.0

# Confidence threshold: below this the claim is marked unverified
_CONFIDENCE_THRESHOLD = 0.4

# Maximum concurrent batch verifications
_MAX_CONCURRENCY = 10

# ── Authority classification ─────────────────────────────────────────────────

# Domain patterns → authority level (Req 8.5)
_AUTHORITY_PATTERNS: dict[str, list[str]] = {
    "academic": [
        ".edu",
        ".ac.",
        "scholar.google",
        "arxiv.org",
        "jstor.org",
        "pubmed",
        "ncbi.nlm.nih.gov",
        "doi.org",
        "researchgate.net",
        "springer.com",
        "wiley.com",
        "nature.com",
        "science.org",
        "ieee.org",
        "acm.org",
    ],
    "government": [
        ".gov",
        ".mil",
        ".int",
        "who.int",
        "un.org",
        "europa.eu",
        "worldbank.org",
    ],
    "media": [
        "bbc.com",
        "bbc.co.uk",
        "reuters.com",
        "apnews.com",
        "nytimes.com",
        "theguardian.com",
        "washingtonpost.com",
        "nationalgeographic.com",
        "smithsonianmag.com",
        "britannica.com",
        "wikipedia.org",
        "history.com",
        "cnn.com",
    ],
}


def _classify_authority(url: str) -> SourceAuthority:
    """Classify a URL's authority level based on domain patterns."""
    domain = urlparse(url).netloc.lower() if url else ""
    full_url = url.lower()

    for level, patterns in _AUTHORITY_PATTERNS.items():
        for pattern in patterns:
            if pattern in domain or pattern in full_url:
                return SourceAuthority(level)

    return SourceAuthority.OTHER


# Authority rank for sorting (lower = more authoritative)
_AUTHORITY_RANK: dict[SourceAuthority, int] = {
    SourceAuthority.ACADEMIC: 0,
    SourceAuthority.GOVERNMENT: 1,
    SourceAuthority.MEDIA: 2,
    SourceAuthority.OTHER: 3,
}


class SearchGrounder:
    """Verifies factual claims using Google Search Grounding API.

    Design reference: SearchGrounder interface in design.md §6.
    Requirements: 8.1–8.6.

    Parameters
    ----------
    client:
        ``google.genai.Client`` instance (injected for testability).
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    # ── Public API ────────────────────────────────────────────────────────

    async def verify_fact(self, claim: FactualClaim) -> VerificationResult:
        """Verify a single factual claim using Google Search Grounding.

        Requirements: 8.1 (verify via Search Grounding), 8.2 (source citations),
                      8.3 (mark unverified), 8.5 (authoritative sources),
                      8.6 (multiple perspectives on conflict).

        Parameters
        ----------
        claim:
            The factual claim to verify.

        Returns
        -------
        VerificationResult
            Verification outcome with sources, confidence, and status.
            Never raises; returns error status on failure.
        """
        start_ms = time.monotonic() * 1000

        try:
            result = await asyncio.wait_for(
                self._do_verify(claim),
                timeout=_VERIFICATION_TIMEOUT_S,
            )
            result.verification_time_ms = time.monotonic() * 1000 - start_ms
            return result

        except asyncio.TimeoutError:
            elapsed_ms = time.monotonic() * 1000 - start_ms
            logger.warning(
                "Fact verification timed out after %.0f ms for claim: %.80s",
                elapsed_ms,
                claim.text,
            )
            return self._unverified_result(claim, elapsed_ms, "Verification timed out")

        except Exception as exc:
            elapsed_ms = time.monotonic() * 1000 - start_ms
            logger.error(
                "Fact verification failed after %.0f ms: %s",
                elapsed_ms,
                exc,
            )
            return self._unverified_result(claim, elapsed_ms, str(exc))

    async def verify_batch(
        self, claims: list[FactualClaim]
    ) -> list[VerificationResult]:
        """Verify multiple factual claims concurrently.

        Parameters
        ----------
        claims:
            List of claims to verify.

        Returns
        -------
        list[VerificationResult]
            One result per claim, in the same order.
        """
        if not claims:
            return []

        # Use a semaphore to limit concurrency
        semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

        async def _bounded_verify(claim: FactualClaim) -> VerificationResult:
            async with semaphore:
                return await self.verify_fact(claim)

        tasks = [_bounded_verify(c) for c in claims]
        return list(await asyncio.gather(*tasks, return_exceptions=False))

    def find_sources(self, claim: FactualClaim, raw_sources: list[Source]) -> list[Source]:
        """Filter and return relevant sources for a claim.

        Parameters
        ----------
        claim:
            The claim being verified.
        raw_sources:
            Unfiltered sources from the grounding response.

        Returns
        -------
        list[Source]
            Sources relevant to the claim, ranked by authority.
        """
        return self.rank_sources(raw_sources)

    def rank_sources(self, sources: list[Source]) -> list[Source]:
        """Rank sources by authority level, then by relevance score.

        Requirements: 8.5 (prioritize authoritative sources).

        Parameters
        ----------
        sources:
            Unranked source list.

        Returns
        -------
        list[Source]
            Sources sorted: academic > government > media > other,
            then by descending relevance within each tier.
        """
        return sorted(
            sources,
            key=lambda s: (_AUTHORITY_RANK.get(s.authority, 99), -s.relevance),
        )

    def detect_conflicts(self, sources: list[Source]) -> Optional[ConflictReport]:
        """Detect conflicting information across sources.

        Requirements: 8.6 (present multiple perspectives).

        Heuristic: if sources span 2+ different authority tiers and any
        excerpts contain contradictory signal words, flag as conflicting.

        Parameters
        ----------
        sources:
            Sources to check for conflicts.

        Returns
        -------
        ConflictReport or None
            Report if conflicts detected, None otherwise.
        """
        if len(sources) < 2:
            return None

        # Collect unique authority tiers represented
        authority_tiers = {s.authority for s in sources}

        # Check excerpts for contradiction signals
        contradiction_signals = [
            "however", "contrary", "disputed", "debated",
            "on the other hand", "alternatively", "some argue",
            "not all agree", "controversial", "conflicting",
        ]

        has_contradiction = False
        for source in sources:
            excerpt_lower = source.excerpt.lower()
            if any(signal in excerpt_lower for signal in contradiction_signals):
                has_contradiction = True
                break

        if not has_contradiction and len(authority_tiers) < 2:
            return None

        if not has_contradiction:
            return None

        return ConflictReport(
            claim_text=sources[0].excerpt[:200] if sources else "",
            conflicting_sources=sources,
            summary=f"Found conflicting information across {len(sources)} sources "
            f"spanning {len(authority_tiers)} authority tiers.",
        )

    def present_multiple_perspectives(
        self, conflict_report: ConflictReport
    ) -> PerspectiveSet:
        """Present multiple perspectives from a conflict report.

        Requirements: 8.6 (present multiple perspectives with sources).

        Parameters
        ----------
        conflict_report:
            Detected conflicts.

        Returns
        -------
        PerspectiveSet
            Organized perspectives with synthesis.
        """
        ranked = self.rank_sources(conflict_report.conflicting_sources)

        synthesis_parts = [
            f"Multiple perspectives exist regarding: {conflict_report.claim_text[:100]}."
        ]
        for i, src in enumerate(ranked[:5], 1):
            if src.excerpt:
                synthesis_parts.append(
                    f"  {i}. [{src.authority.value}] {src.title}: {src.excerpt[:150]}"
                )

        return PerspectiveSet(
            claim_text=conflict_report.claim_text,
            perspectives=ranked,
            synthesis="\n".join(synthesis_parts),
        )

    # ── Private helpers ───────────────────────────────────────────────────

    async def _do_verify(self, claim: FactualClaim) -> VerificationResult:
        """Core verification logic using Google Search Grounding API.

        Calls Gemini with GoogleSearch tool, then extracts grounding metadata
        to build structured VerificationResult.
        """
        from google.genai import types

        # Build the verification prompt with context
        prompt = self._build_verification_prompt(claim)

        # Configure search grounding tool (Req 8.1)
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
        )

        # Call Gemini with search grounding
        response = await self._client.aio.models.generate_content(
            model=_MODEL_ID,
            contents=prompt,
            config=config,
        )

        # Extract grounding metadata from response
        sources = self._extract_sources(response)
        confidence = self._calculate_confidence(response, sources)

        # Rank sources by authority (Req 8.5)
        ranked_sources = self.rank_sources(sources)

        # Determine verification status
        if confidence >= _CONFIDENCE_THRESHOLD and ranked_sources:
            status = VerificationStatus.VERIFIED
            verified = True
        else:
            status = VerificationStatus.UNVERIFIED
            verified = False

        # Check for conflicting information (Req 8.6)
        alternative_perspectives: list[Source] = []
        conflict = self.detect_conflicts(ranked_sources)
        if conflict:
            status = VerificationStatus.CONFLICTING
            perspective_set = self.present_multiple_perspectives(conflict)
            alternative_perspectives = perspective_set.perspectives

        return VerificationResult(
            claim=claim,
            status=status,
            verified=verified,
            confidence=confidence,
            sources=ranked_sources,
            alternative_perspectives=alternative_perspectives,
        )

    def _build_verification_prompt(self, claim: FactualClaim) -> str:
        """Build a prompt that instructs Gemini to verify the claim with search."""
        parts = [
            "Verify the following factual claim using search. "
            "Determine if the claim is accurate, partially accurate, or inaccurate. "
            "Provide specific evidence from search results.",
        ]

        if claim.context:
            if claim.context.location_name:
                parts.append(f"Location context: {claim.context.location_name}.")
            if claim.context.topic:
                parts.append(f"Topic context: {claim.context.topic}.")
            if claim.context.historical_period:
                parts.append(f"Historical period: {claim.context.historical_period}.")

        parts.append(f'\nClaim to verify: "{claim.text}"')

        parts.append(
            "\nRespond with a clear verdict (VERIFIED, UNVERIFIED, or CONFLICTING) "
            "followed by supporting evidence."
        )

        return " ".join(parts)

    def _extract_sources(self, response: Any) -> list[Source]:
        """Extract source citations from Gemini grounding metadata.

        Parses grounding_chunks from the response candidate's grounding_metadata.
        """
        sources: list[Source] = []

        if not response or not response.candidates:
            return sources

        candidate = response.candidates[0]
        grounding_metadata = getattr(candidate, "grounding_metadata", None)
        if not grounding_metadata:
            return sources

        # Extract grounding chunks (each is a web source)
        grounding_chunks = getattr(grounding_metadata, "grounding_chunks", None) or []
        grounding_supports = getattr(grounding_metadata, "grounding_supports", None) or []

        # Build a mapping from chunk index → support text
        chunk_excerpts: dict[int, str] = {}
        for support in grounding_supports:
            segment = getattr(support, "segment", None)
            indices = getattr(support, "grounding_chunk_indices", None) or []
            text = getattr(segment, "text", "") if segment else ""
            for idx in indices:
                if text:
                    chunk_excerpts[idx] = text

        for i, chunk in enumerate(grounding_chunks):
            web = getattr(chunk, "web", None)
            if not web:
                continue

            url = getattr(web, "uri", "") or ""
            title = getattr(web, "title", "") or ""
            excerpt = chunk_excerpts.get(i, "")

            authority = _classify_authority(url)

            # Assign relevance based on position (first results tend to be most relevant)
            relevance = max(0.0, 1.0 - (i * 0.1))

            sources.append(
                Source(
                    url=url,
                    title=title,
                    authority=authority,
                    relevance=round(relevance, 2),
                    excerpt=excerpt,
                )
            )

        return sources

    def _calculate_confidence(
        self, response: Any, sources: list[Source]
    ) -> float:
        """Calculate verification confidence from response and source quality.

        Confidence is based on:
        - Number of sources found (40% weight)
        - Authority of top sources (30% weight)
        - Response text signals (30% weight)
        """
        if not sources:
            return 0.0

        # Source count factor (0-1): more sources = higher confidence
        source_count = len(sources)
        count_factor = min(1.0, source_count / 5.0)

        # Authority factor: weighted by best source authority
        authority_scores = {
            SourceAuthority.ACADEMIC: 1.0,
            SourceAuthority.GOVERNMENT: 0.9,
            SourceAuthority.MEDIA: 0.7,
            SourceAuthority.OTHER: 0.4,
        }
        best_authority = max(
            (authority_scores.get(s.authority, 0.3) for s in sources),
            default=0.3,
        )

        # Response text signal factor
        text_factor = 0.5  # Neutral default
        if response and response.text:
            text_lower = response.text.lower()
            # Positive signals
            if any(
                w in text_lower
                for w in ["verified", "confirmed", "accurate", "correct", "true"]
            ):
                text_factor = 0.9
            # Negative signals
            elif any(
                w in text_lower
                for w in ["unverified", "false", "inaccurate", "incorrect", "no evidence"]
            ):
                text_factor = 0.2
            # Ambiguous signals
            elif any(
                w in text_lower
                for w in ["partially", "debated", "disputed", "conflicting"]
            ):
                text_factor = 0.5

        # Weighted combination
        confidence = (
            0.4 * count_factor + 0.3 * best_authority + 0.3 * text_factor
        )

        return round(min(1.0, max(0.0, confidence)), 2)

    def _unverified_result(
        self,
        claim: FactualClaim,
        elapsed_ms: float,
        error_msg: str,
    ) -> VerificationResult:
        """Create a graceful-degradation result when verification fails.

        Requirements: 8.3 (mark unverified on failure).
        """
        return VerificationResult(
            claim=claim,
            status=VerificationStatus.ERROR,
            verified=False,
            confidence=0.0,
            sources=[],
            verification_time_ms=elapsed_ms,
            error=error_msg,
        )
