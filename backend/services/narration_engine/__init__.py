"""Narration Engine — real-time voice narration via Gemini Live API.

Design reference: LORE design.md, Section 3 – Narration Engine.
Requirements: 3.1, 3.2, 5.2, 11.1–11.6.
"""

from .affective_narrator import AffectiveNarrator
from .engine import NarrationEngine
from .models import (
    AudioChunk,
    DepthLevel,
    EmotionalTone,
    NarrationContext,
    NarrationResult,
    NarrationScript,
    NarrationSegment,
    VoiceParameters,
)

__all__ = [
    "AffectiveNarrator",
    "NarrationEngine",
    "AudioChunk",
    "DepthLevel",
    "EmotionalTone",
    "NarrationContext",
    "NarrationResult",
    "NarrationScript",
    "NarrationSegment",
    "VoiceParameters",
]
