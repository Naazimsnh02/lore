"""LoreMode — camera + voice fusion handler for LORE.

Implements Task 21: LoreMode fusion handler.
Requirements: 4.1, 4.2, 4.3, 4.5, 4.6.
"""

from .fusion_engine import FusionEngine
from .handler import LoreModeHandler
from .models import (
    CrossModalConnection,
    ConnectionType,
    FusedContext,
    LoreModeEvent,
    LoreModeResponse,
    ProcessingLoad,
    ProcessingPriority,
)

__all__ = [
    "FusionEngine",
    "LoreModeHandler",
    "CrossModalConnection",
    "ConnectionType",
    "FusedContext",
    "LoreModeEvent",
    "LoreModeResponse",
    "ProcessingLoad",
    "ProcessingPriority",
]
