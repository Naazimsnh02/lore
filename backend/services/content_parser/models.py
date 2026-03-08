"""Documentary Content Format (DCF) data models.

Defines the formal grammar for structured documentary content representation.
All content types have strongly-typed Pydantic models that map losslessly
to/from the flat ContentElement used by the orchestrator.

Grammar specification (Requirement 28.1):

  DCF_STREAM  := JSON object {
    "version"    : "1.0",
    "stream_id"  : string,
    "request_id" : string,
    "session_id" : string,
    "mode"       : "sight" | "voice" | "lore",
    "started_at" : float,
    "completed_at": float | null,
    "error"      : string | null,
    "elements"   : DCF_ELEMENT[]
  }

  DCF_ELEMENT := JSON object {
    "version"     : "1.0",
    "element_id"  : string,
    "sequence_id" : integer >= 0,
    "timestamp"   : float >= 0,
    "type"        : ELEMENT_TYPE,
    "content"     : TYPED_CONTENT
  }

  ELEMENT_TYPE     := "narration" | "video" | "illustration" | "fact" | "transition"

  NARRATION_CONTENT := {
    "transcript"  : string,          -- required, maps to narration_text
    "audio_data"  : string | null,   -- base64 LINEAR16 PCM
    "audio_url"   : string,          -- Cloud Storage URL
    "duration"    : float >= 0,      -- seconds, maps to audio_duration
    "language"    : string,          -- ISO 639-1
    "tone"        : string,          -- maps to emotional_tone
    "depth_level" : string           -- explorer|scholar|expert
  }

  VIDEO_CONTENT := {
    "video_url"        : string,
    "thumbnail_url"    : string,
    "duration"         : float >= 0,  -- maps to video_duration
    "resolution"       : string,
    "has_native_audio" : bool,
    "scene_description": string
  }

  ILLUSTRATION_CONTENT := {
    "image_url"          : string,
    "image_data"         : string | null,  -- base64 PNG
    "caption"            : string,
    "visual_style"       : string,
    "concept_description": string
  }

  FACT_CONTENT := {
    "claim"                   : string,   -- required, maps to claim_text
    "verified"                : bool,
    "sources"                 : SOURCE_CITATION[],
    "confidence"              : float [0,1],
    "alternative_perspectives": string[]
  }

  SOURCE_CITATION := {
    "title"     : string,
    "url"       : string,
    "authority" : "academic" | "government" | "media" | "other",
    "excerpt"   : string
  }

  TRANSITION_CONTENT := {
    "transition_type" : "scene_change"|"topic_shift"|"branch_enter"|"branch_exit",
    "message"         : string
  }

Requirements: 28.1
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

DCFVersion = Literal["1.0"]

VALID_AUTHORITIES = {"academic", "government", "media", "other"}
VALID_TRANSITION_TYPES = {"scene_change", "topic_shift", "branch_enter", "branch_exit"}


# ── Typed content models ──────────────────────────────────────────────────────


class NarrationContent(BaseModel):
    """Typed content block for NARRATION elements (Requirement 28.1)."""

    transcript: str = Field(description="Narration text / transcript")
    audio_data: Optional[str] = Field(
        default=None, description="Base64-encoded LINEAR16 PCM audio"
    )
    audio_url: str = Field(default="", description="Cloud Storage URL for audio file")
    duration: float = Field(default=0.0, ge=0.0, description="Duration in seconds")
    language: str = Field(default="en", description="ISO 639-1 language code")
    tone: str = Field(default="neutral", description="Emotional tone identifier")
    depth_level: str = Field(default="explorer", description="explorer|scholar|expert")


class VideoContent(BaseModel):
    """Typed content block for VIDEO elements (Requirement 28.1)."""

    video_url: str = Field(default="", description="Cloud Storage URL for video file")
    thumbnail_url: str = Field(default="", description="Thumbnail image URL")
    duration: float = Field(default=0.0, ge=0.0, description="Duration in seconds")
    resolution: str = Field(default="1080p", description="Video resolution")
    has_native_audio: bool = Field(default=False, description="Includes native audio")
    scene_description: str = Field(default="", description="Description of scene content")


class IllustrationContent(BaseModel):
    """Typed content block for ILLUSTRATION elements (Requirement 28.1)."""

    image_url: str = Field(default="", description="Cloud Storage URL for image file")
    image_data: Optional[str] = Field(
        default=None, description="Base64-encoded PNG image"
    )
    caption: str = Field(default="", description="Illustration caption")
    visual_style: str = Field(default="illustrated", description="Visual style identifier")
    concept_description: str = Field(default="", description="What the illustration depicts")


class SourceCitation(BaseModel):
    """A single source citation within a FACT element (Requirement 28.1)."""

    title: str = Field(default="", description="Source title")
    url: str = Field(default="", description="Source URL")
    authority: str = Field(
        default="other",
        description="Authority level: academic|government|media|other",
    )
    excerpt: str = Field(default="", description="Relevant excerpt from the source")

    @field_validator("authority")
    @classmethod
    def validate_authority(cls, v: str) -> str:
        if v not in VALID_AUTHORITIES:
            raise ValueError(
                f"authority must be one of {sorted(VALID_AUTHORITIES)}, got '{v}'"
            )
        return v


class FactContent(BaseModel):
    """Typed content block for FACT elements (Requirement 28.1)."""

    claim: str = Field(description="The factual claim being presented")
    verified: bool = Field(default=False, description="Whether claim was verified")
    sources: list[SourceCitation] = Field(
        default_factory=list, description="Authoritative source citations"
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Verification confidence [0, 1]"
    )
    alternative_perspectives: list[str] = Field(
        default_factory=list, description="Alternative viewpoints on this claim"
    )


class TransitionContent(BaseModel):
    """Typed content block for TRANSITION elements (Requirement 28.1)."""

    transition_type: str = Field(
        default="scene_change",
        description="scene_change|topic_shift|branch_enter|branch_exit",
    )
    message: str = Field(default="", description="Optional human-readable transition message")

    @field_validator("transition_type")
    @classmethod
    def validate_transition_type(cls, v: str) -> str:
        if v not in VALID_TRANSITION_TYPES:
            raise ValueError(
                f"transition_type must be one of {sorted(VALID_TRANSITION_TYPES)}, got '{v}'"
            )
        return v


# Union type for typed content — discriminated by element type at parse time
TypedContent = Union[
    NarrationContent,
    VideoContent,
    IllustrationContent,
    FactContent,
    TransitionContent,
]


# ── DCF element and stream wrappers ───────────────────────────────────────────


class DCFElement(BaseModel):
    """A single element in the Documentary Content Format (DCF).

    This is the canonical serialised representation of a ContentElement.
    The `content` field holds a typed sub-model appropriate for `type`.

    Requirements: 28.1, 28.2, 28.3, 28.4
    """

    version: DCFVersion = "1.0"
    element_id: str = Field(description="Unique element identifier")
    sequence_id: int = Field(ge=0, description="Position in stream")
    timestamp: float = Field(ge=0.0, description="Unix timestamp when element was created")
    type: str = Field(
        description="narration | video | illustration | fact | transition"
    )
    content: Union[
        NarrationContent,
        VideoContent,
        IllustrationContent,
        FactContent,
        TransitionContent,
    ] = Field(description="Type-specific content block")

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        valid = {"narration", "video", "illustration", "fact", "transition"}
        if v not in valid:
            raise ValueError(f"type must be one of {sorted(valid)}, got '{v}'")
        return v


class DCFStream(BaseModel):
    """A full documentary stream in DCF format.

    Wraps a DocumentaryStream for storage and transmission.
    Requirements: 28.3, 28.4
    """

    version: DCFVersion = "1.0"
    stream_id: str
    request_id: str = ""
    session_id: str = ""
    mode: str = Field(description="sight | voice | lore")
    started_at: float
    completed_at: Optional[float] = None
    error: Optional[str] = None
    elements: list[DCFElement] = Field(default_factory=list)

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        valid = {"sight", "voice", "lore"}
        if v not in valid:
            raise ValueError(f"mode must be one of {sorted(valid)}, got '{v}'")
        return v
