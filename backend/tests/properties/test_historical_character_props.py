"""Property-based tests for Historical Character Encounters (Task 25).

Requirements tested: 12.4 (accuracy), 12.5 (knowledge cutoff, period language).
Uses Hypothesis for randomised generation with 100+ iterations.

Feature: lore-multimodal-documentary-app, Property: Historical Character Accuracy
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from backend.services.historical_character.database import (
    HistoricalCharacterDatabase,
    _CHARACTERS,
)
from backend.services.historical_character.manager import (
    HistoricalCharacterManager,
    _HISTORICAL_KEYWORDS,
)
from backend.services.historical_character.models import (
    CharacterPersona,
    HistoricalCharacter,
    InteractionResult,
    Personality,
)


# ── Strategies ────────────────────────────────────────────────────────────────

_historical_keyword_list = sorted(_HISTORICAL_KEYWORDS)

personality_strategy = st.builds(
    Personality,
    traits=st.lists(st.text(min_size=1, max_size=30), min_size=0, max_size=5),
    speech_style=st.text(min_size=0, max_size=100),
    knowledge_domain=st.lists(st.text(min_size=1, max_size=30), min_size=0, max_size=5),
)

character_strategy = st.builds(
    HistoricalCharacter,
    name=st.text(min_size=1, max_size=50).filter(lambda s: s.strip()),
    historical_period=st.text(min_size=1, max_size=80),
    birth_year=st.one_of(st.none(), st.integers(min_value=-3000, max_value=2000)),
    death_year=st.one_of(st.none(), st.integers(min_value=-3000, max_value=2025)),
    occupation=st.lists(st.text(min_size=1, max_size=30), min_size=0, max_size=3),
    location=st.text(min_size=0, max_size=100),
    personality=personality_strategy,
    knowledge_cutoff=st.integers(min_value=-3000, max_value=2025),
    cultural_context=st.text(min_size=0, max_size=200),
    related_locations=st.lists(st.text(min_size=1, max_size=30), min_size=0, max_size=5),
    related_topics=st.lists(st.text(min_size=1, max_size=30), min_size=0, max_size=5),
)


# ── Property: Knowledge cutoff is always in system prompt (Req 12.5) ────────


class TestKnowledgeCutoffProperty:
    """Feature: lore-multimodal-documentary-app, Property: Knowledge cutoff enforcement."""

    @given(character=character_strategy)
    @settings(max_examples=120)
    def test_system_prompt_always_contains_cutoff_year(self, character: HistoricalCharacter):
        """The system prompt must always reference the knowledge_cutoff year."""
        manager = HistoricalCharacterManager()
        prompt = manager._build_system_prompt(character)
        assert str(character.knowledge_cutoff) in prompt

    @given(character=character_strategy)
    @settings(max_examples=120)
    def test_system_prompt_always_instructs_first_person(self, character: HistoricalCharacter):
        """Req 12.3 — prompt always instructs first-person perspective."""
        manager = HistoricalCharacterManager()
        prompt = manager._build_system_prompt(character)
        assert "first person" in prompt.lower()

    @given(character=character_strategy)
    @settings(max_examples=120)
    def test_system_prompt_always_instructs_period_language(self, character: HistoricalCharacter):
        """Req 12.5 — prompt always instructs period-appropriate language."""
        manager = HistoricalCharacterManager()
        prompt = manager._build_system_prompt(character)
        assert "period-appropriate" in prompt.lower()

    @given(character=character_strategy)
    @settings(max_examples=120)
    def test_system_prompt_contains_character_name(self, character: HistoricalCharacter):
        """Prompt always contains the character's name."""
        manager = HistoricalCharacterManager()
        prompt = manager._build_system_prompt(character)
        assert character.name in prompt


# ── Property: Historical context detection consistency ───────────────────────


class TestHistoricalContextProperty:
    """Feature: lore-multimodal-documentary-app, Property: Historical context detection."""

    @given(
        keyword=st.sampled_from(_historical_keyword_list),
        prefix=st.text(min_size=0, max_size=20, alphabet=st.characters(whitelist_categories=("L", "Zs"))),
    )
    @settings(max_examples=150)
    def test_topic_with_historical_keyword_is_historical(
        self, keyword: str, prefix: str
    ):
        """Any topic containing a historical keyword must be detected as historical."""
        manager = HistoricalCharacterManager()
        topic = f"{prefix} {keyword} topic"
        assert manager.is_historical_context(topic=topic) is True

    @given(significance=st.floats(min_value=0.71, max_value=1.0))
    @settings(max_examples=100)
    def test_high_significance_always_historical(self, significance: float):
        """Significance > 0.7 must always return True."""
        manager = HistoricalCharacterManager()
        assert manager.is_historical_context(
            historical_significance=significance
        ) is True

    @given(significance=st.floats(min_value=0.0, max_value=0.69))
    @settings(max_examples=100)
    def test_low_significance_no_keywords_not_historical(self, significance: float):
        """Low significance with no keywords → not historical."""
        manager = HistoricalCharacterManager()
        assert manager.is_historical_context(
            historical_significance=significance,
            topic="modern cooking recipes",
        ) is False


# ── Property: Database relevance scoring ─────────────────────────────────────


class TestDatabaseScoringProperty:
    """Feature: lore-multimodal-documentary-app, Property: Database scoring bounds."""

    @given(
        location=st.text(min_size=0, max_size=50),
        topic=st.text(min_size=0, max_size=50),
    )
    @settings(max_examples=120)
    @pytest.mark.asyncio
    async def test_find_relevant_returns_bounded_results(
        self, location: str, topic: str
    ):
        """find_relevant must return 0-3 results, all with valid scores."""
        db = HistoricalCharacterDatabase()
        results = await db.find_relevant(location=location, topic=topic, limit=3)
        assert 0 <= len(results) <= 3
        for char in results:
            assert isinstance(char, HistoricalCharacter)

    @given(data=st.data())
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_scoring_is_between_0_and_1(self, data):
        """Character scoring must always produce values in [0, 1]."""
        db = HistoricalCharacterDatabase()
        char = data.draw(st.sampled_from(list(_CHARACTERS)))
        location_words = set(data.draw(
            st.lists(st.text(min_size=3, max_size=15, alphabet="abcdefghijklmnopqrstuvwxyz"), min_size=0, max_size=5)
        ))
        topic_words = set(data.draw(
            st.lists(st.text(min_size=3, max_size=15, alphabet="abcdefghijklmnopqrstuvwxyz"), min_size=0, max_size=5)
        ))
        period_words = set(data.draw(
            st.lists(st.text(min_size=3, max_size=15, alphabet="abcdefghijklmnopqrstuvwxyz"), min_size=0, max_size=5)
        ))

        score = db._score_character(char, location_words, topic_words, period_words)
        assert 0.0 <= score <= 1.0


# ── Property: InteractionResult always has AI disclaimer (Req 12.6) ─────────


class TestAIDisclaimerProperty:
    """Feature: lore-multimodal-documentary-app, Property: AI disclaimer presence."""

    @given(
        name=st.text(min_size=1, max_size=50),
        text=st.text(min_size=0, max_size=500),
    )
    @settings(max_examples=120)
    def test_interaction_result_always_has_disclaimer(self, name: str, text: str):
        """Req 12.6 — every InteractionResult must carry an AI-generated disclaimer."""
        result = InteractionResult(
            character_name=name,
            response_text=text,
        )
        assert result.ai_generated_disclaimer
        assert "AI" in result.ai_generated_disclaimer

    @given(character=character_strategy)
    @settings(max_examples=100)
    def test_persona_always_has_disclaimer(self, character: HistoricalCharacter):
        """Every CharacterPersona must carry an AI disclaimer."""
        persona = CharacterPersona(character=character)
        assert persona.ai_disclaimer
        assert "AI" in persona.ai_disclaimer
