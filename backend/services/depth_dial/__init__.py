"""Depth Dial configuration service.

Adjusts content complexity based on user expertise level (Req 14.1–14.6).
Property 13: complexity(Explorer) < complexity(Scholar) < complexity(Expert).
"""

from .manager import DepthDialManager
from .models import (
    ContentAdaptationRequest,
    ContentAdaptationResult,
    DEPTH_COMPLEXITY,
    DepthDialState,
    DepthLevel,
    DepthLevelConfig,
    NarrationPromptConfig,
)

__all__ = [
    "DepthDialManager",
    "ContentAdaptationRequest",
    "ContentAdaptationResult",
    "DEPTH_COMPLEXITY",
    "DepthDialState",
    "DepthLevel",
    "DepthLevelConfig",
    "NarrationPromptConfig",
]
