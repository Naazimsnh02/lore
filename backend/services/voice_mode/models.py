"""Data models for VoiceMode handler and conversation management.

Design reference: LORE design.md, VoiceMode Implementation section.
Requirements: 3.1–3.6, 13.1–13.2.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────


class VoiceModeEvent(str, Enum):
    """Events emitted by the VoiceMode handler."""

    TOPIC_DETECTED = "topic_detected"
    TRANSCRIPTION_COMPLETE = "transcription_complete"
    NOISE_WARNING = "noise_warning"
    LANGUAGE_DETECTED = "language_detected"
    PROCESSING_STARTED = "processing_started"
    SILENCE_DETECTED = "silence_detected"
    INPUT_BUFFERED = "input_buffered"
    ERROR = "error"


class ConversationIntent(str, Enum):
    """Classified intent of a user utterance (design.md §ConversationManager)."""

    NEW_TOPIC = "new_topic"
    FOLLOW_UP = "follow_up"
    BRANCH = "branch"
    QUESTION = "question"
    COMMAND = "command"  # e.g. "stop", "switch mode", "change depth"


class NoiseLevel(str, Enum):
    """Ambient noise classification."""

    LOW = "low"          # < 50 dB
    MODERATE = "moderate"  # 50-70 dB
    HIGH = "high"        # > 70 dB


# ── Supported languages (24 per Ghost Guide — Req 3.6, 17.1) ────────────────

SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "ru": "Russian",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "ar": "Arabic",
    "hi": "Hindi",
    "tr": "Turkish",
    "pl": "Polish",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "el": "Greek",
    "th": "Thai",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "he": "Hebrew",
}


# ── Audio / Voice input models ────────────────────────────────────────────────


class AudioMetadata(BaseModel):
    """Metadata for an audio input chunk."""

    sample_rate: int = Field(default=16000, description="Sample rate in Hz")
    channels: int = Field(default=1, description="Number of audio channels")
    encoding: str = Field(default="LINEAR16", description="Audio encoding format")
    duration_ms: float = Field(default=0.0, ge=0.0, description="Duration in milliseconds")
    noise_level_db: float = Field(default=0.0, description="Estimated ambient noise in dB")
    timestamp: float = Field(default_factory=time.time)


class TranscriptionResult(BaseModel):
    """Result from speech-to-text transcription."""

    text: str = Field(default="", description="Transcribed text")
    language: str = Field(default="en", description="Detected language (ISO 639-1)")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Transcription confidence")
    duration_ms: float = Field(default=0.0, ge=0.0, description="Transcription latency in ms")
    is_final: bool = Field(default=True, description="Whether this is a final transcription")
    timestamp: float = Field(default_factory=time.time)


class VoiceModeResponse(BaseModel):
    """Response from VoiceMode input processing."""

    event: VoiceModeEvent
    transcription: Optional[TranscriptionResult] = None
    topic: Optional[str] = None
    detected_language: Optional[str] = None
    noise_level: Optional[NoiseLevel] = None
    noise_cancelled: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)


class VoiceModeContext(BaseModel):
    """Context produced by VoiceMode for documentary generation."""

    mode: str = "voice"
    topic: str = Field(default="", description="Parsed topic from voice input")
    original_query: str = Field(default="", description="Full transcribed text")
    language: str = Field(default="en", description="Detected language")
    intent: ConversationIntent = Field(default=ConversationIntent.NEW_TOPIC)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    noise_cancelled: bool = False
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    previous_topics: list[str] = Field(default_factory=list)
    branch_parent_id: Optional[str] = None


# ── Conversation models ──────────────────────────────────────────────────────


class ConversationTurn(BaseModel):
    """A single turn in the conversation history."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    role: str = Field(description="'user' or 'assistant'")
    content: str = Field(description="Text content of the turn")
    intent: Optional[ConversationIntent] = None
    topic: Optional[str] = None
    language: str = Field(default="en")
    timestamp: float = Field(default_factory=time.time)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationState(BaseModel):
    """Current state of an active conversation."""

    session_id: str = Field(default="")
    user_id: str = Field(default="")
    current_topic: Optional[str] = None
    current_language: str = Field(default="en")
    branch_depth: int = Field(default=0, ge=0, le=3, description="Current branch nesting depth")
    branch_stack: list[str] = Field(
        default_factory=list,
        description="Stack of branch topic IDs for navigation",
    )
    turn_count: int = Field(default=0, ge=0)
    started_at: float = Field(default_factory=time.time)
    last_activity: float = Field(default_factory=time.time)


class IntentClassification(BaseModel):
    """Result of classifying a user utterance's intent."""

    intent: ConversationIntent
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    extracted_topic: Optional[str] = None
    branch_topic: Optional[str] = None
    reasoning: str = Field(default="", description="Brief explanation of classification")
