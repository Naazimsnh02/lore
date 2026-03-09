"""Veo Generator – Veo 3.1 video generation for LORE documentaries.

Design reference: LORE design.md, Section 6 – Veo Generator.
Requirements: 6.1–6.7.
Properties: 6 (duration), 7 (quality), 23 (graceful degradation).

Uses Veo 3.1 via the google-genai SDK for cinematic video clip generation
with native audio (30-60s generation time, minimum 1080p resolution).
"""

from .generator import VeoGenerator
from .models import (
    AspectRatio,
    DocumentaryContext,
    SceneChainResult,
    SceneDescription,
    VideoClip,
    VideoGenerationResult,
    VideoResolution,
    VideoStatus,
    VideoStyle,
    VeoError,
    VeoGenerationError,
    VeoTimeoutError,
)

__all__ = [
    "VeoGenerator",
    "AspectRatio",
    "DocumentaryContext",
    "SceneChainResult",
    "SceneDescription",
    "VideoClip",
    "VideoGenerationResult",
    "VideoResolution",
    "VideoStatus",
    "VideoStyle",
    "VeoError",
    "VeoGenerationError",
    "VeoTimeoutError",
]
