"""Nano Illustrator – AI illustration generation for LORE documentaries.

Design reference: LORE design.md, Section 5 – Nano Illustrator.
Requirements: 7.1–7.6.
Properties: 8 (latency), 9 (quality), 10 (style consistency).

Uses Gemini 3.1 Flash Image Preview for rapid illustration generation
(< 2 seconds per image, minimum 1024×1024 resolution).
"""

from .illustrator import NanoIllustrator
from .models import (
    ConceptDescription,
    Illustration,
    IllustrationError,
    IllustrationResult,
    VisualStyle,
)

__all__ = [
    "NanoIllustrator",
    "ConceptDescription",
    "Illustration",
    "IllustrationError",
    "IllustrationResult",
    "VisualStyle",
]
