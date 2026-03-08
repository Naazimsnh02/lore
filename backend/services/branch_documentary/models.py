"""Data models for the Branch Documentary system.

Design reference: LORE design.md, Branch Documentary section.
Requirements: 13.1–13.6.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Constants ────────────────────────────────────────────────────────────────

MAX_BRANCH_DEPTH: int = 3  # Req 13.4


# ── Models ───────────────────────────────────────────────────────────────────


class BranchStackEntry(BaseModel):
    """One frame on the branch navigation stack."""

    branch_id: str
    parent_branch_id: Optional[str] = None
    topic: str
    depth: int = Field(ge=0)
    stream_position: float = 0.0


class BranchDocumentaryContext(BaseModel):
    """Context passed to the Orchestrator for branch documentary generation.

    Extends the parent context with branch-specific fields.
    """

    branch_id: str
    parent_branch_id: Optional[str] = None
    topic: str
    depth: int = Field(ge=1)
    mode: str = "voice"
    language: str = "en"
    depth_dial: str = "explorer"
    session_id: str = ""
    user_id: str = ""
    previous_topics: list[str] = Field(default_factory=list)


class BranchDocumentary(BaseModel):
    """Result of creating a branch documentary."""

    branch_id: str
    context: BranchDocumentaryContext
    stream: Any = None  # DocumentaryStream — typed as Any to avoid circular import


# ── Exceptions ───────────────────────────────────────────────────────────────


class BranchDocumentaryError(Exception):
    """Base exception for Branch Documentary operations."""


class BranchDepthExceeded(BranchDocumentaryError):
    """Raised when a branch creation exceeds the maximum depth limit (Req 13.4)."""


class NoBranchToReturn(BranchDocumentaryError):
    """Raised when return_to_parent is called at the root level."""
