"""Mode Switch Manager — handles mode transitions with content preservation.

Design reference: LORE design.md, Section 1 – Core Mode Selection.
Requirements: 1.6 (mode switching during active sessions),
              1.7 (preserve all previously generated content on mode switch).
"""

from .manager import ModeSwitchManager
from .models import (
    ModeSwitchContext,
    ModeSwitchError,
    ModeSwitchRecord,
    ModeSwitchResult,
    PreservedContent,
)

__all__ = [
    "ModeSwitchContext",
    "ModeSwitchError",
    "ModeSwitchManager",
    "ModeSwitchRecord",
    "ModeSwitchResult",
    "PreservedContent",
]
