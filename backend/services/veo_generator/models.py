"""Data models for the Veo Generator service.

Design reference: LORE design.md, Section 6 – Veo Generator.
Requirements: 6.1–6.7.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────


class VideoStyle(str, Enum):
    """Video generation visual style."""

    CINEMATIC = "cinematic"
    DOCUMENTARY = "documentary"
    HISTORICAL = "historical"
    SPECULATIVE = "speculative"


class VideoResolution(str, Enum):
    """Supported video resolutions (Req 6.5: minimum 1080p)."""

    HD_720P = "720p"
    FHD_1080P = "1080p"
    UHD_4K = "4k"


class VideoStatus(str, Enum):
    """Status of a video generation operation."""

    PENDING = "pending"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class AspectRatio(str, Enum):
    """Supported aspect ratios for Veo 3.1."""

    LANDSCAPE = "16:9"
    PORTRAIT = "9:16"


# ── Request models ────────────────────────────────────────────────────────────


class SceneDescription(BaseModel):
    """Describes a scene for video generation.

    Design reference: SceneDescription interface in design.md §6.
    """

    prompt: str = Field(..., description="Text description of the scene to generate")
    duration: int = Field(
        default=8,
        ge=4,
        le=8,
        description="Clip duration in seconds (Veo supports 4, 6, or 8 per clip)",
    )
    style: VideoStyle = Field(default=VideoStyle.CINEMATIC)
    context: Optional[DocumentaryContext] = Field(
        default=None, description="Documentary context for style adaptation"
    )
    reference_image: Optional[str] = Field(
        default=None,
        description="GCS URI or base64-encoded reference image for visual continuity",
    )
    reference_image_mime_type: Optional[str] = Field(
        default=None, description="MIME type of reference image (e.g. image/png)"
    )
    negative_prompt: Optional[str] = Field(
        default=None, description="What to avoid in the generated video"
    )
    aspect_ratio: AspectRatio = Field(default=AspectRatio.LANDSCAPE)
    generate_audio: bool = Field(
        default=True, description="Include native audio (Req 6.3)"
    )
    resolution: VideoResolution = Field(
        default=VideoResolution.FHD_1080P,
        description="Target resolution (Req 6.5: minimum 1080p)",
    )


class DocumentaryContext(BaseModel):
    """Context about the documentary session for video generation."""

    session_id: str = Field(..., description="Current session ID")
    mode: str = Field(default="sight", description="Operating mode: sight, voice, lore")
    topic: Optional[str] = Field(default=None, description="Current documentary topic")
    place_name: Optional[str] = Field(default=None, description="Recognised place name")
    place_types: list[str] = Field(default_factory=list)
    historical_period: Optional[str] = Field(default=None)
    language: str = Field(default="en")


# Allow forward-ref resolution
SceneDescription.model_rebuild()


# ── Response models ───────────────────────────────────────────────────────────


class VideoClip(BaseModel):
    """A generated video clip (design.md §6).

    Requirements: 6.2 (8–60s duration via chaining), 6.3 (native audio),
                  6.5 (minimum 1080p resolution).
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    url: Optional[str] = Field(
        default=None, description="Cloud Storage URL or signed URL"
    )
    gcs_uri: Optional[str] = Field(
        default=None, description="GCS URI (gs://bucket/path)"
    )
    duration: float = Field(default=0.0, description="Clip duration in seconds")
    resolution: VideoResolution = Field(default=VideoResolution.FHD_1080P)
    has_native_audio: bool = Field(default=True, description="Whether clip has audio")
    thumbnail_url: Optional[str] = Field(default=None)
    style: VideoStyle = Field(default=VideoStyle.CINEMATIC)
    generation_time_ms: float = Field(default=0.0, description="Generation wall time")
    prompt: str = Field(default="", description="Prompt used to generate this clip")
    scene_index: int = Field(default=0, description="Position in a scene chain")
    timestamp: float = Field(default_factory=time.time)


class VideoGenerationResult(BaseModel):
    """Full result from a video generation request."""

    clip: Optional[VideoClip] = None
    stored: bool = Field(default=False, description="Whether stored in Media Store")
    media_id: Optional[str] = Field(default=None, description="Media Store ID if stored")
    media_url: Optional[str] = Field(default=None, description="Signed URL from Media Store")
    error: Optional[str] = Field(default=None, description="Error message if failed")
    status: VideoStatus = Field(default=VideoStatus.COMPLETED)


class SceneChainResult(BaseModel):
    """Result from generating a chain of scenes (Req 6.4: visual continuity)."""

    clips: list[VideoClip] = Field(default_factory=list)
    total_duration: float = Field(default=0.0)
    visual_continuity_score: float = Field(
        default=0.0, description="0.0–1.0 continuity assessment"
    )
    errors: list[str] = Field(default_factory=list)


# ── Exceptions ────────────────────────────────────────────────────────────────


class VeoError(Exception):
    """Base exception for Veo Generator errors."""


class VeoTimeoutError(VeoError):
    """Raised when video generation exceeds the timeout deadline."""


class VeoGenerationError(VeoError):
    """Raised when the model fails to generate a video."""


class VeoQualityError(VeoError):
    """Raised when a generated clip doesn't meet quality constraints."""
