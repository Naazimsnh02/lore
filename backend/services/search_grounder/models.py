"""Data models for the Search Grounder service.

Design reference: LORE design.md, Section 6 – Search Grounder.
Requirements: 8.1–8.6.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────


class ClaimImportance(str, Enum):
    """Importance level for a factual claim."""

    CRITICAL = "critical"
    SUPPORTING = "supporting"
    CONTEXTUAL = "contextual"


class SourceAuthority(str, Enum):
    """Authority level of a source (Req 8.5)."""

    ACADEMIC = "academic"
    GOVERNMENT = "government"
    MEDIA = "media"
    OTHER = "other"


class VerificationStatus(str, Enum):
    """Outcome of a fact verification attempt."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    CONFLICTING = "conflicting"
    ERROR = "error"


# ── Request models ────────────────────────────────────────────────────────────


class DocumentaryContext(BaseModel):
    """Context for a documentary session, used to scope fact verification."""

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    location_name: Optional[str] = None
    topic: Optional[str] = None
    historical_period: Optional[str] = None
    mode: Optional[str] = None  # sight / voice / lore


class FactualClaim(BaseModel):
    """A single factual claim to verify.

    Design reference: FactualClaim interface in design.md §6.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: str = Field(..., description="The factual claim text to verify")
    context: Optional[DocumentaryContext] = None
    importance: ClaimImportance = Field(
        default=ClaimImportance.SUPPORTING,
        description="How critical this claim is to the documentary",
    )


# ── Response models ───────────────────────────────────────────────────────────


class Source(BaseModel):
    """A source citation for a verified fact.

    Design reference: Source interface in design.md §6.
    Requirements: 8.2 (provide source citations), 8.5 (prioritize authoritative).
    """

    url: str = Field(..., description="URL of the source")
    title: str = Field(default="", description="Title of the source page")
    authority: SourceAuthority = Field(
        default=SourceAuthority.OTHER,
        description="Authority level of the source",
    )
    relevance: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Relevance score 0-1"
    )
    excerpt: str = Field(default="", description="Relevant excerpt from the source")


class ConflictReport(BaseModel):
    """Report of conflicting information across sources (Req 8.6)."""

    claim_text: str
    conflicting_sources: list[Source] = Field(default_factory=list)
    summary: str = Field(
        default="", description="Summary of the conflicting viewpoints"
    )


class PerspectiveSet(BaseModel):
    """Multiple perspectives on a conflicting claim (Req 8.6)."""

    claim_text: str
    perspectives: list[Source] = Field(default_factory=list)
    synthesis: str = Field(
        default="",
        description="Synthesised summary presenting multiple perspectives",
    )


class VerificationResult(BaseModel):
    """Result of verifying a single factual claim.

    Design reference: VerificationResult interface in design.md §6.
    Requirements: 8.1 (verify), 8.2 (citations), 8.3 (mark unverified), 8.6 (conflicts).
    """

    claim: FactualClaim
    status: VerificationStatus = Field(
        default=VerificationStatus.UNVERIFIED,
        description="Outcome of the verification",
    )
    verified: bool = Field(default=False, description="Whether the claim was verified")
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Verification confidence 0-1"
    )
    sources: list[Source] = Field(
        default_factory=list, description="Sources supporting the claim"
    )
    alternative_perspectives: list[Source] = Field(
        default_factory=list,
        description="Sources with alternative/conflicting viewpoints",
    )
    verification_time_ms: float = Field(
        default=0.0, description="Time taken to verify in milliseconds"
    )
    error: Optional[str] = Field(
        default=None, description="Error message if verification failed"
    )


# ── Exceptions ────────────────────────────────────────────────────────────────


class SearchGrounderError(Exception):
    """Base exception for Search Grounder errors."""


class SearchGrounderTimeoutError(SearchGrounderError):
    """Raised when a search grounding request times out."""


class SearchGrounderAPIError(SearchGrounderError):
    """Raised when the Google Search Grounding API returns an error."""
