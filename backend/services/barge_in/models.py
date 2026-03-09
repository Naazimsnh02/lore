"""Data models for the Barge-In Handler service.

Design reference: LORE design.md, Section 9 (Barge-In Handler).
Requirements: 19.1-19.6.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class InterjectionType(str, Enum):
    """Type of user interjection during documentary playback.
    
    Requirements:
    - 19.4: Questions must be answered before resuming
    - 19.5: Topic changes create branches or redirect main stream
    """
    QUESTION = "question"  # User asks a question about current content
    TOPIC_CHANGE = "topic_change"  # User wants to explore a different topic
    COMMAND = "command"  # User issues a system command (pause, resume, etc.)
    FOLLOW_UP = "follow_up"  # User adds context or continues current topic
    BRANCH_REQUEST = "branch_request"  # User explicitly requests a branch documentary


class ResumeAction(str, Enum):
    """Action to take after processing the interjection.
    
    Requirement 19.6: Resume from interruption point after addressing input.
    """
    CONTINUE = "continue"  # Resume from interruption point
    RESTART = "restart"  # Restart current segment
    BRANCH = "branch"  # Create branch documentary
    REDIRECT = "redirect"  # Change main documentary topic
    PAUSE = "pause"  # Keep paused (user command)


class Interruption(BaseModel):
    """Represents a user interruption during documentary playback.
    
    Requirement 19.1: Monitor for user voice input during documentary stream.
    Requirement 19.2: Pause within 200ms of speech detection.
    """
    timestamp: float = Field(
        default_factory=lambda: time.time(),
        description="Unix timestamp when interruption was detected"
    )
    audio_data: str = Field(
        ...,
        description="Base64-encoded PCM audio data of the interruption"
    )
    stream_position: float = Field(
        ...,
        description="Position in seconds where documentary was interrupted"
    )
    client_id: str = Field(
        ...,
        description="Client ID of the user who interrupted"
    )
    session_id: str = Field(
        default="",
        description="Session ID for context tracking"
    )


class InterjectionResponse(BaseModel):
    """Response to a user interjection.
    
    Requirements:
    - 19.3: Process interjection and respond appropriately
    - 19.4: Answer questions before resuming
    - 19.5: Handle topic changes via branch or redirect
    """
    type: InterjectionType = Field(
        ...,
        description="Classified type of the interjection"
    )
    content: Any = Field(
        default=None,
        description="Response content (answer, acknowledgment, etc.)"
    )
    resume_action: ResumeAction = Field(
        ...,
        description="Action to take after processing interjection"
    )
    resume_position: float = Field(
        ...,
        description="Stream position to resume from (seconds)"
    )
    transcription: str = Field(
        default="",
        description="Transcribed text of the interjection"
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence score for interjection classification"
    )
    branch_topic: Optional[str] = Field(
        default=None,
        description="Extracted topic for branch documentary (if applicable)"
    )
    processing_time_ms: float = Field(
        default=0.0,
        description="Time taken to process the interjection (milliseconds)"
    )


class BargeInResult(BaseModel):
    """Result of barge-in processing.
    
    Requirement 19.2: Acknowledge within 200ms.
    Requirement 19.6: Resume from interruption point.
    """
    acknowledged: bool = Field(
        default=True,
        description="Whether the interruption was acknowledged"
    )
    acknowledgment_time_ms: float = Field(
        ...,
        description="Time taken to acknowledge (must be < 200ms per Req 19.2)"
    )
    interjection_response: Optional[InterjectionResponse] = Field(
        default=None,
        description="Detailed response to the interjection"
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if processing failed"
    )


class PlaybackState(BaseModel):
    """Tracks the state of documentary playback for a client.
    
    Used internally by BargeInHandler to manage pause/resume.
    """
    client_id: str
    session_id: str
    is_playing: bool = True
    current_position: float = 0.0  # seconds
    paused_at: Optional[float] = None  # timestamp when paused
    last_segment_id: Optional[str] = None
    mode: str = "voice"  # sight, voice, or lore
