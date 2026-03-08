"""Unit tests for the Mode Switch Manager (Task 26).

Requirements tested:
  1.6 — Mode switching during active sessions.
  1.7 — Content preservation on mode switch.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.mode_switch.manager import ModeSwitchManager
from backend.services.mode_switch.models import (
    ModeSwitchContext,
    ModeSwitchError,
    ModeSwitchRecord,
    ModeSwitchResult,
    PreservedContent,
    SwitchableMode,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _mock_session_memory(
    *,
    content_refs: list | None = None,
    content_counts: dict | None = None,
    branches: list | None = None,
    total_duration: float = 0.0,
) -> AsyncMock:
    """Create a mock SessionMemoryManager that returns a fake session."""
    session = MagicMock()
    session.content_refs = content_refs or []
    session.content_counts = content_counts
    session.branches = branches or []
    session.total_duration_seconds = total_duration

    memory = AsyncMock()
    memory.load_session = AsyncMock(return_value=session)
    memory.add_interaction = AsyncMock()
    memory.update_session = AsyncMock()
    return memory


def _mock_session_with_content() -> AsyncMock:
    """Session memory with realistic content."""
    return _mock_session_memory(
        content_refs=[
            {"content_id": "narr_001", "content_type": "narration"},
            {"content_id": "narr_002", "content_type": "narration"},
            {"content_id": "ill_001", "content_type": "illustration"},
            {"content_id": "vid_001", "content_type": "video"},
            {"content_id": "fact_001", "content_type": "fact"},
            {"content_id": "fact_002", "content_type": "fact"},
        ],
        branches=[
            {"branch_id": "branch_001"},
            {"branch_id": "branch_002"},
        ],
        total_duration=120.5,
    )


# ── Basic construction ────────────────────────────────────────────────────────


class TestModeSwitchManagerConstruction:
    def test_creates_without_session_memory(self):
        mgr = ModeSwitchManager()
        assert mgr._session_memory is None

    def test_creates_with_session_memory(self):
        memory = AsyncMock()
        mgr = ModeSwitchManager(session_memory=memory)
        assert mgr._session_memory is memory


# ── Transition validation ─────────────────────────────────────────────────────


class TestTransitionValidation:
    def test_all_transitions_valid(self):
        """Req 1.6: all mode transitions are valid."""
        mgr = ModeSwitchManager()
        for from_mode in SwitchableMode:
            for to_mode in SwitchableMode:
                assert mgr.validate_transition(from_mode, to_mode) is True

    def test_same_mode_transition_valid(self):
        mgr = ModeSwitchManager()
        assert mgr.validate_transition(SwitchableMode.SIGHT, SwitchableMode.SIGHT) is True


# ── Content preservation (Req 1.7) ───────────────────────────────────────────


class TestContentPreservation:
    @pytest.mark.asyncio
    async def test_snapshot_empty_session(self):
        memory = _mock_session_memory()
        mgr = ModeSwitchManager(session_memory=memory)
        preserved = await mgr._snapshot_content("sess_001")
        assert preserved.narration_count == 0
        assert preserved.illustration_count == 0
        assert preserved.video_count == 0
        assert preserved.fact_count == 0
        assert preserved.content_ids == []
        assert preserved.branch_ids == []

    @pytest.mark.asyncio
    async def test_snapshot_with_content(self):
        memory = _mock_session_with_content()
        mgr = ModeSwitchManager(session_memory=memory)
        preserved = await mgr._snapshot_content("sess_001")

        assert preserved.narration_count == 2
        assert preserved.illustration_count == 1
        assert preserved.video_count == 1
        assert preserved.fact_count == 2
        assert len(preserved.content_ids) == 6
        assert "narr_001" in preserved.content_ids
        assert "vid_001" in preserved.content_ids
        assert len(preserved.branch_ids) == 2
        assert preserved.total_duration_seconds == 120.5

    @pytest.mark.asyncio
    async def test_snapshot_without_session_memory(self):
        """Graceful degradation: no session memory → empty snapshot."""
        mgr = ModeSwitchManager()
        preserved = await mgr._snapshot_content("sess_001")
        assert preserved == PreservedContent()

    @pytest.mark.asyncio
    async def test_snapshot_on_load_error(self):
        """Graceful degradation: load failure → empty snapshot."""
        memory = AsyncMock()
        memory.load_session = AsyncMock(side_effect=RuntimeError("Firestore down"))
        mgr = ModeSwitchManager(session_memory=memory)
        preserved = await mgr._snapshot_content("sess_001")
        assert preserved == PreservedContent()

    @pytest.mark.asyncio
    async def test_snapshot_uses_content_counts_when_higher(self):
        memory = _mock_session_memory(
            content_refs=[{"content_id": "n1", "content_type": "narration"}],
            content_counts={
                "narration_segments": 5,
                "illustrations": 3,
                "video_clips": 2,
                "facts": 4,
            },
        )
        mgr = ModeSwitchManager(session_memory=memory)
        preserved = await mgr._snapshot_content("sess_001")
        # content_counts has higher values, so those are used
        assert preserved.narration_count == 5
        assert preserved.illustration_count == 3
        assert preserved.video_count == 2
        assert preserved.fact_count == 4


# ── switch_mode ───────────────────────────────────────────────────────────────


class TestSwitchMode:
    @pytest.mark.asyncio
    async def test_basic_switch(self):
        memory = _mock_session_with_content()
        mgr = ModeSwitchManager(session_memory=memory)

        result = await mgr.switch_mode(
            session_id="sess_001",
            user_id="user_001",
            from_mode=SwitchableMode.SIGHT,
            to_mode=SwitchableMode.VOICE,
        )

        assert isinstance(result, ModeSwitchResult)
        assert result.from_mode == SwitchableMode.SIGHT
        assert result.to_mode == SwitchableMode.VOICE
        assert result.session_id == "sess_001"
        assert result.switch_id  # non-empty
        assert result.preserved.narration_count == 2
        assert "preserved" in result.transition_message.lower() or "switching" in result.transition_message.lower()

    @pytest.mark.asyncio
    async def test_switch_persists_interaction(self):
        memory = _mock_session_with_content()
        mgr = ModeSwitchManager(session_memory=memory)

        await mgr.switch_mode(
            session_id="sess_001",
            user_id="user_001",
            from_mode=SwitchableMode.SIGHT,
            to_mode=SwitchableMode.LORE,
        )

        # Verify add_interaction was called
        memory.add_interaction.assert_awaited_once()
        call_args = memory.add_interaction.call_args
        assert call_args[0][0] == "sess_001"  # session_id
        interaction = call_args[0][1]
        assert interaction.interaction_type.value == "mode_switch"

    @pytest.mark.asyncio
    async def test_switch_updates_session_mode(self):
        memory = _mock_session_with_content()
        mgr = ModeSwitchManager(session_memory=memory)

        await mgr.switch_mode(
            session_id="sess_001",
            user_id="user_001",
            from_mode=SwitchableMode.VOICE,
            to_mode=SwitchableMode.LORE,
        )

        memory.update_session.assert_awaited_once()
        call_kwargs = memory.update_session.call_args
        assert call_kwargs[0][0] == "sess_001"

    @pytest.mark.asyncio
    async def test_switch_without_session_memory(self):
        """Manager works even without session memory (no persistence)."""
        mgr = ModeSwitchManager()
        result = await mgr.switch_mode(
            session_id="sess_001",
            user_id="user_001",
            from_mode=SwitchableMode.SIGHT,
            to_mode=SwitchableMode.VOICE,
        )
        assert result.from_mode == SwitchableMode.SIGHT
        assert result.to_mode == SwitchableMode.VOICE

    @pytest.mark.asyncio
    async def test_switch_records_in_history(self):
        mgr = ModeSwitchManager()
        await mgr.switch_mode(
            session_id="sess_001",
            user_id="user_001",
            from_mode=SwitchableMode.SIGHT,
            to_mode=SwitchableMode.VOICE,
        )
        await mgr.switch_mode(
            session_id="sess_001",
            user_id="user_001",
            from_mode=SwitchableMode.VOICE,
            to_mode=SwitchableMode.LORE,
        )

        history = mgr.get_switch_history("sess_001")
        assert len(history) == 2
        assert history[0].from_mode == SwitchableMode.SIGHT
        assert history[1].to_mode == SwitchableMode.LORE

    @pytest.mark.asyncio
    async def test_switch_graceful_on_interaction_error(self):
        """Switch succeeds even if add_interaction fails."""
        memory = _mock_session_with_content()
        memory.add_interaction = AsyncMock(side_effect=RuntimeError("write fail"))
        mgr = ModeSwitchManager(session_memory=memory)

        result = await mgr.switch_mode(
            session_id="sess_001",
            user_id="user_001",
            from_mode=SwitchableMode.SIGHT,
            to_mode=SwitchableMode.VOICE,
        )
        assert result.to_mode == SwitchableMode.VOICE

    @pytest.mark.asyncio
    async def test_switch_graceful_on_update_error(self):
        """Switch succeeds even if update_session fails."""
        memory = _mock_session_with_content()
        memory.update_session = AsyncMock(side_effect=RuntimeError("update fail"))
        mgr = ModeSwitchManager(session_memory=memory)

        result = await mgr.switch_mode(
            session_id="sess_001",
            user_id="user_001",
            from_mode=SwitchableMode.SIGHT,
            to_mode=SwitchableMode.LORE,
        )
        assert result.to_mode == SwitchableMode.LORE

    @pytest.mark.asyncio
    async def test_all_mode_pairs(self):
        """Every pair of modes can be switched between (Req 1.6)."""
        mgr = ModeSwitchManager()
        for from_mode in SwitchableMode:
            for to_mode in SwitchableMode:
                result = await mgr.switch_mode(
                    session_id="sess_pairs",
                    user_id="user_001",
                    from_mode=from_mode,
                    to_mode=to_mode,
                )
                assert result.from_mode == from_mode
                assert result.to_mode == to_mode


# ── get_current_mode ──────────────────────────────────────────────────────────


class TestGetCurrentMode:
    def test_default_when_no_history(self):
        mgr = ModeSwitchManager()
        assert mgr.get_current_mode("sess_001") == SwitchableMode.SIGHT

    def test_custom_default(self):
        mgr = ModeSwitchManager()
        assert mgr.get_current_mode("sess_001", default=SwitchableMode.LORE) == SwitchableMode.LORE

    @pytest.mark.asyncio
    async def test_tracks_latest_mode(self):
        mgr = ModeSwitchManager()
        await mgr.switch_mode(
            session_id="sess_001",
            user_id="u1",
            from_mode=SwitchableMode.SIGHT,
            to_mode=SwitchableMode.VOICE,
        )
        assert mgr.get_current_mode("sess_001") == SwitchableMode.VOICE

        await mgr.switch_mode(
            session_id="sess_001",
            user_id="u1",
            from_mode=SwitchableMode.VOICE,
            to_mode=SwitchableMode.LORE,
        )
        assert mgr.get_current_mode("sess_001") == SwitchableMode.LORE


# ── reset ─────────────────────────────────────────────────────────────────────


class TestReset:
    @pytest.mark.asyncio
    async def test_reset_clears_history(self):
        mgr = ModeSwitchManager()
        await mgr.switch_mode(
            session_id="sess_001",
            user_id="u1",
            from_mode=SwitchableMode.SIGHT,
            to_mode=SwitchableMode.VOICE,
        )
        assert len(mgr.get_switch_history("sess_001")) == 1

        mgr.reset("sess_001")
        assert mgr.get_switch_history("sess_001") == []
        assert mgr.get_current_mode("sess_001") == SwitchableMode.SIGHT

    def test_reset_nonexistent_session(self):
        mgr = ModeSwitchManager()
        mgr.reset("nonexistent")  # no error


# ── Transition messages ───────────────────────────────────────────────────────


class TestTransitionMessages:
    def test_message_with_content(self):
        msg = ModeSwitchManager._build_transition_message(
            SwitchableMode.SIGHT,
            SwitchableMode.VOICE,
            PreservedContent(narration_count=3, fact_count=2),
        )
        assert "5 content items" in msg
        assert "SightMode" in msg
        assert "VoiceMode" in msg

    def test_message_without_content(self):
        msg = ModeSwitchManager._build_transition_message(
            SwitchableMode.VOICE,
            SwitchableMode.LORE,
            PreservedContent(),
        )
        assert "preserved" not in msg.lower()
        assert "VoiceMode" in msg
        assert "LoreMode" in msg


# ── Models ────────────────────────────────────────────────────────────────────


class TestModels:
    def test_preserved_content_defaults(self):
        p = PreservedContent()
        assert p.narration_count == 0
        assert p.content_ids == []
        assert p.branch_ids == []
        assert p.total_duration_seconds == 0.0

    def test_switch_record_auto_id(self):
        r = ModeSwitchRecord(
            from_mode=SwitchableMode.SIGHT,
            to_mode=SwitchableMode.VOICE,
        )
        assert len(r.switch_id) == 12

    def test_switch_result_fields(self):
        r = ModeSwitchResult(
            switch_id="abc123",
            from_mode=SwitchableMode.SIGHT,
            to_mode=SwitchableMode.LORE,
            preserved=PreservedContent(narration_count=1),
            session_id="s1",
        )
        assert r.switch_id == "abc123"
        assert r.preserved.narration_count == 1

    def test_switchable_mode_values(self):
        assert SwitchableMode.SIGHT.value == "sight"
        assert SwitchableMode.VOICE.value == "voice"
        assert SwitchableMode.LORE.value == "lore"

    def test_mode_switch_error(self):
        err = ModeSwitchError("test error")
        assert str(err) == "test error"

    def test_mode_switch_context(self):
        ctx = ModeSwitchContext(
            session_id="s1",
            user_id="u1",
            from_mode=SwitchableMode.SIGHT,
            to_mode=SwitchableMode.VOICE,
            preserved=PreservedContent(),
        )
        assert ctx.session_id == "s1"
        assert ctx.depth_dial == "explorer"
        assert ctx.language == "en"
