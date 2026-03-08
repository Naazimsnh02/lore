"""Unit tests for the Historical Character Encounters service (Task 25).

Requirements tested: 12.1–12.6.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.historical_character.database import (
    HistoricalCharacterDatabase,
)
from backend.services.historical_character.manager import (
    HistoricalCharacterManager,
)
from backend.services.historical_character.models import (
    CharacterEncounterOffer,
    CharacterPersona,
    HistoricalCharacter,
    InteractionResult,
    Personality,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_character(**overrides: Any) -> HistoricalCharacter:
    """Create a test character with sensible defaults."""
    defaults = dict(
        name="Marcus Aurelius",
        historical_period="Roman Empire (2nd century CE)",
        birth_year=121,
        death_year=180,
        occupation=["Roman Emperor", "Stoic philosopher"],
        location="Rome, Italy",
        personality=Personality(
            traits=["contemplative", "disciplined"],
            speech_style="Measured, philosophical",
            knowledge_domain=["Stoic philosophy", "Roman governance"],
        ),
        knowledge_cutoff=180,
        cultural_context="Height of the Roman Empire",
        related_locations=["rome", "colosseum"],
        related_topics=["roman", "stoic", "philosophy"],
    )
    defaults.update(overrides)
    return HistoricalCharacter(**defaults)


def _mock_genai_client(response_text: str = "I am Marcus Aurelius.") -> MagicMock:
    """Create a mock genai.Client that returns the given text."""
    mock_response = MagicMock()
    mock_response.text = response_text
    mock_response.candidates = []

    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    return client


def _mock_search_grounder(verified: bool = True, confidence: float = 0.8) -> MagicMock:
    """Create a mock SearchGrounder."""
    grounder = MagicMock()

    mock_result = MagicMock()
    mock_result.verified = verified
    mock_result.confidence = confidence
    mock_result.claim = MagicMock()
    mock_result.claim.text = "test claim"

    grounder.verify_batch = AsyncMock(return_value=[mock_result])
    grounder.verify_fact = AsyncMock(return_value=mock_result)
    return grounder


# ── Database Tests ───────────────────────────────────────────────────────────


class TestHistoricalCharacterDatabase:
    """Tests for HistoricalCharacterDatabase."""

    @pytest.mark.asyncio
    async def test_find_relevant_by_location(self):
        db = HistoricalCharacterDatabase()
        results = await db.find_relevant(location="Rome, Italy")
        assert len(results) > 0
        # Marcus Aurelius and Galileo are both linked to Rome/Italy
        names = [c.name for c in results]
        assert "Marcus Aurelius" in names

    @pytest.mark.asyncio
    async def test_find_relevant_by_topic(self):
        db = HistoricalCharacterDatabase()
        results = await db.find_relevant(topic="ancient philosophy and stoicism")
        assert len(results) > 0
        names = [c.name for c in results]
        assert "Marcus Aurelius" in names

    @pytest.mark.asyncio
    async def test_find_relevant_by_period(self):
        db = HistoricalCharacterDatabase()
        results = await db.find_relevant(time_period="Roman Empire")
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_find_relevant_empty_input(self):
        db = HistoricalCharacterDatabase()
        results = await db.find_relevant()
        assert results == []

    @pytest.mark.asyncio
    async def test_find_relevant_limit(self):
        db = HistoricalCharacterDatabase()
        results = await db.find_relevant(topic="history war empire", limit=2)
        assert len(results) <= 2

    @pytest.mark.asyncio
    async def test_find_relevant_no_match(self):
        db = HistoricalCharacterDatabase()
        results = await db.find_relevant(topic="quantum computing blockchain")
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_custom_character_database(self):
        custom_char = _make_character(name="Test Hero", related_topics=["testing"])
        db = HistoricalCharacterDatabase(characters=[custom_char])
        results = await db.find_relevant(topic="testing")
        assert len(results) == 1
        assert results[0].name == "Test Hero"

    def test_characters_property(self):
        db = HistoricalCharacterDatabase()
        chars = db.characters
        assert len(chars) > 0
        # Should be a copy
        chars.clear()
        assert len(db.characters) > 0

    @pytest.mark.asyncio
    async def test_egypt_finds_cleopatra(self):
        db = HistoricalCharacterDatabase()
        results = await db.find_relevant(location="Egypt", topic="pharaoh")
        names = [c.name for c in results]
        assert "Cleopatra VII" in names

    @pytest.mark.asyncio
    async def test_science_finds_curie(self):
        db = HistoricalCharacterDatabase()
        results = await db.find_relevant(topic="radiation physics Nobel")
        names = [c.name for c in results]
        assert "Marie Curie" in names


# ── Manager Tests: is_historical_context ─────────────────────────────────────


class TestIsHistoricalContext:
    """Tests for HistoricalCharacterManager.is_historical_context (Req 12.1)."""

    def setup_method(self):
        self.manager = HistoricalCharacterManager()

    def test_high_significance_returns_true(self):
        assert self.manager.is_historical_context(historical_significance=0.9) is True

    def test_low_significance_no_keywords_returns_false(self):
        assert self.manager.is_historical_context(
            historical_significance=0.3, topic="modern cooking"
        ) is False

    def test_historical_topic_returns_true(self):
        assert self.manager.is_historical_context(
            topic="The ancient Roman Empire"
        ) is True

    def test_historical_location_returns_true(self):
        assert self.manager.is_historical_context(
            location="medieval castle"
        ) is True

    def test_historical_place_type_returns_true(self):
        assert self.manager.is_historical_context(
            place_types=["museum"]
        ) is True

    def test_empty_context_returns_false(self):
        assert self.manager.is_historical_context() is False

    def test_non_historical_topic(self):
        assert self.manager.is_historical_context(
            topic="modern restaurant food"
        ) is False


# ── Manager Tests: offer_character_encounter ─────────────────────────────────


class TestOfferCharacterEncounter:
    """Tests for HistoricalCharacterManager.offer_character_encounter (Req 12.1)."""

    @pytest.mark.asyncio
    async def test_offers_character_for_historical_context(self):
        manager = HistoricalCharacterManager()
        offer = await manager.offer_character_encounter(
            location="Rome",
            topic="ancient Roman history",
            historical_significance=0.9,
        )
        assert offer is not None
        assert offer.character.name
        assert offer.prompt_text
        assert offer.ai_disclaimer  # Req 12.6

    @pytest.mark.asyncio
    async def test_no_offer_for_non_historical_context(self):
        manager = HistoricalCharacterManager()
        offer = await manager.offer_character_encounter(
            location="Modern Mall",
            topic="shopping",
            historical_significance=0.1,
        )
        assert offer is None

    @pytest.mark.asyncio
    async def test_offer_includes_ai_disclaimer(self):
        """Req 12.6 — clearly indicate AI-generated."""
        manager = HistoricalCharacterManager()
        offer = await manager.offer_character_encounter(
            location="Rome",
            topic="ancient history",
            historical_significance=0.9,
        )
        assert offer is not None
        assert "AI" in offer.ai_disclaimer

    @pytest.mark.asyncio
    async def test_offer_relevance_score(self):
        manager = HistoricalCharacterManager()
        offer = await manager.offer_character_encounter(
            location="Rome, Italy",
            topic="Stoic philosophy",
            historical_significance=0.9,
        )
        assert offer is not None
        assert 0.0 <= offer.relevance_score <= 1.0


# ── Manager Tests: create_character_persona ──────────────────────────────────


class TestCreateCharacterPersona:
    """Tests for persona creation (Req 12.2, 12.5)."""

    @pytest.mark.asyncio
    async def test_creates_persona_with_system_prompt(self):
        manager = HistoricalCharacterManager()
        char = _make_character()
        persona = await manager.create_character_persona(char, session_id="s1")
        assert persona.character.name == "Marcus Aurelius"
        assert persona.system_prompt
        assert persona.conversation_history == []

    @pytest.mark.asyncio
    async def test_system_prompt_contains_knowledge_cutoff(self):
        """Req 12.5 — knowledge cutoff enforced in prompt."""
        manager = HistoricalCharacterManager()
        char = _make_character(knowledge_cutoff=180)
        persona = await manager.create_character_persona(char)
        assert "180" in persona.system_prompt

    @pytest.mark.asyncio
    async def test_system_prompt_contains_first_person(self):
        """Req 12.3 — first-person perspective."""
        manager = HistoricalCharacterManager()
        char = _make_character()
        persona = await manager.create_character_persona(char)
        assert "first person" in persona.system_prompt.lower()

    @pytest.mark.asyncio
    async def test_system_prompt_contains_period_language(self):
        """Req 12.5 — period-appropriate language."""
        manager = HistoricalCharacterManager()
        char = _make_character()
        persona = await manager.create_character_persona(char)
        assert "period-appropriate" in persona.system_prompt.lower()

    @pytest.mark.asyncio
    async def test_system_prompt_contains_character_name(self):
        manager = HistoricalCharacterManager()
        char = _make_character(name="Cleopatra VII")
        persona = await manager.create_character_persona(char)
        assert "Cleopatra VII" in persona.system_prompt

    @pytest.mark.asyncio
    async def test_persona_stored_in_active_personas(self):
        manager = HistoricalCharacterManager()
        char = _make_character()
        persona = await manager.create_character_persona(char, session_id="s1")
        assert manager.get_active_persona("s1") is persona

    @pytest.mark.asyncio
    async def test_bce_dates_formatted_correctly(self):
        manager = HistoricalCharacterManager()
        char = _make_character(birth_year=-69, death_year=-30)
        persona = await manager.create_character_persona(char)
        assert "BCE" in persona.system_prompt


# ── Manager Tests: interact_with_character ───────────────────────────────────


class TestInteractWithCharacter:
    """Tests for character interaction (Req 12.3, 12.4, 12.6)."""

    @pytest.mark.asyncio
    async def test_basic_interaction_with_llm(self):
        """Req 12.3 — responds to user questions."""
        client = _mock_genai_client("As the Emperor of Rome, I believe in duty above all.")
        manager = HistoricalCharacterManager(client=client)
        char = _make_character()
        persona = await manager.create_character_persona(char)

        result = await manager.interact_with_character(
            persona, "What is your philosophy?"
        )
        assert result.response_text
        assert result.character_name == "Marcus Aurelius"
        assert result.ai_generated_disclaimer  # Req 12.6

    @pytest.mark.asyncio
    async def test_interaction_updates_conversation_history(self):
        client = _mock_genai_client("I ruled Rome wisely.")
        manager = HistoricalCharacterManager(client=client)
        char = _make_character()
        persona = await manager.create_character_persona(char)

        await manager.interact_with_character(persona, "Tell me about your reign")
        assert len(persona.conversation_history) == 2  # user + assistant
        assert persona.conversation_history[0]["role"] == "user"
        assert persona.conversation_history[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_interaction_with_accuracy_verification(self):
        """Req 12.4 — accuracy verified by SearchGrounder."""
        client = _mock_genai_client("Rome was founded in 753 BCE.")
        grounder = _mock_search_grounder(verified=True)
        manager = HistoricalCharacterManager(client=client, search_grounder=grounder)
        char = _make_character()
        persona = await manager.create_character_persona(char)

        result = await manager.interact_with_character(
            persona, "When was Rome founded?"
        )
        assert result.accuracy_verified is True

    @pytest.mark.asyncio
    async def test_interaction_without_grounder(self):
        """Graceful degradation — no grounder means no verification."""
        client = _mock_genai_client("I was born in Rome.")
        manager = HistoricalCharacterManager(client=client)
        char = _make_character()
        persona = await manager.create_character_persona(char)

        result = await manager.interact_with_character(persona, "Where were you born?")
        assert result.accuracy_verified is False
        assert result.error is None

    @pytest.mark.asyncio
    async def test_interaction_fallback_on_timeout(self):
        """Graceful degradation — timeout returns fallback."""
        client = MagicMock()

        async def slow_generate(*args, **kwargs):
            await asyncio.sleep(20)

        client.aio.models.generate_content = slow_generate
        manager = HistoricalCharacterManager(client=client)
        # Override timeout for fast test
        import backend.services.historical_character.manager as mgr_module
        original_timeout = mgr_module._RESPONSE_TIMEOUT_S
        mgr_module._RESPONSE_TIMEOUT_S = 0.1

        try:
            char = _make_character()
            persona = await manager.create_character_persona(char)
            result = await manager.interact_with_character(persona, "Hello?")
            assert result.error is not None
            assert "timed out" in result.error.lower()
        finally:
            mgr_module._RESPONSE_TIMEOUT_S = original_timeout

    @pytest.mark.asyncio
    async def test_interaction_fallback_on_error(self):
        """Graceful degradation — API error returns error result."""
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(
            side_effect=RuntimeError("API Error")
        )
        manager = HistoricalCharacterManager(client=client)
        char = _make_character()
        persona = await manager.create_character_persona(char)

        result = await manager.interact_with_character(persona, "Hello?")
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_fallback_response_without_client(self):
        """No client → static fallback response."""
        manager = HistoricalCharacterManager()
        char = _make_character()
        persona = await manager.create_character_persona(char)

        result = await manager.interact_with_character(persona, "Hello?")
        assert result.response_text
        assert "Marcus Aurelius" in result.response_text

    @pytest.mark.asyncio
    async def test_ai_disclaimer_always_present(self):
        """Req 12.6 — every result has AI-generated disclaimer."""
        client = _mock_genai_client("I am a philosopher.")
        manager = HistoricalCharacterManager(client=client)
        char = _make_character()
        persona = await manager.create_character_persona(char)

        result = await manager.interact_with_character(persona, "Who are you?")
        assert "AI" in result.ai_generated_disclaimer
        assert "historical" in result.ai_generated_disclaimer.lower()

    @pytest.mark.asyncio
    async def test_conversation_history_trimmed(self):
        """Conversation history stays within limits."""
        client = _mock_genai_client("Response")
        manager = HistoricalCharacterManager(client=client)
        char = _make_character()
        persona = await manager.create_character_persona(char)

        # Fill up history beyond max
        for i in range(25):
            persona.conversation_history.append({"role": "user", "content": f"q{i}"})
            persona.conversation_history.append({"role": "assistant", "content": f"a{i}"})

        await manager.interact_with_character(persona, "final question")
        assert len(persona.conversation_history) <= 22  # max 20 + new pair

    @pytest.mark.asyncio
    async def test_corrections_applied_on_inaccuracy(self):
        """Req 12.4 — inaccurate responses are corrected."""
        # First call: original response with factual claim
        # Second call: corrected response
        call_count = 0

        async def mock_generate(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            if call_count == 1:
                resp.text = "Rome was built in 500 BCE by the Greeks."
            else:
                resp.text = "Rome was founded in 753 BCE by Romulus."
            return resp

        client = MagicMock()
        client.aio.models.generate_content = mock_generate

        grounder = _mock_search_grounder(verified=False, confidence=0.6)
        manager = HistoricalCharacterManager(client=client, search_grounder=grounder)
        char = _make_character()
        persona = await manager.create_character_persona(char)

        result = await manager.interact_with_character(
            persona, "When was Rome built?"
        )
        assert result.accuracy_verified is True
        assert result.corrections_applied is True


# ── Manager Tests: session management ────────────────────────────────────────


class TestSessionManagement:
    """Tests for active persona session management."""

    @pytest.mark.asyncio
    async def test_get_active_persona(self):
        manager = HistoricalCharacterManager()
        char = _make_character()
        persona = await manager.create_character_persona(char, session_id="s1")
        assert manager.get_active_persona("s1") is persona

    @pytest.mark.asyncio
    async def test_get_nonexistent_persona(self):
        manager = HistoricalCharacterManager()
        assert manager.get_active_persona("nonexistent") is None

    @pytest.mark.asyncio
    async def test_end_encounter(self):
        manager = HistoricalCharacterManager()
        char = _make_character()
        await manager.create_character_persona(char, session_id="s1")
        assert manager.end_encounter("s1") is True
        assert manager.get_active_persona("s1") is None

    @pytest.mark.asyncio
    async def test_end_nonexistent_encounter(self):
        manager = HistoricalCharacterManager()
        assert manager.end_encounter("nonexistent") is False


# ── Model Tests ──────────────────────────────────────────────────────────────


class TestModels:
    """Tests for Historical Character data models."""

    def test_historical_character_defaults(self):
        char = HistoricalCharacter(name="Test", historical_period="Ancient")
        assert char.character_id
        assert char.name == "Test"
        assert char.occupation == []
        assert char.knowledge_cutoff == 0

    def test_character_persona_defaults(self):
        char = _make_character()
        persona = CharacterPersona(character=char)
        assert persona.system_prompt == ""
        assert persona.conversation_history == []
        assert "AI" in persona.ai_disclaimer

    def test_interaction_result_defaults(self):
        result = InteractionResult(
            character_name="Test",
            response_text="Hello",
        )
        assert result.accuracy_verified is False
        assert result.corrections_applied is False
        assert "AI" in result.ai_generated_disclaimer

    def test_character_encounter_offer_defaults(self):
        char = _make_character()
        offer = CharacterEncounterOffer(character=char)
        assert offer.relevance_score == 0.0
        assert "AI" in offer.ai_disclaimer

    def test_personality_model(self):
        p = Personality(
            traits=["brave"], speech_style="bold", knowledge_domain=["war"]
        )
        assert p.traits == ["brave"]


# ── Claim Extraction Tests ───────────────────────────────────────────────────


class TestClaimExtraction:
    """Tests for _extract_claims helper."""

    def test_extracts_factual_sentences(self):
        manager = HistoricalCharacterManager()
        char = _make_character()
        # Patch the search_grounder models import
        text = "I founded the city in the year 100. I also enjoy sunsets. The battle was fierce."
        claims = manager._extract_claims(text, char)
        assert len(claims) >= 2  # "founded" and "battle"

    def test_ignores_short_sentences(self):
        manager = HistoricalCharacterManager()
        char = _make_character()
        text = "Yes. No. Maybe so."
        claims = manager._extract_claims(text, char)
        assert len(claims) == 0

    def test_limits_claims(self):
        manager = HistoricalCharacterManager()
        char = _make_character()
        text = ". ".join(
            f"In the year {100+i} we conquered territory number {i}"
            for i in range(20)
        )
        claims = manager._extract_claims(text, char)
        assert len(claims) <= 5
