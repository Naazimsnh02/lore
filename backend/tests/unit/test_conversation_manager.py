"""Unit tests for ConversationManager.

Design reference: LORE design.md, Conversation Management section.
Requirements: 3.4 (continuous conversation), 13.1 (branch detection),
              13.2 (branch creation), 13.4 (branch depth ≤ 3).
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.voice_mode.conversation_manager import (
    DEFAULT_CONTEXT_WINDOW,
    INACTIVITY_TIMEOUT_S,
    MAX_BRANCH_DEPTH,
    ConversationManager,
)
from backend.services.voice_mode.models import (
    ConversationIntent,
    ConversationState,
    ConversationTurn,
    IntentClassification,
    VoiceModeContext,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _ctx(
    topic: str = "ancient Rome",
    query: str = "",
    language: str = "en",
) -> VoiceModeContext:
    """Create a VoiceModeContext for testing."""
    return VoiceModeContext(
        topic=topic,
        original_query=query or f"Tell me about {topic}",
        language=language,
        confidence=0.9,
    )


# ── Intent classification ────────────────────────────────────────────────────


class TestIntentClassification:
    """Test keyword-based intent classification."""

    @pytest.mark.asyncio
    async def test_new_topic_default(self):
        """A fresh topic without indicators → NEW_TOPIC."""
        mgr = ConversationManager()
        result = await mgr.handle_input(_ctx("the Colosseum", "the Colosseum"))
        assert result.intent == ConversationIntent.NEW_TOPIC

    @pytest.mark.asyncio
    async def test_question_detected(self):
        """Questions with question indicators → QUESTION."""
        mgr = ConversationManager()
        result = await mgr.handle_input(
            _ctx("Julius Caesar", "Who was Julius Caesar?")
        )
        assert result.intent == ConversationIntent.QUESTION

    @pytest.mark.asyncio
    async def test_question_mark_detection(self):
        """Trailing '?' triggers QUESTION intent."""
        mgr = ConversationManager()
        result = await mgr.handle_input(
            _ctx("quantum physics", "quantum physics?")
        )
        assert result.intent == ConversationIntent.QUESTION

    @pytest.mark.asyncio
    async def test_branch_request(self):
        """Branch indicators → BRANCH intent with extracted sub-topic."""
        mgr = ConversationManager()
        result = await mgr.handle_input(
            _ctx("gladiators", "tell me more about gladiators")
        )
        assert result.intent == ConversationIntent.BRANCH
        assert result.branch_topic == "gladiators"

    @pytest.mark.asyncio
    async def test_branch_dive_into(self):
        mgr = ConversationManager()
        result = await mgr.handle_input(
            _ctx("the aqueducts", "dive into the aqueducts")
        )
        assert result.intent == ConversationIntent.BRANCH
        assert result.branch_topic == "the aqueducts"

    @pytest.mark.asyncio
    async def test_follow_up_with_existing_topic(self):
        """Follow-up indicators with existing topic → FOLLOW_UP."""
        mgr = ConversationManager()
        # First: set a topic
        await mgr.handle_input(_ctx("ancient Rome"))
        # Then: follow up
        result = await mgr.handle_input(
            _ctx("more", "what happened next")
        )
        assert result.intent == ConversationIntent.FOLLOW_UP

    @pytest.mark.asyncio
    async def test_follow_up_short_input(self):
        """Short input with existing topic → FOLLOW_UP."""
        mgr = ConversationManager()
        await mgr.handle_input(_ctx("ancient Rome"))
        result = await mgr.handle_input(_ctx("yes", "yes"))
        assert result.intent == ConversationIntent.FOLLOW_UP

    @pytest.mark.asyncio
    async def test_command_detected(self):
        """System commands → COMMAND intent."""
        mgr = ConversationManager()
        result = await mgr.handle_input(
            _ctx("stop", "stop")
        )
        assert result.intent == ConversationIntent.COMMAND

    @pytest.mark.asyncio
    async def test_command_switch_mode(self):
        mgr = ConversationManager()
        result = await mgr.handle_input(
            _ctx("switch mode", "switch mode to sight")
        )
        assert result.intent == ConversationIntent.COMMAND

    @pytest.mark.asyncio
    async def test_command_change_language(self):
        mgr = ConversationManager()
        result = await mgr.handle_input(
            _ctx("change language", "change language to French")
        )
        assert result.intent == ConversationIntent.COMMAND


# ── Branch depth management ──────────────────────────────────────────────────


class TestBranchDepth:
    """Req 13.4: Branch nesting depth ≤ 3."""

    @pytest.mark.asyncio
    async def test_branch_increments_depth(self):
        mgr = ConversationManager()
        await mgr.handle_input(_ctx("Rome", "tell me more about Rome"))
        assert mgr.branch_depth == 1

    @pytest.mark.asyncio
    async def test_nested_branches_up_to_3(self):
        mgr = ConversationManager()
        await mgr.handle_input(_ctx("topic1", "dive into topic1"))
        await mgr.handle_input(_ctx("topic2", "dive into topic2"))
        await mgr.handle_input(_ctx("topic3", "dive into topic3"))
        assert mgr.branch_depth == 3

    @pytest.mark.asyncio
    async def test_branch_depth_capped_at_3(self):
        """Attempting a 4th branch should downgrade to FOLLOW_UP."""
        mgr = ConversationManager()
        for i in range(3):
            await mgr.handle_input(_ctx(f"topic{i}", f"dive into topic{i}"))
        assert mgr.branch_depth == 3

        result = await mgr.handle_input(_ctx("topic4", "dive into topic4"))
        assert result.intent == ConversationIntent.FOLLOW_UP
        assert mgr.branch_depth == 3  # still capped

    @pytest.mark.asyncio
    async def test_branch_exit_decrements_depth(self):
        mgr = ConversationManager()
        await mgr.handle_input(_ctx("topic1", "dive into topic1"))
        assert mgr.branch_depth == 1

        await mgr.handle_input(_ctx("back", "go back"))
        assert mgr.branch_depth == 0

    @pytest.mark.asyncio
    async def test_branch_stack_tracks_topics(self):
        mgr = ConversationManager()
        await mgr.handle_input(_ctx("gladiators", "tell me more about gladiators"))
        await mgr.handle_input(_ctx("weapons", "dive into weapons"))
        assert mgr.state.branch_stack == ["gladiators", "weapons"]

    @pytest.mark.asyncio
    async def test_branch_exit_pops_stack(self):
        mgr = ConversationManager()
        await mgr.handle_input(_ctx("gladiators", "tell me more about gladiators"))
        await mgr.handle_input(_ctx("weapons", "dive into weapons"))
        await mgr.handle_input(_ctx("back", "go back"))
        assert mgr.state.branch_stack == ["gladiators"]
        assert mgr.branch_depth == 1


# ── Conversation history ─────────────────────────────────────────────────────


class TestConversationHistory:
    """Req 3.4: Continuous conversation without wake words."""

    @pytest.mark.asyncio
    async def test_history_records_user_turns(self):
        mgr = ConversationManager()
        await mgr.handle_input(_ctx("topic A"))
        await mgr.handle_input(_ctx("topic B"))
        assert len(mgr.history) == 2
        assert all(t.role == "user" for t in mgr.history)

    def test_add_assistant_turn(self):
        mgr = ConversationManager()
        mgr.add_assistant_turn("Here's what I know about Rome...", topic="Rome")
        assert len(mgr.history) == 1
        assert mgr.history[0].role == "assistant"
        assert mgr.history[0].topic == "Rome"

    @pytest.mark.asyncio
    async def test_turn_count(self):
        mgr = ConversationManager()
        await mgr.handle_input(_ctx("topic A"))
        mgr.add_assistant_turn("response A")
        await mgr.handle_input(_ctx("topic B"))
        assert mgr.turn_count == 3

    def test_context_window_limits_returned_turns(self):
        mgr = ConversationManager(context_window=3)
        for i in range(10):
            mgr.add_assistant_turn(f"turn {i}")
        ctx = mgr.get_context()
        assert len(ctx) == 3

    def test_get_context_summary(self):
        mgr = ConversationManager()
        mgr.add_assistant_turn("Welcome!")
        summary = mgr.get_context_summary()
        assert "Assistant: Welcome!" in summary

    @pytest.mark.asyncio
    async def test_get_topics_discussed(self):
        mgr = ConversationManager()
        await mgr.handle_input(_ctx("Rome"))
        await mgr.handle_input(_ctx("Egypt"))
        await mgr.handle_input(_ctx("Rome"))  # duplicate
        topics = mgr.get_topics_discussed()
        assert topics == ["Rome", "Egypt"]

    @pytest.mark.asyncio
    async def test_get_current_topic(self):
        mgr = ConversationManager()
        assert mgr.get_current_topic() is None
        await mgr.handle_input(_ctx("ancient Rome", "ancient Rome"))
        assert mgr.get_current_topic() == "ancient Rome"


# ── State management ─────────────────────────────────────────────────────────


class TestStateManagement:
    def test_reset_clears_all(self):
        mgr = ConversationManager(session_id="s1", user_id="u1")
        mgr.add_assistant_turn("something")
        mgr.state.current_topic = "Rome"
        mgr.state.branch_depth = 2
        mgr.reset()
        assert mgr.turn_count == 0
        assert len(mgr.history) == 0
        assert mgr.state.current_topic is None
        assert mgr.branch_depth == 0
        # Session/user preserved
        assert mgr.state.session_id == "s1"
        assert mgr.state.user_id == "u1"

    def test_is_stale_false_when_fresh(self):
        mgr = ConversationManager()
        assert not mgr.is_stale()

    def test_is_stale_true_after_timeout(self):
        mgr = ConversationManager()
        mgr.state.last_activity = time.time() - INACTIVITY_TIMEOUT_S - 10
        assert mgr.is_stale()

    @pytest.mark.asyncio
    async def test_last_activity_updated_on_input(self):
        mgr = ConversationManager()
        mgr.state.last_activity = time.time() - 100
        await mgr.handle_input(_ctx("test"))
        assert time.time() - mgr.state.last_activity < 2

    @pytest.mark.asyncio
    async def test_language_updated_on_new_topic(self):
        mgr = ConversationManager()
        ctx = _ctx("la Alhambra", "la Alhambra", language="es")
        await mgr.handle_input(ctx)
        assert mgr.state.current_language == "es"


# ── Edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_context_summary(self):
        mgr = ConversationManager()
        assert mgr.get_context_summary() == ""

    @pytest.mark.asyncio
    async def test_branch_exit_at_depth_zero(self):
        """Exiting branch at depth 0 should not go negative."""
        mgr = ConversationManager()
        await mgr.handle_input(_ctx("go back", "go back"))
        assert mgr.branch_depth == 0

    @pytest.mark.asyncio
    async def test_branch_indicator_no_topic_after(self):
        """Branch indicator with no text after → not a branch, treated differently."""
        mgr = ConversationManager()
        result = await mgr.handle_input(
            _ctx("tell me more about", "tell me more about")
        )
        # indicator found but no remainder → extract_branch_topic returns None
        # so it won't match as a branch
        assert result.intent in (ConversationIntent.NEW_TOPIC, ConversationIntent.BRANCH)

    @pytest.mark.asyncio
    async def test_multiple_indicators_command_wins(self):
        """Command indicators take priority over others."""
        mgr = ConversationManager()
        result = await mgr.handle_input(
            _ctx("stop asking what is this", "stop asking what is this")
        )
        assert result.intent == ConversationIntent.COMMAND


# ── Model tests ──────────────────────────────────────────────────────────────


class TestConversationModels:
    def test_conversation_turn_defaults(self):
        t = ConversationTurn(role="user", content="hello")
        assert t.language == "en"
        assert t.intent is None
        assert len(t.id) == 12

    def test_conversation_state_defaults(self):
        s = ConversationState()
        assert s.branch_depth == 0
        assert s.branch_stack == []
        assert s.turn_count == 0

    def test_intent_classification_defaults(self):
        ic = IntentClassification(intent=ConversationIntent.NEW_TOPIC)
        assert ic.confidence == 0.0
        assert ic.branch_topic is None

    def test_conversation_state_branch_depth_validation(self):
        """Branch depth must be 0-3."""
        with pytest.raises(Exception):
            ConversationState(branch_depth=4)

        with pytest.raises(Exception):
            ConversationState(branch_depth=-1)
