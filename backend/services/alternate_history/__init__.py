"""Alternate History Engine — what-if scenario generation for LORE.

Requirement 15: Alternate History Mode.
"""

from .detector import AlternateHistoryDetector
from .engine import AlternateHistoryEngine
from .models import (
    AlternateHistoryScenario,
    CausalLink,
    HistoricalEvent,
    SpeculativeContent,
    WhatIfQuestion,
)

__all__ = [
    "AlternateHistoryDetector",
    "AlternateHistoryEngine",
    "AlternateHistoryScenario",
    "CausalLink",
    "HistoricalEvent",
    "SpeculativeContent",
    "WhatIfQuestion",
]
