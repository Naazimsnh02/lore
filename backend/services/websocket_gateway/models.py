"""Pydantic models for WebSocket message types.

Covers the full client → server and server → client message protocol
defined in the LORE design document (API Specifications / WebSocket Protocol).
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

class OperatingMode(str, Enum):
    SIGHT = "sight"
    VOICE = "voice"
    LORE = "lore"


class DepthDial(str, Enum):
    EXPLORER = "explorer"
    SCHOLAR = "scholar"
    EXPERT = "expert"


class ContentType(str, Enum):
    NARRATION = "narration"
    VIDEO = "video"
    ILLUSTRATION = "illustration"
    FACT = "fact"
    TRANSITION = "transition"


class ComponentStatus(str, Enum):
    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    FAILED = "failed"


# ── Client → Server payloads ──────────────────────────────────────────────────

class ModeSelectPayload(BaseModel):
    mode: OperatingMode
    depthDial: DepthDial
    language: str


class CameraFramePayload(BaseModel):
    imageData: str  # base64-encoded JPEG
    timestamp: int
    gpsLocation: Optional[dict] = None  # {latitude: float, longitude: float}


class VoiceInputPayload(BaseModel):
    audioData: str  # base64-encoded LINEAR16 PCM
    sampleRate: int
    timestamp: int


class GPSUpdatePayload(BaseModel):
    latitude: float
    longitude: float
    accuracy: float  # metres
    timestamp: int


class BargeInPayload(BaseModel):
    audioData: str  # base64-encoded PCM
    streamPosition: float  # seconds into current documentary stream
    timestamp: int


class QueryPayload(BaseModel):
    query: str
    timestamp: int


class BranchRequestPayload(BaseModel):
    topic: str
    parentBranchId: str
    timestamp: int


class DepthDialChangePayload(BaseModel):
    newLevel: DepthDial
    timestamp: int


class ChronicleExportPayload(BaseModel):
    sessionId: str


class CharacterInteractionPayload(BaseModel):
    """Payload for historical character interactions (Req 12.3)."""
    action: str  # "accept" | "message" | "end"
    message: str = ""
    timestamp: int = 0


class ModeSwitchPayload(BaseModel):
    """Payload for switching modes during an active session (Req 1.6)."""
    targetMode: OperatingMode
    timestamp: int = 0


# ── Validated incoming message wrapper ────────────────────────────────────────

CLIENT_MESSAGE_TYPES = Literal[
    "mode_select",
    "mode_switch",
    "camera_frame",
    "voice_input",
    "gps_update",
    "barge_in",
    "query",
    "branch_request",
    "depth_dial_change",
    "chronicle_export",
    "character_interaction",
]


class ClientMessage(BaseModel):
    """Top-level envelope for all messages from the mobile client."""

    type: CLIENT_MESSAGE_TYPES
    payload: Any
    timestamp: Optional[int] = Field(
        default_factory=lambda: int(time.time() * 1000)
    )


# ── Server → Client payloads ──────────────────────────────────────────────────

class ComponentsStatus(BaseModel):
    narration: ComponentStatus = ComponentStatus.OPERATIONAL
    video: ComponentStatus = ComponentStatus.OPERATIONAL
    illustration: ComponentStatus = ComponentStatus.OPERATIONAL
    search: ComponentStatus = ComponentStatus.OPERATIONAL


class ErrorPayload(BaseModel):
    errorCode: str
    message: str
    degradedFunctionality: list[str] = Field(default_factory=list)
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))


class StatusPayload(BaseModel):
    activeMode: str
    componentsStatus: ComponentsStatus
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))


class ServerMessage(BaseModel):
    """Top-level envelope for all messages sent to the mobile client."""

    type: str
    payload: Any
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))


# ── Per-connection state ──────────────────────────────────────────────────────

class ConnectionInfo(BaseModel):
    """Metadata tracked per active WebSocket connection."""

    client_id: str
    user_id: str
    session_id: Optional[str] = None
    mode: Optional[OperatingMode] = None
    depth_dial: DepthDial = DepthDial.SCHOLAR
    language: str = "en"
    connected_at: float = Field(default_factory=time.time)
    last_seen: float = Field(default_factory=time.time)
