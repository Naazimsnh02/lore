"""Data models for the Nano Illustrator service.

Design reference: LORE design.md, Section 5 – Nano Illustrator.
Requirements: 7.1–7.6.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────


class VisualStyle(str, Enum):
    """Illustration visual style (design.md §5)."""

    PHOTOREALISTIC = "photorealistic"
    ILLUSTRATED = "illustrated"
    HISTORICAL = "historical"
    TECHNICAL = "technical"
    ARTISTIC = "artistic"


class DepthLevel(str, Enum):
    """Content complexity dial (Explorer/Scholar/Expert)."""

    EXPLORER = "explorer"
    SCHOLAR = "scholar"
    EXPERT = "expert"


# ── Request models ────────────────────────────────────────────────────────────


class ConceptDescription(BaseModel):
    """Describes a concept to illustrate.

    Design reference: ConceptDescription interface in design.md §5.
    """

    prompt: str = Field(..., description="Text description of the concept to illustrate")
    context: Optional[DocumentaryContext] = Field(
        default=None, description="Documentary context for style adaptation"
    )
    historical_period: Optional[str] = Field(
        default=None,
        description="Historical period for period-appropriate style (e.g. 'Ancient Rome', '1920s')",
    )
    complexity: DepthLevel = Field(default=DepthLevel.EXPLORER)
    aspect_ratio: str = Field(
        default="1:1",
        description="Aspect ratio for the illustration",
    )
    style_override: Optional[VisualStyle] = Field(
        default=None,
        description="Explicit style override; if None, determined from context",
    )


class DocumentaryContext(BaseModel):
    """Context about the documentary session for style determination."""

    session_id: str = Field(..., description="Current session ID")
    mode: str = Field(default="sight", description="Operating mode: sight, voice, lore")
    topic: Optional[str] = Field(default=None, description="Current documentary topic")
    place_name: Optional[str] = Field(default=None, description="Recognised place name")
    place_types: list[str] = Field(default_factory=list, description="Google Places type tags")
    historical_period: Optional[str] = Field(default=None)
    previous_styles: list[VisualStyle] = Field(
        default_factory=list, description="Styles used earlier in the session"
    )
    language: str = Field(default="en")


# Allow forward-ref resolution
ConceptDescription.model_rebuild()


# ── Response models ───────────────────────────────────────────────────────────


class Illustration(BaseModel):
    """A generated illustration (design.md §5).

    Requirements: 7.2 (< 2 s), 7.3 (≥ 1024×1024), 7.6 (style consistency).
    """

    model_config = {"arbitrary_types_allowed": True}

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    image_data: Optional[bytes] = Field(
        default=None, description="Raw image bytes (PNG)"
    )
    mime_type: str = Field(default="image/png")
    url: Optional[str] = Field(
        default=None, description="Cloud Storage signed URL after storage"
    )
    resolution: str = Field(default="1024x1024")
    style: VisualStyle = Field(default=VisualStyle.ILLUSTRATED)
    generation_time_ms: float = Field(
        default=0.0, description="Generation time in milliseconds"
    )
    caption: str = Field(default="", description="Auto-generated caption")
    concept_description: str = Field(default="", description="Original prompt used")
    timestamp: float = Field(default_factory=time.time)


class IllustrationResult(BaseModel):
    """Full result from an illustration generation request."""

    illustration: Illustration
    stored: bool = Field(default=False, description="Whether stored in Media Store")
    media_id: Optional[str] = Field(
        default=None, description="Media Store ID if stored"
    )
    media_url: Optional[str] = Field(
        default=None, description="Signed URL from Media Store"
    )
    error: Optional[str] = Field(default=None, description="Error message if failed")


# ── Exceptions ────────────────────────────────────────────────────────────────


class IllustrationError(Exception):
    """Base exception for Nano Illustrator errors."""


class IllustrationTimeoutError(IllustrationError):
    """Raised when illustration generation exceeds the 2-second deadline."""


class IllustrationGenerationError(IllustrationError):
    """Raised when the model fails to generate an image."""
