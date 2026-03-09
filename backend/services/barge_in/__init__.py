"""Barge-In Handler service for managing user interruptions during documentary playback.

This module implements the Barge-In Handler component from the LORE design document,
enabling natural conversational interruptions during documentary streaming.

Design reference: LORE design.md, Section 9 (Barge-In Handler).
Requirements: 19.1-19.6 (Barge-In Handling).
"""

from .handler import BargeInHandler
from .models import (
    BargeInResult,
    InterjectionResponse,
    InterjectionType,
    Interruption,
    ResumeAction,
)

__all__ = [
    "BargeInHandler",
    "BargeInResult",
    "Interruption",
    "InterjectionResponse",
    "InterjectionType",
    "ResumeAction",
]
