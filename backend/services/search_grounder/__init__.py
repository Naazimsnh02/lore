"""Search Grounder – Fact verification via Google Search Grounding for LORE.

Design reference: LORE design.md, Section 6 – Search Grounder.
Requirements: 8.1–8.6.
Property: 11 (Fact Verification Completeness).

Uses Google Search Grounding API via the google-genai SDK to verify
factual claims and provide authoritative source citations.
"""

from .grounder import SearchGrounder
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

__all__ = [
    "SearchGrounder",
    "ClaimImportance",
    "ConflictReport",
    "DocumentaryContext",
    "FactualClaim",
    "PerspectiveSet",
    "Source",
    "SourceAuthority",
    "VerificationResult",
    "VerificationStatus",
]
