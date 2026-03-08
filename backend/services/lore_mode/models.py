"""Data models for LoreMode handler and FusionEngine.

Design reference: LORE design.md, LoreMode Implementation (Fusion) section.
Requirements: 4.1–4.6.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────


class LoreModeEvent(str, Enum):
    """Events emitted by the LoreMode handler."""

    DOCUMENTARY_TRIGGER = "documentary_trigger"
    ALTERNATE_HISTORY = "alternate_history"
    CONTEXT_FUSED = "context_fused"
    CAMERA_ONLY = "camera_only"
    VOICE_ONLY = "voice_only"
    LOAD_DEGRADED = "load_degraded"
    ERROR = "error"


class ConnectionType(str, Enum):
    """Types of cross-modal connections between visual and verbal contexts."""

    HISTORICAL = "historical"
    CULTURAL = "cultural"
    GEOGRAPHIC = "geographic"
    TEMPORAL = "temporal"
    THEMATIC = "thematic"


class ProcessingPriority(str, Enum):
    """Processing priority levels under load (Req 4.6)."""

    NORMAL = "normal"
    VOICE_PRIORITY = "voice_priority"
    DEGRADED = "degraded"


# ── Data models ──────────────────────────────────────────────────────────────


class ProcessingLoad(BaseModel):
    """Tracks processing load to determine priority (Req 4.6)."""

    camera_latency_ms: float = Field(default=0.0, ge=0.0)
    voice_latency_ms: float = Field(default=0.0, ge=0.0)
    concurrent_tasks: int = Field(default=0, ge=0)
    camera_frame_rate: float = Field(
        default=1.0, ge=0.0, description="Current camera fps"
    )
    timestamp: float = Field(default_factory=time.time)

    @property
    def is_overloaded(self) -> bool:
        """True when processing is falling behind.

        Thresholds: camera > 3s or voice > 2s or > 10 concurrent tasks.
        """
        return (
            self.camera_latency_ms > 3000
            or self.voice_latency_ms > 2000
            or self.concurrent_tasks > 10
        )


class CrossModalConnection(BaseModel):
    """A semantic connection found between location and topic."""

    type: ConnectionType
    description: str = Field(default="")
    relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    keywords: list[str] = Field(default_factory=list)


class FusedContext(BaseModel):
    """Unified documentary context from fused camera + voice inputs.

    This is the primary output of FusionEngine.fuse().  It carries visual,
    verbal, and GPS contexts merged into a single rich structure that the
    Orchestrator uses to drive documentary generation.
    """

    mode: str = Field(default="lore")

    # Visual context (from SightMode)
    place_id: str = Field(default="")
    place_name: str = Field(default="")
    place_description: str = Field(default="")
    place_types: list[str] = Field(default_factory=list)
    latitude: float = Field(default=0.0)
    longitude: float = Field(default=0.0)
    formatted_address: str = Field(default="")
    visual_description: str = Field(default="")
    visual_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    # Verbal context (from VoiceMode)
    topic: str = Field(default="")
    original_query: str = Field(default="")
    language: str = Field(default="en")
    verbal_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    # GPS context
    gps_latitude: float = Field(default=0.0)
    gps_longitude: float = Field(default=0.0)
    gps_accuracy: float = Field(default=0.0, ge=0.0)

    # Fusion results
    fused_topic: str = Field(
        default="",
        description="Combined topic from visual + verbal + GPS context",
    )
    cross_modal_connections: list[CrossModalConnection] = Field(
        default_factory=list
    )

    # Advanced feature flags
    enable_alternate_history: bool = Field(default=True)
    enable_historical_characters: bool = Field(default=True)

    # Frame data for style reference (Veo, illustrations)
    frame_data: Optional[bytes] = None

    # Processing metadata
    processing_priority: ProcessingPriority = Field(
        default=ProcessingPriority.NORMAL
    )

    model_config = {"arbitrary_types_allowed": True}


class LoreModeResponse(BaseModel):
    """Response from LoreMode multimodal input processing."""

    event: LoreModeEvent
    fused_context: Optional[FusedContext] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)
