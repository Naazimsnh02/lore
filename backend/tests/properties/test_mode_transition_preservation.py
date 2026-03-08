"""Property test: Mode Transition Content Preservation (Property 1).

Feature: lore-multimodal-documentary-app, Property 1: Mode transition preserves content.

*For any* active session with generated content, when the mode is switched from
any mode to any other mode, all previously generated content (narration, video,
illustrations, facts) shall remain accessible in the session memory.

Validates: Requirements 1.6, 1.7

Strategy:
  - Generate random session content (narrations, illustrations, videos, facts).
  - Perform a random mode switch.
  - Verify that the preserved content snapshot matches what was in the session.
  - Verify the session memory was never asked to delete content.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.services.mode_switch.manager import ModeSwitchManager
from backend.services.mode_switch.models import (
    PreservedContent,
    SwitchableMode,
)


# ── Hypothesis strategies ─────────────────────────────────────────────────────

mode_strategy = st.sampled_from(list(SwitchableMode))

content_ref_strategy = st.fixed_dictionaries({
    "content_id": st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789_",
        min_size=4,
        max_size=16,
    ),
    "content_type": st.sampled_from(["narration", "illustration", "video", "fact"]),
})

branch_strategy = st.fixed_dictionaries({
    "branch_id": st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
        min_size=6,
        max_size=12,
    ),
})

session_strategy = st.fixed_dictionaries({
    "content_refs": st.lists(content_ref_strategy, min_size=0, max_size=20),
    "branches": st.lists(branch_strategy, min_size=0, max_size=5),
    "total_duration": st.floats(min_value=0.0, max_value=3600.0),
})


def _build_mock_memory(session_data: dict) -> AsyncMock:
    """Build a mock session memory from Hypothesis-generated data."""
    session = MagicMock()
    session.content_refs = session_data["content_refs"]
    session.content_counts = None  # Let content_refs be the source of truth
    session.branches = session_data["branches"]
    session.total_duration_seconds = session_data["total_duration"]

    memory = AsyncMock()
    memory.load_session = AsyncMock(return_value=session)
    memory.add_interaction = AsyncMock()
    memory.update_session = AsyncMock()
    # Ensure delete is never called
    memory.delete_session = AsyncMock()
    memory.delete_content = AsyncMock()
    return memory


# ── Property tests ────────────────────────────────────────────────────────────


class TestModeTransitionContentPreservation:
    """Feature: lore-multimodal-documentary-app,
    Property 1: Mode Transition Content Preservation."""

    @pytest.mark.asyncio
    @settings(
        max_examples=120,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        from_mode=mode_strategy,
        to_mode=mode_strategy,
        session_data=session_strategy,
    )
    async def test_content_preserved_across_mode_switch(
        self, from_mode, to_mode, session_data
    ):
        """All content in session memory remains accessible after any mode switch."""
        memory = _build_mock_memory(session_data)
        mgr = ModeSwitchManager(session_memory=memory)

        result = await mgr.switch_mode(
            session_id="prop_test_session",
            user_id="prop_test_user",
            from_mode=from_mode,
            to_mode=to_mode,
        )

        # Count expected content by type
        expected_narrations = sum(
            1 for r in session_data["content_refs"] if r["content_type"] == "narration"
        )
        expected_illustrations = sum(
            1 for r in session_data["content_refs"] if r["content_type"] == "illustration"
        )
        expected_videos = sum(
            1 for r in session_data["content_refs"] if r["content_type"] == "video"
        )
        expected_facts = sum(
            1 for r in session_data["content_refs"] if r["content_type"] == "fact"
        )
        expected_ids = {
            r["content_id"] for r in session_data["content_refs"] if r["content_id"]
        }
        expected_branch_ids = {
            b["branch_id"] for b in session_data["branches"] if b["branch_id"]
        }

        # Property 1: All content counts preserved
        assert result.preserved.narration_count == expected_narrations
        assert result.preserved.illustration_count == expected_illustrations
        assert result.preserved.video_count == expected_videos
        assert result.preserved.fact_count == expected_facts

        # Property 1: All content IDs preserved
        assert set(result.preserved.content_ids) == expected_ids

        # Property 1: All branch IDs preserved
        assert set(result.preserved.branch_ids) == expected_branch_ids

        # Property 1: Duration preserved
        assert result.preserved.total_duration_seconds == session_data["total_duration"]

    @pytest.mark.asyncio
    @settings(
        max_examples=120,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        from_mode=mode_strategy,
        to_mode=mode_strategy,
        session_data=session_strategy,
    )
    async def test_no_content_deleted_on_switch(
        self, from_mode, to_mode, session_data
    ):
        """Mode switching must never delete or modify existing content."""
        memory = _build_mock_memory(session_data)
        mgr = ModeSwitchManager(session_memory=memory)

        await mgr.switch_mode(
            session_id="prop_test_session",
            user_id="prop_test_user",
            from_mode=from_mode,
            to_mode=to_mode,
        )

        # Verify no delete operations were called
        memory.delete_session.assert_not_awaited()
        memory.delete_content.assert_not_awaited()

    @pytest.mark.asyncio
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        modes=st.lists(mode_strategy, min_size=2, max_size=6),
        session_data=session_strategy,
    )
    async def test_multiple_switches_preserve_content(
        self, modes, session_data
    ):
        """Content remains preserved through a chain of mode switches."""
        memory = _build_mock_memory(session_data)
        mgr = ModeSwitchManager(session_memory=memory)

        # Perform a chain of switches
        for i in range(len(modes) - 1):
            result = await mgr.switch_mode(
                session_id="chain_session",
                user_id="chain_user",
                from_mode=modes[i],
                to_mode=modes[i + 1],
            )

        # After all switches, last result should still show all content
        expected_total = len(session_data["content_refs"])
        actual_total = (
            result.preserved.narration_count
            + result.preserved.illustration_count
            + result.preserved.video_count
            + result.preserved.fact_count
        )
        assert actual_total == expected_total

    @pytest.mark.asyncio
    @settings(
        max_examples=120,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        from_mode=mode_strategy,
        to_mode=mode_strategy,
    )
    async def test_switch_always_records_interaction(
        self, from_mode, to_mode
    ):
        """Every mode switch is recorded as a UserInteraction."""
        memory = _build_mock_memory({
            "content_refs": [],
            "branches": [],
            "total_duration": 0.0,
        })
        mgr = ModeSwitchManager(session_memory=memory)

        await mgr.switch_mode(
            session_id="record_session",
            user_id="record_user",
            from_mode=from_mode,
            to_mode=to_mode,
        )

        memory.add_interaction.assert_awaited_once()
        args = memory.add_interaction.call_args[0]
        assert args[0] == "record_session"
        assert args[1].interaction_type.value == "mode_switch"

    @pytest.mark.asyncio
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        from_mode=mode_strategy,
        to_mode=mode_strategy,
    )
    async def test_switch_always_succeeds(self, from_mode, to_mode):
        """All mode transitions succeed (Req 1.6: all switches allowed)."""
        mgr = ModeSwitchManager()

        result = await mgr.switch_mode(
            session_id="any_session",
            user_id="any_user",
            from_mode=from_mode,
            to_mode=to_mode,
        )

        assert result.from_mode == from_mode
        assert result.to_mode == to_mode
        assert result.switch_id  # non-empty
