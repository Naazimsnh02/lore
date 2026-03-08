"""Data models for the Depth Dial configuration service.

Design reference: LORE design.md, Section "Depth Dial Configuration".
Requirements: 14.1–14.6.
Property 13: Depth Dial Content Complexity Ordering.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class DepthLevel(str, Enum):
    """Content complexity levels (Req 14.1).

    Explorer < Scholar < Expert in complexity ordering (Property 13).
    """

    EXPLORER = "explorer"
    SCHOLAR = "scholar"
    EXPERT = "expert"


# Numeric complexity values used for Property 13 ordering guarantee.
DEPTH_COMPLEXITY: dict[DepthLevel, int] = {
    DepthLevel.EXPLORER: 1,
    DepthLevel.SCHOLAR: 2,
    DepthLevel.EXPERT: 3,
}


class DepthLevelConfig(BaseModel):
    """Configuration parameters for a single depth level."""

    complexity: int = Field(ge=1, le=3)
    vocabulary: str = Field(description="Vocabulary tier: simple | intermediate | advanced")
    detail_level: str = Field(description="overview | detailed | comprehensive")
    technical_depth: str = Field(description="minimal | moderate | deep")
    examples: str = Field(description="many | some | few")
    duration_multiplier: float = Field(ge=0.5, le=3.0, description="Multiplier applied to narration duration")


class ContentAdaptationRequest(BaseModel):
    """Request to adapt content to a specific depth level."""

    content: str = Field(..., min_length=1)
    level: DepthLevel
    topic: Optional[str] = Field(default=None)
    language: str = Field(default="en")


class ContentAdaptationResult(BaseModel):
    """Result of adapting content to a depth level."""

    original_content: str
    adapted_content: str
    level: DepthLevel
    config: DepthLevelConfig
    word_count_original: int = 0
    word_count_adapted: int = 0
    error: Optional[str] = None


class DepthDialState(BaseModel):
    """Per-session depth dial state."""

    session_id: str
    current_level: DepthLevel = Field(default=DepthLevel.EXPLORER)
    previous_level: Optional[DepthLevel] = None
    change_count: int = Field(default=0, ge=0)


class NarrationPromptConfig(BaseModel):
    """Prompt engineering configuration per depth level.

    These instructions are injected into the narration engine's system
    prompt to control output complexity.
    """

    system_instruction: str = Field(description="Injected into narration system prompt")
    vocabulary_instruction: str
    detail_instruction: str
    example_instruction: str
    max_sentences_per_segment: int = Field(ge=1, le=20)
    target_reading_level: str = Field(description="E.g. 'grade 6', 'undergraduate', 'postgraduate'")
