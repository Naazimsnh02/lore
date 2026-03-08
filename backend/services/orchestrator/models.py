"""Data models for the Orchestrator service.

Design reference: LORE design.md, Section 2 – Orchestrator.
Requirements: 21.1–21.5, 5.1, 5.3.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────


class Mode(str, Enum):
    """Operating modes (design.md §1)."""

    SIGHT = "sight"
    VOICE = "voice"
    LORE = "lore"


class ContentElementType(str, Enum):
    """Types of content in a documentary stream (design.md §2)."""

    NARRATION = "narration"
    VIDEO = "video"
    ILLUSTRATION = "illustration"
    FACT = "fact"
    TRANSITION = "transition"


class TaskStatus(str, Enum):
    """Status of an individual generation task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


# ── Request models ────────────────────────────────────────────────────────────


class DocumentaryRequest(BaseModel):
    """Inbound request to generate documentary content (design.md §2).

    Created by the WebSocket Gateway from client messages and forwarded
    to the Orchestrator for processing.
    """

    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    user_id: str
    session_id: str
    mode: Mode
    # SightMode inputs
    camera_frame: Optional[str] = Field(
        default=None, description="Base64-encoded camera frame (JPEG)"
    )
    gps_location: Optional[dict[str, float]] = Field(
        default=None, description="{latitude, longitude}"
    )
    # VoiceMode inputs
    voice_topic: Optional[str] = Field(
        default=None, description="Transcribed voice topic"
    )
    voice_audio: Optional[str] = Field(
        default=None, description="Base64-encoded audio (LINEAR16 PCM)"
    )
    # Configuration
    depth_dial: str = Field(default="explorer", description="explorer|scholar|expert")
    language: str = Field(default="en", description="ISO 639-1 language code")
    # Context
    previous_topics: list[str] = Field(default_factory=list)
    branch_parent_id: Optional[str] = Field(
        default=None, description="Parent branch ID for branch documentaries"
    )
    branch_topic: Optional[str] = Field(
        default=None, description="Topic for branch documentary"
    )
    timestamp: float = Field(default_factory=time.time)


# ── Content element ──────────────────────────────────────────────────────────


class ContentElement(BaseModel):
    """A single element in the documentary stream.

    Sent to the client via WebSocket as part of the interleaved stream.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    type: ContentElementType
    sequence_id: int = Field(default=0, description="Position in stream")
    timestamp: float = Field(default_factory=time.time)

    # Narration fields
    narration_text: Optional[str] = None
    audio_data: Optional[str] = Field(
        default=None, description="Base64-encoded audio"
    )
    audio_duration: float = Field(default=0.0)
    emotional_tone: Optional[str] = None

    # Illustration fields
    image_url: Optional[str] = None
    image_data: Optional[str] = Field(
        default=None, description="Base64-encoded PNG"
    )
    caption: Optional[str] = None
    visual_style: Optional[str] = None

    # Fact fields
    claim_text: Optional[str] = None
    verified: Optional[bool] = None
    confidence: Optional[float] = None
    sources: list[dict[str, Any]] = Field(default_factory=list)

    # Video fields (populated later by Veo — Task 28)
    video_url: Optional[str] = None
    video_duration: float = Field(default=0.0)

    # Transition fields
    transition_text: Optional[str] = None


# ── Stream and result models ─────────────────────────────────────────────────


class DocumentaryStream(BaseModel):
    """An assembled documentary stream ready for delivery to the client."""

    stream_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    request_id: str = Field(default="")
    session_id: str = Field(default="")
    mode: Mode = Mode.SIGHT
    elements: list[ContentElement] = Field(default_factory=list)
    started_at: float = Field(default_factory=time.time)
    completed_at: Optional[float] = None
    error: Optional[str] = None

    @property
    def total_duration(self) -> float:
        """Sum of narration and video durations."""
        return sum(
            e.audio_duration + e.video_duration for e in self.elements
        )


class TaskFailure(BaseModel):
    """Records a task failure for diagnostics."""

    task_name: str
    attempt: int = 1
    error: str
    timestamp: float = Field(default_factory=time.time)


class WorkflowResult(BaseModel):
    """Intermediate result from a single workflow step."""

    task_name: str
    success: bool = True
    data: Any = None
    error: Optional[str] = None
    duration_ms: float = 0.0


# ── Exceptions ───────────────────────────────────────────────────────────────


class OrchestratorError(Exception):
    """Base exception for Orchestrator errors."""


class WorkflowError(OrchestratorError):
    """Raised when a workflow step fails after all retries."""


class ModeTransitionError(OrchestratorError):
    """Raised when an invalid mode transition is attempted."""
