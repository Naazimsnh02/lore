"""Data models for the Alternate History Engine.

Design reference: LORE design.md — AlternateHistoryScenario interface.
Requirements: 15.1–15.6.
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────


class ScenarioStatus(str, Enum):
    """Status of an alternate history scenario generation."""

    PENDING = "pending"
    GROUNDING = "grounding"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


class ContentLabel(str, Enum):
    """Labels applied to content to distinguish fact from speculation (Req 15.4)."""

    HISTORICAL_FACT = "historical_fact"
    SPECULATIVE = "speculative"
    CAUSAL_REASONING = "causal_reasoning"


# ── Core models ──────────────────────────────────────────────────────────────


class HistoricalEvent(BaseModel):
    """A real historical event serving as the basis for alternate history.

    Design reference: HistoricalEvent interface in design.md.
    """

    name: str = Field(..., description="Name of the historical event")
    date: str = Field(default="", description="Approximate date or period")
    location: str = Field(default="", description="Geographic location")
    description: str = Field(default="", description="Brief description of the event")
    significance: str = Field(
        default="", description="Historical significance of the event"
    )


class CausalLink(BaseModel):
    """A link in a causal reasoning chain (Req 15.5).

    Design reference: CausalLink interface in design.md.
    """

    from_event: str = Field(..., description="The cause / preceding condition")
    to_event: str = Field(..., description="The resulting outcome")
    reasoning: str = Field(
        default="", description="Explanation of the causal relationship"
    )
    confidence: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Confidence in this link"
    )


class WhatIfQuestion(BaseModel):
    """Parsed representation of a user's what-if question.

    Extracted by AlternateHistoryDetector from natural language.
    """

    original_question: str = Field(..., description="The raw user question")
    base_event: HistoricalEvent = Field(
        default_factory=lambda: HistoricalEvent(name="Unknown"),
        description="The historical event being modified",
    )
    divergence_point: str = Field(
        default="",
        description="The specific point of divergence from real history",
    )
    detected_at: float = Field(default_factory=time.time)


class AlternateHistoryScenario(BaseModel):
    """A fully generated alternate history scenario.

    Design reference: AlternateHistoryScenario interface in design.md.
    Requirements: 15.2 (plausible narratives), 15.3 (grounded in facts),
                  15.5 (causal reasoning).
    """

    scenario_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    session_id: str = Field(default="")
    what_if_question: WhatIfQuestion
    status: ScenarioStatus = Field(default=ScenarioStatus.PENDING)

    # Historical grounding (Req 15.3)
    base_event: HistoricalEvent = Field(
        default_factory=lambda: HistoricalEvent(name="Unknown")
    )
    historical_grounding: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Source citations verifying the base historical facts",
    )

    # Alternative outcome (Req 15.2)
    alternative_narrative: str = Field(
        default="",
        description="The generated plausible alternative history narrative",
    )
    divergence_point: str = Field(
        default="", description="Where this timeline diverges from reality"
    )
    causal_chain: list[CausalLink] = Field(
        default_factory=list,
        description="Chain of causal reasoning for the outcome (Req 15.5)",
    )
    plausibility: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Estimated plausibility score",
    )

    # Metadata
    generated_at: float = Field(default_factory=time.time)
    error: Optional[str] = None


class SpeculativeContent(BaseModel):
    """Wrapper that labels content as speculative (Req 15.4).

    All content produced by the Alternate History Engine is wrapped in this
    model to clearly distinguish it from verified historical facts.
    """

    label: ContentLabel = Field(default=ContentLabel.SPECULATIVE)
    text: str = Field(default="")
    source_scenario_id: str = Field(default="")
    disclaimer: str = Field(
        default="This content is speculative and explores an alternate history scenario.",
    )
