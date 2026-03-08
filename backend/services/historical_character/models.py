"""Data models for the Historical Character Encounters service.

Design reference: LORE design.md, HistoricalCharacter interface.
Requirements: 12.1–12.6.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field


class Personality(BaseModel):
    """Character personality definition."""

    traits: list[str] = Field(default_factory=list)
    speech_style: str = ""
    knowledge_domain: list[str] = Field(default_factory=list)


class HistoricalCharacter(BaseModel):
    """A historical figure who can be encountered in a documentary.

    Matches the HistoricalCharacter interface in design.md.
    """

    character_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str
    historical_period: str
    birth_year: Optional[int] = None
    death_year: Optional[int] = None
    occupation: list[str] = Field(default_factory=list)
    location: str = ""

    # Character definition
    personality: Personality = Field(default_factory=Personality)

    # Constraints
    knowledge_cutoff: int = 0  # Year — character knows nothing after this
    language_limitations: list[str] = Field(default_factory=list)
    cultural_context: str = ""

    # Relevance metadata
    related_locations: list[str] = Field(default_factory=list)
    related_topics: list[str] = Field(default_factory=list)


class CharacterPersona(BaseModel):
    """An active character persona with conversation state."""

    character: HistoricalCharacter
    system_prompt: str = ""
    conversation_history: list[dict[str, str]] = Field(default_factory=list)
    ai_disclaimer: str = (
        "This is an AI-generated historical persona. "
        "Responses are based on historical knowledge but are not direct quotes."
    )


class InteractionResult(BaseModel):
    """Result of a user interaction with a historical character."""

    character_name: str
    response_text: str
    accuracy_verified: bool = False
    corrections_applied: bool = False
    ai_generated_disclaimer: str = (
        "[AI-GENERATED] This historical character interaction is created by AI "
        "and should not be taken as direct historical quotation."
    )
    error: Optional[str] = None


class CharacterEncounterOffer(BaseModel):
    """An offer for the user to engage with a historical character."""

    character: HistoricalCharacter
    prompt_text: str = ""
    relevance_score: float = 0.0
    ai_disclaimer: str = (
        "Historical character encounters are AI-generated and should not be "
        "taken as direct historical quotation."
    )
