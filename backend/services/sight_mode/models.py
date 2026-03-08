"""Data models for SightMode handler.

Design reference: LORE design.md, SightMode Implementation.
Requirements: 2.1–2.6.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class SightModeEvent(str, Enum):
    """Events emitted by the SightMode handler."""

    DOCUMENTARY_TRIGGER = "documentary_trigger"
    VOICE_CLARIFICATION = "voice_clarification"
    FLASH_SUGGESTION = "flash_suggestion"
    FRAME_BUFFERED = "frame_buffered"
    RECOGNITION_FAILED = "recognition_failed"


class FrameMetadata(BaseModel):
    """Metadata for a single camera frame."""

    timestamp: float = Field(default_factory=time.time, description="Capture timestamp (epoch)")
    brightness: float = Field(default=0.0, ge=0.0, le=255.0, description="Average pixel brightness 0-255")
    quality_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Overall frame quality 0-1")
    width: int = Field(default=0, ge=0, description="Frame width in pixels")
    height: int = Field(default=0, ge=0, description="Frame height in pixels")
    mime_type: str = Field(default="image/jpeg", description="Image MIME type")


class BufferedFrame(BaseModel):
    """A camera frame stored in the FrameBuffer."""

    model_config = {"arbitrary_types_allowed": True}

    data: bytes
    metadata: FrameMetadata


class SightModeResponse(BaseModel):
    """Response from SightMode frame processing."""

    event: SightModeEvent
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)


class DocumentaryContext(BaseModel):
    """Context for triggering documentary generation from SightMode."""

    model_config = {"arbitrary_types_allowed": True}

    mode: str = "sight"
    place_id: str = Field(default="", description="Google Places unique ID")
    place_name: str = Field(default="", description="Human-readable place name")
    place_description: str = Field(default="", description="Editorial summary")
    place_types: list[str] = Field(default_factory=list)
    latitude: float = Field(default=0.0)
    longitude: float = Field(default=0.0)
    formatted_address: str = Field(default="")
    visual_description: str = Field(default="", description="Gemini scene description")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    frame_data: Optional[bytes] = None
