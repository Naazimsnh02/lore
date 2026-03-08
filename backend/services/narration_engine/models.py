"""Data models for the Narration Engine service.

Design reference: LORE design.md, Section 3 – Narration Engine.
Requirements: 3.1, 3.2, 5.2, 11.1–11.6.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class EmotionalTone(str, Enum):
    """Narration tone adapted to emotional context (Req 11.1–11.4)."""

    RESPECTFUL = "respectful"
    ENTHUSIASTIC = "enthusiastic"
    CONTEMPLATIVE = "contemplative"
    NEUTRAL = "neutral"


class DepthLevel(str, Enum):
    """Content complexity dial (Explorer/Scholar/Expert)."""

    EXPLORER = "explorer"
    SCHOLAR = "scholar"
    EXPERT = "expert"


class VoiceParameters(BaseModel):
    """Voice configuration applied to Gemini Live API speech output."""

    voice_name: str = Field(default="Kore", description="Gemini prebuilt voice name")
    speaking_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    pitch: float = Field(default=0.0, ge=-10.0, le=10.0)
    volume_gain_db: float = Field(default=0.0, ge=-10.0, le=10.0)
    pause_duration: float = Field(default=0.8, ge=0.0, le=5.0)
    vocabulary: str = Field(default="standard")


class NarrationSegment(BaseModel):
    """A single segment of narration script."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    text: str = Field(..., description="Narration text for this segment")
    duration: float = Field(default=0.0, ge=0.0, description="Estimated duration in seconds")
    tone: EmotionalTone = Field(default=EmotionalTone.NEUTRAL)
    timestamp: float = Field(default_factory=time.time)


class NarrationScript(BaseModel):
    """Complete narration script with multiple segments."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    segments: list[NarrationSegment] = Field(default_factory=list)
    total_duration: float = Field(default=0.0, ge=0.0, description="Sum of segment durations")
    language: str = Field(default="en", description="ISO 639-1 language code")
    depth_level: DepthLevel = Field(default=DepthLevel.EXPLORER)
    tone: EmotionalTone = Field(default=EmotionalTone.NEUTRAL)


class AudioChunk(BaseModel):
    """A chunk of PCM audio data from the Live API."""

    model_config = {"arbitrary_types_allowed": True}

    data: bytes = Field(..., description="Raw PCM audio bytes (24 kHz, 16-bit, mono)")
    sequence: int = Field(default=0, ge=0, description="Chunk sequence number")
    timestamp: float = Field(default_factory=time.time)
    is_final: bool = Field(default=False, description="True if this is the last chunk")


class NarrationResult(BaseModel):
    """Result from a complete narration generation pass."""

    script: NarrationScript
    audio_url: Optional[str] = Field(default=None, description="Cloud Storage URL for assembled audio")
    transcript: str = Field(default="", description="Full transcript text")
    duration: float = Field(default=0.0, ge=0.0, description="Total audio duration in seconds")
    language: str = Field(default="en")
    tone: EmotionalTone = Field(default=EmotionalTone.NEUTRAL)
    depth_level: DepthLevel = Field(default=DepthLevel.EXPLORER)
    chunk_count: int = Field(default=0, ge=0, description="Number of audio chunks generated")


class NarrationContext(BaseModel):
    """Context passed to the narration engine for script generation."""

    mode: str = Field(default="sight", description="Operating mode: sight, voice, lore")
    topic: Optional[str] = Field(default=None, description="Voice topic or extracted theme")
    place_name: Optional[str] = Field(default=None, description="Recognised place name")
    place_description: Optional[str] = Field(default=None, description="Place editorial summary")
    place_types: list[str] = Field(default_factory=list, description="Google Places type tags")
    visual_description: Optional[str] = Field(default=None, description="Gemini scene description")
    latitude: float = Field(default=0.0)
    longitude: float = Field(default=0.0)
    language: str = Field(default="en")
    depth_level: DepthLevel = Field(default=DepthLevel.EXPLORER)
    session_id: Optional[str] = Field(default=None)
    user_id: Optional[str] = Field(default=None)
    previous_topics: list[str] = Field(default_factory=list, description="Earlier topics in session")
    custom_instructions: Optional[str] = Field(default=None, description="User-specified style guidance")
