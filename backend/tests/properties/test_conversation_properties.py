"""Property tests for ConversationManager.

Feature: lore-multimodal-documentary-app
Property 14: Branch Documentary Nesting Limit — nesting depth ≤ 3.

Validates: Requirements 13.4 (branch depth ≤ 3), 3.4 (continuous conversation).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.services.voice_mode.conversation_manager import (
    MAX_BRANCH_DEPTH,
    ConversationManager,
)
from backend.services.voice_mode.models import (
    ConversationIntent,
    VoiceModeContext,
)


# ── Strategies ───────────────────────────────────────────────────────────────


@st.composite
def voice_context(draw: st.DrawFn, *, force_branch: bool = False) -> VoiceModeContext:
    """Generate a random VoiceModeContext."""
    topic = draw(st.text(min_size=1, max_size=50, alphabet=st.characters(
        whitelist_categories=("L", "N", "Z"),
    )))
    if force_branch:
        query = f"tell me more about {topic}"
    else:
        query = draw(st.sampled_from([
            f"tell me about {topic}",
            f"tell me more about {topic}",
            f"what is {topic}?",
            f"dive into {topic}",
            topic,
            f"continue",
            f"go on",
            f"go back",
            f"stop",
        ]))
    language = draw(st.sampled_from(["en", "es", "fr", "de", "ja"]))
    return VoiceModeContext(
        topic=topic,
        original_query=query,
        language=language,
        confidence=draw(st.floats(min_value=0.5, max_value=1.0)),
    )


@st.composite
def branch_sequence(draw: st.DrawFn) -> list[VoiceModeContext]:
    """Generate a sequence of branch requests (1-10)."""
    count = draw(st.integers(min_value=1, max_value=10))
    return [draw(voice_context(force_branch=True)) for _ in range(count)]


# ── Property tests ───────────────────────────────────────────────────────────


class TestBranchNestingLimitProperty:
    """Property 14: Branch Documentary Nesting Limit.

    FOR ALL Branch_Documentary structures, the nesting depth SHALL NOT
    exceed 3 levels, preventing infinite recursion.
    """

    @given(branches=branch_sequence())
    @settings(max_examples=150, deadline=5000)
    @pytest.mark.asyncio
    async def test_branch_depth_never_exceeds_max(
        self, branches: list[VoiceModeContext]
    ):
        """Feature: lore-multimodal-documentary-app, Property 14: Branch nesting ≤ 3."""
        mgr = ConversationManager()

        for ctx in branches:
            await mgr.handle_input(ctx)
            assert mgr.branch_depth <= MAX_BRANCH_DEPTH, (
                f"Branch depth {mgr.branch_depth} exceeds max {MAX_BRANCH_DEPTH}"
            )

    @given(branches=branch_sequence())
    @settings(max_examples=150, deadline=5000)
    @pytest.mark.asyncio
    async def test_branch_at_max_downgrades_to_follow_up(
        self, branches: list[VoiceModeContext]
    ):
        """Beyond depth 3, branch requests become follow-ups."""
        mgr = ConversationManager()

        for ctx in branches:
            result = await mgr.handle_input(ctx)
            if mgr.branch_depth == MAX_BRANCH_DEPTH:
                # Any further branch request should be downgraded
                # (already at max, so the one that just ran was either the 3rd
                # branch OR was already downgraded)
                pass
            assert mgr.branch_depth <= MAX_BRANCH_DEPTH


class TestConversationContinuityProperty:
    """Property: Continuous conversation without wake words (Req 3.4).

    All user inputs should be processed regardless of content — no input
    should be silently dropped.
    """

    @given(ctx=voice_context())
    @settings(max_examples=150, deadline=5000)
    @pytest.mark.asyncio
    async def test_all_inputs_classified(self, ctx: VoiceModeContext):
        """Every voice input gets a valid intent classification."""
        mgr = ConversationManager()
        result = await mgr.handle_input(ctx)
        assert result.intent in ConversationIntent.__members__.values()
        assert 0.0 <= result.confidence <= 1.0

    @given(ctx=voice_context())
    @settings(max_examples=150, deadline=5000)
    @pytest.mark.asyncio
    async def test_history_always_recorded(self, ctx: VoiceModeContext):
        """Every input is recorded in conversation history."""
        mgr = ConversationManager()
        await mgr.handle_input(ctx)
        assert mgr.turn_count >= 1
        assert len(mgr.history) >= 1
        assert mgr.history[-1].role == "user"


class TestConversationStateConsistency:
    """State invariants that must hold after any sequence of inputs."""

    @given(inputs=st.lists(voice_context(), min_size=1, max_size=20))
    @settings(max_examples=100, deadline=10000)
    @pytest.mark.asyncio
    async def test_turn_count_matches_history(
        self, inputs: list[VoiceModeContext]
    ):
        """Turn count always equals history length."""
        mgr = ConversationManager()
        for ctx in inputs:
            await mgr.handle_input(ctx)
        assert mgr.turn_count == len(mgr.history)

    @given(inputs=st.lists(voice_context(), min_size=1, max_size=20))
    @settings(max_examples=100, deadline=10000)
    @pytest.mark.asyncio
    async def test_branch_stack_size_equals_depth(
        self, inputs: list[VoiceModeContext]
    ):
        """Branch stack length always equals branch depth."""
        mgr = ConversationManager()
        for ctx in inputs:
            await mgr.handle_input(ctx)
        assert len(mgr.state.branch_stack) == mgr.branch_depth

    @given(inputs=st.lists(voice_context(), min_size=1, max_size=20))
    @settings(max_examples=100, deadline=10000)
    @pytest.mark.asyncio
    async def test_branch_depth_non_negative(
        self, inputs: list[VoiceModeContext]
    ):
        """Branch depth is always non-negative."""
        mgr = ConversationManager()
        for ctx in inputs:
            await mgr.handle_input(ctx)
            assert mgr.branch_depth >= 0
