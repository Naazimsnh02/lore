"""Historical Character Encounters — AI-generated historical personas for LORE.

Design reference: LORE design.md, HistoricalCharacterManager interface.
Requirements: 12.1–12.6.
"""

from .manager import HistoricalCharacterManager
from .models import (
    CharacterPersona,
    HistoricalCharacter,
    InteractionResult,
    Personality,
)

__all__ = [
    "HistoricalCharacterManager",
    "HistoricalCharacter",
    "CharacterPersona",
    "InteractionResult",
    "Personality",
]
