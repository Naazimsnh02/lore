"""Data models for the Mode Switch Manager.

Design reference: LORE design.md, Section 1 – Core Mode Selection.
Requirements:
  1.6 — Mode switching during active sessions.
  1.7 — Preserve all previously generated content on mode switch.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class SwitchableMode(str, Enum):
    """Operating modes that support switching (mirrors orchestrator Mode)."""

    SIGHT = "sight"
    VOICE = "voice"
    LORE = "lore"


class PreservedContent(BaseModel):
    """Snapshot of content preserved across a mode transition (Req 1.7).

    All content IDs and counts are captured at the moment of the switch
    so the client and session memory can verify nothing was lost.
    """

    narration_count: int = 0
    illustration_count: int = 0
    video_count: int = 0
    fact_count: int = 0
    content_ids: list[str] = Field(default_factory=list)
    branch_ids: list[str] = Field(default_factory=list)
    total_duration_seconds: float = 0.0


class ModeSwitchRecord(BaseModel):
    """Persisted record of a single mode transition.

    Stored as a UserInteraction in session memory with
    interaction_type = MODE_SWITCH.
    """

    switch_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    from_mode: SwitchableMode
    to_mode: SwitchableMode
    timestamp: float = Field(default_factory=time.time)
    preserved: PreservedContent = Field(default_factory=PreservedContent)
    session_id: str = ""


class ModeSwitchContext(BaseModel):
    """Context passed to the orchestrator when a mode switch occurs.

    Carries enough information for the orchestrator to resume generation
    in the new mode while maintaining continuity.
    """

    session_id: str
    user_id: str
    from_mode: SwitchableMode
    to_mode: SwitchableMode
    preserved: PreservedContent
    previous_topics: list[str] = Field(default_factory=list)
    depth_dial: str = "explorer"
    language: str = "en"


class ModeSwitchResult(BaseModel):
    """Result returned to the caller after a successful mode switch."""

    switch_id: str
    from_mode: SwitchableMode
    to_mode: SwitchableMode
    preserved: PreservedContent
    session_id: str
    timestamp: float = Field(default_factory=time.time)
    transition_message: str = ""


class ModeSwitchError(Exception):
    """Raised when a mode switch fails."""
