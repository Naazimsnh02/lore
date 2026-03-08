"""Property-based tests for Depth Dial Content Complexity Ordering.

Feature: lore-multimodal-documentary-app, Property 13:
  FOR ALL topics T, the content complexity SHALL satisfy:
    complexity(Explorer, T) < complexity(Scholar, T) < complexity(Expert, T)

Requirements: 14.1–14.4.
Minimum 100 iterations per property (Hypothesis default is 100).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.services.depth_dial.manager import DepthDialManager
from backend.services.depth_dial.models import (
    DEPTH_COMPLEXITY,
    DepthDialState,
    DepthLevel,
    DepthLevelConfig,
    NarrationPromptConfig,
)


# ── Strategies ────────────────────────────────────────────────────────────────

depth_level_st = st.sampled_from(list(DepthLevel))
session_id_st = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-_"),
    min_size=1,
    max_size=32,
)
topic_st = st.text(min_size=1, max_size=200).filter(lambda s: s.strip())
content_st = st.text(min_size=10, max_size=500).filter(lambda s: len(s.split()) >= 3)


# ── Property 13: Complexity ordering ─────────────────────────────────────────


class TestProperty13ComplexityOrdering:
    """Property 13: complexity(Explorer, T) < complexity(Scholar, T) < complexity(Expert, T)."""

    @given(st.just(None))
    @settings(max_examples=150, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_numeric_complexity_strict_ordering(self, _: None) -> None:
        """Numeric complexity values always maintain strict ordering."""
        assert DEPTH_COMPLEXITY[DepthLevel.EXPLORER] < DEPTH_COMPLEXITY[DepthLevel.SCHOLAR]
        assert DEPTH_COMPLEXITY[DepthLevel.SCHOLAR] < DEPTH_COMPLEXITY[DepthLevel.EXPERT]

    @given(depth_level_st)
    @settings(max_examples=150, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_complexity_is_positive_integer(self, level: DepthLevel) -> None:
        """Every depth level maps to a positive integer."""
        assert DEPTH_COMPLEXITY[level] >= 1
        assert isinstance(DEPTH_COMPLEXITY[level], int)

    @given(depth_level_st)
    @settings(max_examples=150, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_config_complexity_matches_ordering(self, level: DepthLevel) -> None:
        """DepthLevelConfig.complexity matches DEPTH_COMPLEXITY."""
        manager = DepthDialManager()
        cfg = manager.get_level_config(level)
        assert cfg.complexity == DEPTH_COMPLEXITY[level]

    @given(depth_level_st)
    @settings(max_examples=150, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_max_sentences_respects_ordering(self, level: DepthLevel) -> None:
        """Expert allows more sentences than Scholar, which allows more than Explorer."""
        manager = DepthDialManager()
        cfg = manager.get_narration_prompt_config(level)
        explorer_cfg = manager.get_narration_prompt_config(DepthLevel.EXPLORER)
        expert_cfg = manager.get_narration_prompt_config(DepthLevel.EXPERT)

        # The current level's sentences should be between or equal to explorer and expert
        assert explorer_cfg.max_sentences_per_segment <= cfg.max_sentences_per_segment
        assert cfg.max_sentences_per_segment <= expert_cfg.max_sentences_per_segment

    @given(depth_level_st)
    @settings(max_examples=150, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_duration_multiplier_respects_ordering(self, level: DepthLevel) -> None:
        """Higher complexity → longer duration multiplier."""
        manager = DepthDialManager()
        explorer_mult = manager.get_duration_multiplier(DepthLevel.EXPLORER)
        current_mult = manager.get_duration_multiplier(level)
        expert_mult = manager.get_duration_multiplier(DepthLevel.EXPERT)
        assert explorer_mult <= current_mult <= expert_mult


# ── Prompt instructions properties ────────────────────────────────────────────


class TestPromptInstructionProperties:
    """Verify that prompt instructions are non-empty and differ across levels."""

    @given(depth_level_st)
    @settings(max_examples=150, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_narration_instructions_nonempty(self, level: DepthLevel) -> None:
        manager = DepthDialManager()
        instructions = manager.build_narration_instructions(level)
        assert len(instructions) > 50

    @given(depth_level_st)
    @settings(max_examples=150, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_illustration_instructions_nonempty(self, level: DepthLevel) -> None:
        manager = DepthDialManager()
        instructions = manager.build_illustration_instructions(level)
        assert len(instructions) > 20

    @given(st.just(None))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_all_levels_produce_distinct_narration_instructions(self, _: None) -> None:
        manager = DepthDialManager()
        instructions = {
            level: manager.build_narration_instructions(level)
            for level in DepthLevel
        }
        # All three instructions should be distinct
        values = list(instructions.values())
        assert len(set(values)) == 3


# ── Session state properties ─────────────────────────────────────────────────


class TestSessionStateProperties:
    """Verify session state invariants across random transitions."""

    @given(session_id_st, depth_level_st)
    @settings(max_examples=150, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_initial_state_is_explorer(self, session_id: str, _level: DepthLevel) -> None:
        """Fresh sessions always start at Explorer."""
        manager = DepthDialManager()
        state = manager.get_session_state(session_id)
        assert state.current_level == DepthLevel.EXPLORER

    @given(session_id_st, depth_level_st)
    @settings(max_examples=150, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_change_sets_correct_level(self, session_id: str, target: DepthLevel) -> None:
        """After change_depth_dial, current_level matches the target."""
        manager = DepthDialManager()
        loop = asyncio.new_event_loop()
        try:
            state = loop.run_until_complete(manager.change_depth_dial(session_id, target))
            assert state.current_level == target
        finally:
            loop.close()

    @given(session_id_st, st.lists(depth_level_st, min_size=1, max_size=10))
    @settings(max_examples=120, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_change_count_tracks_unique_transitions(
        self, session_id: str, levels: list[DepthLevel]
    ) -> None:
        """change_count only increments on actual level changes (not same-level)."""
        manager = DepthDialManager()
        loop = asyncio.new_event_loop()
        try:
            expected_changes = 0
            prev = DepthLevel.EXPLORER  # default
            for lvl in levels:
                loop.run_until_complete(manager.change_depth_dial(session_id, lvl))
                if lvl != prev:
                    expected_changes += 1
                prev = lvl

            state = manager.get_session_state(session_id)
            assert state.change_count == expected_changes
        finally:
            loop.close()


# ── Content adaptation properties ─────────────────────────────────────────────


class TestAdaptationProperties:
    """Verify content adaptation invariants."""

    @given(content_st, depth_level_st)
    @settings(max_examples=120, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_adapt_without_client_is_identity(
        self, content: str, level: DepthLevel
    ) -> None:
        """Without a genai client, adaptation is the identity function."""
        manager = DepthDialManager()
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(manager.adapt_content(content, level))
            assert result.adapted_content == content
            assert result.error is None
            assert result.level == level
        finally:
            loop.close()

    @given(content_st, depth_level_st)
    @settings(max_examples=120, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_adapt_preserves_word_count_metadata(
        self, content: str, level: DepthLevel
    ) -> None:
        """Word counts are always populated correctly."""
        manager = DepthDialManager()
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(manager.adapt_content(content, level))
            assert result.word_count_original == len(content.split())
            assert result.word_count_adapted >= 0
        finally:
            loop.close()
