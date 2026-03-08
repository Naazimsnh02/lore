"""VoiceMode handler — voice-based documentary generation.

Design reference: LORE design.md, VoiceMode Implementation section.
Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 13.1, 13.2.
"""

from .conversation_manager import ConversationManager
from .handler import VoiceModeHandler

__all__ = ["ConversationManager", "VoiceModeHandler"]
