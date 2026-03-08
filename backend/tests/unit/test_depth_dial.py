"""Unit tests for the Depth Dial configuration service (Task 24).

Requirements: 14.1–14.6.
Property 13: complexity(Explorer) < complexity(Scholar) < complexity(Expert).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.depth_dial.manager import DepthDialManager
from backend.services.depth_dial.models import (
    ContentAdaptationResult,
    DEPTH_COMPLEXITY,
    DepthDialState,
    DepthLevel,
    DepthLevelConfig,
    NarrationPromptConfig,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def manager() -> DepthDialManager:
    """DepthDialManager with no genai client (offline mode)."""
    return DepthDialManager()


@pytest.fixture
def mock_genai_client() -> MagicMock:
    """Mock google.genai.Client that returns adapted text."""
    client = MagicMock()
    response = MagicMock()
    response.text = "Adapted content from LLM"
    client.aio.models.generate_content = AsyncMock(return_value=response)
    return client


@pytest.fixture
def manager_with_client(mock_genai_client: MagicMock) -> DepthDialManager:
    """DepthDialManager with a mock genai client."""
    return DepthDialManager(genai_client=mock_genai_client)


# ── Req 14.1: Three depth levels ─────────────────────────────────────────────


class TestDepthLevels:
    """Verify the three depth levels exist and are properly configured."""

    def test_three_levels_exist(self) -> None:
        """Req 14.1: Explorer, Scholar, Expert."""
        assert DepthLevel.EXPLORER == "explorer"
        assert DepthLevel.SCHOLAR == "scholar"
        assert DepthLevel.EXPERT == "expert"
        assert len(DepthLevel) == 3

    def test_all_levels_have_configs(self, manager: DepthDialManager) -> None:
        configs = manager.get_all_configs()
        assert set(configs.keys()) == {DepthLevel.EXPLORER, DepthLevel.SCHOLAR, DepthLevel.EXPERT}

    def test_complexity_ordering(self) -> None:
        """Property 13: Explorer < Scholar < Expert."""
        assert DEPTH_COMPLEXITY[DepthLevel.EXPLORER] < DEPTH_COMPLEXITY[DepthLevel.SCHOLAR]
        assert DEPTH_COMPLEXITY[DepthLevel.SCHOLAR] < DEPTH_COMPLEXITY[DepthLevel.EXPERT]

    def test_get_complexity(self, manager: DepthDialManager) -> None:
        assert manager.get_complexity(DepthLevel.EXPLORER) == 1
        assert manager.get_complexity(DepthLevel.SCHOLAR) == 2
        assert manager.get_complexity(DepthLevel.EXPERT) == 3


# ── Level config properties ──────────────────────────────────────────────────


class TestLevelConfigs:
    """Verify each level's configuration parameters."""

    def test_explorer_config(self, manager: DepthDialManager) -> None:
        cfg = manager.get_level_config(DepthLevel.EXPLORER)
        assert cfg.complexity == 1
        assert cfg.vocabulary == "simple"
        assert cfg.detail_level == "overview"
        assert cfg.technical_depth == "minimal"
        assert cfg.examples == "many"
        assert cfg.duration_multiplier == 1.0

    def test_scholar_config(self, manager: DepthDialManager) -> None:
        cfg = manager.get_level_config(DepthLevel.SCHOLAR)
        assert cfg.complexity == 2
        assert cfg.vocabulary == "intermediate"
        assert cfg.detail_level == "detailed"
        assert cfg.technical_depth == "moderate"
        assert cfg.examples == "some"
        assert cfg.duration_multiplier == 1.5

    def test_expert_config(self, manager: DepthDialManager) -> None:
        cfg = manager.get_level_config(DepthLevel.EXPERT)
        assert cfg.complexity == 3
        assert cfg.vocabulary == "advanced"
        assert cfg.detail_level == "comprehensive"
        assert cfg.technical_depth == "deep"
        assert cfg.examples == "few"
        assert cfg.duration_multiplier == 2.0

    def test_duration_multiplier_ordering(self, manager: DepthDialManager) -> None:
        e = manager.get_duration_multiplier(DepthLevel.EXPLORER)
        s = manager.get_duration_multiplier(DepthLevel.SCHOLAR)
        x = manager.get_duration_multiplier(DepthLevel.EXPERT)
        assert e < s < x


# ── Prompt-based adaptation (primary path) ────────────────────────────────────


class TestNarrationPromptConfig:
    """Verify prompt engineering configs per level."""

    def test_explorer_prompt(self, manager: DepthDialManager) -> None:
        cfg = manager.get_narration_prompt_config(DepthLevel.EXPLORER)
        assert isinstance(cfg, NarrationPromptConfig)
        assert "simple" in cfg.system_instruction.lower() or "general" in cfg.system_instruction.lower()
        assert cfg.target_reading_level == "grade 6"
        assert cfg.max_sentences_per_segment == 5

    def test_scholar_prompt(self, manager: DepthDialManager) -> None:
        cfg = manager.get_narration_prompt_config(DepthLevel.SCHOLAR)
        assert "intermediate" in cfg.vocabulary_instruction.lower()
        assert cfg.target_reading_level == "undergraduate"
        assert cfg.max_sentences_per_segment == 8

    def test_expert_prompt(self, manager: DepthDialManager) -> None:
        cfg = manager.get_narration_prompt_config(DepthLevel.EXPERT)
        assert "technical" in cfg.system_instruction.lower()
        assert cfg.target_reading_level == "postgraduate"
        assert cfg.max_sentences_per_segment == 12

    def test_max_sentences_ordering(self, manager: DepthDialManager) -> None:
        e = manager.get_narration_prompt_config(DepthLevel.EXPLORER).max_sentences_per_segment
        s = manager.get_narration_prompt_config(DepthLevel.SCHOLAR).max_sentences_per_segment
        x = manager.get_narration_prompt_config(DepthLevel.EXPERT).max_sentences_per_segment
        assert e < s < x

    def test_build_narration_instructions(self, manager: DepthDialManager) -> None:
        for level in DepthLevel:
            instructions = manager.build_narration_instructions(level)
            assert isinstance(instructions, str)
            assert len(instructions) > 50
            assert "Vocabulary:" in instructions
            assert "Detail:" in instructions
            assert "sentences per segment" in instructions

    def test_build_illustration_instructions(self, manager: DepthDialManager) -> None:
        for level in DepthLevel:
            instructions = manager.build_illustration_instructions(level)
            assert isinstance(instructions, str)
            assert len(instructions) > 20

    def test_illustration_instructions_differ(self, manager: DepthDialManager) -> None:
        e = manager.build_illustration_instructions(DepthLevel.EXPLORER)
        s = manager.build_illustration_instructions(DepthLevel.SCHOLAR)
        x = manager.build_illustration_instructions(DepthLevel.EXPERT)
        assert e != s != x


# ── Post-hoc content adaptation ───────────────────────────────────────────────


class TestContentAdaptation:
    """Test adapt_content and convenience methods."""

    @pytest.mark.asyncio
    async def test_adapt_without_client_returns_original(self, manager: DepthDialManager) -> None:
        """Graceful degradation: no LLM → return original unchanged."""
        result = await manager.adapt_content("Complex scientific text.", DepthLevel.EXPLORER)
        assert result.adapted_content == "Complex scientific text."
        assert result.error is None
        assert result.level == DepthLevel.EXPLORER

    @pytest.mark.asyncio
    async def test_adapt_with_client_calls_llm(
        self, manager_with_client: DepthDialManager, mock_genai_client: MagicMock
    ) -> None:
        result = await manager_with_client.adapt_content("Complex text.", DepthLevel.EXPLORER)
        assert result.adapted_content == "Adapted content from LLM"
        assert result.error is None
        mock_genai_client.aio.models.generate_content.assert_called_once()

    @pytest.mark.asyncio
    async def test_adapt_explorer_simplification(
        self, manager_with_client: DepthDialManager, mock_genai_client: MagicMock
    ) -> None:
        """Req 14.2: Explorer generates introductory content."""
        await manager_with_client.adapt_content("Text", DepthLevel.EXPLORER)
        call_args = mock_genai_client.aio.models.generate_content.call_args
        prompt = call_args.kwargs.get("contents", call_args[1].get("contents", ""))
        assert "simple" in prompt.lower() or "general audience" in prompt.lower()

    @pytest.mark.asyncio
    async def test_adapt_scholar_context(
        self, manager_with_client: DepthDialManager, mock_genai_client: MagicMock
    ) -> None:
        """Req 14.3: Scholar generates intermediate content."""
        await manager_with_client.adapt_content("Text", DepthLevel.SCHOLAR)
        call_args = mock_genai_client.aio.models.generate_content.call_args
        prompt = call_args.kwargs.get("contents", call_args[1].get("contents", ""))
        assert "context" in prompt.lower() or "educated" in prompt.lower()

    @pytest.mark.asyncio
    async def test_adapt_expert_technical(
        self, manager_with_client: DepthDialManager, mock_genai_client: MagicMock
    ) -> None:
        """Req 14.4: Expert generates advanced content."""
        await manager_with_client.adapt_content("Text", DepthLevel.EXPERT)
        call_args = mock_genai_client.aio.models.generate_content.call_args
        prompt = call_args.kwargs.get("contents", call_args[1].get("contents", ""))
        assert "technical" in prompt.lower() or "expert" in prompt.lower()

    @pytest.mark.asyncio
    async def test_adapt_with_topic_hint(
        self, manager_with_client: DepthDialManager, mock_genai_client: MagicMock
    ) -> None:
        await manager_with_client.adapt_content("Text", DepthLevel.SCHOLAR, topic="Roman Colosseum")
        call_args = mock_genai_client.aio.models.generate_content.call_args
        prompt = call_args.kwargs.get("contents", call_args[1].get("contents", ""))
        assert "Roman Colosseum" in prompt

    @pytest.mark.asyncio
    async def test_adapt_with_language(
        self, manager_with_client: DepthDialManager, mock_genai_client: MagicMock
    ) -> None:
        await manager_with_client.adapt_content("Text", DepthLevel.EXPLORER, language="fr")
        call_args = mock_genai_client.aio.models.generate_content.call_args
        prompt = call_args.kwargs.get("contents", call_args[1].get("contents", ""))
        assert "'fr'" in prompt

    @pytest.mark.asyncio
    async def test_adapt_llm_error_graceful(self, mock_genai_client: MagicMock) -> None:
        """Graceful degradation: LLM error → return original with error field."""
        mock_genai_client.aio.models.generate_content = AsyncMock(
            side_effect=RuntimeError("API down")
        )
        mgr = DepthDialManager(genai_client=mock_genai_client)
        result = await mgr.adapt_content("Original text.", DepthLevel.EXPERT)
        assert result.adapted_content == "Original text."
        assert result.error is not None
        assert "API down" in result.error

    @pytest.mark.asyncio
    async def test_adapt_empty_response(self, mock_genai_client: MagicMock) -> None:
        """LLM returns empty text → fallback to original."""
        response = MagicMock()
        response.text = ""
        mock_genai_client.aio.models.generate_content = AsyncMock(return_value=response)
        mgr = DepthDialManager(genai_client=mock_genai_client)
        result = await mgr.adapt_content("Hello world.", DepthLevel.EXPLORER)
        assert result.adapted_content == "Hello world."

    @pytest.mark.asyncio
    async def test_adapt_none_response(self, mock_genai_client: MagicMock) -> None:
        response = MagicMock()
        response.text = None
        mock_genai_client.aio.models.generate_content = AsyncMock(return_value=response)
        mgr = DepthDialManager(genai_client=mock_genai_client)
        result = await mgr.adapt_content("Hello world.", DepthLevel.EXPLORER)
        assert result.adapted_content == "Hello world."

    @pytest.mark.asyncio
    async def test_word_counts(
        self, manager_with_client: DepthDialManager
    ) -> None:
        result = await manager_with_client.adapt_content(
            "One two three four five.", DepthLevel.SCHOLAR
        )
        assert result.word_count_original == 5
        assert result.word_count_adapted > 0

    @pytest.mark.asyncio
    async def test_simplify_convenience(self, manager: DepthDialManager) -> None:
        text = await manager.simplify_content("Complex text")
        assert text == "Complex text"  # No client → passthrough

    @pytest.mark.asyncio
    async def test_add_context_convenience(self, manager: DepthDialManager) -> None:
        text = await manager.add_context("Some text")
        assert text == "Some text"

    @pytest.mark.asyncio
    async def test_add_technical_depth_convenience(self, manager: DepthDialManager) -> None:
        text = await manager.add_technical_depth("Some text")
        assert text == "Some text"


# ── Session state management (Req 14.5, 14.6) ────────────────────────────────


class TestSessionState:
    """Test per-session depth dial state and runtime changes."""

    def test_default_level_is_explorer(self, manager: DepthDialManager) -> None:
        state = manager.get_session_state("sess-1")
        assert state.current_level == DepthLevel.EXPLORER
        assert state.previous_level is None
        assert state.change_count == 0

    def test_get_current_level(self, manager: DepthDialManager) -> None:
        assert manager.get_current_level("sess-1") == DepthLevel.EXPLORER

    @pytest.mark.asyncio
    async def test_change_depth_dial(self, manager: DepthDialManager) -> None:
        """Req 14.5: Allow adjustment during active stream."""
        state = await manager.change_depth_dial("sess-1", DepthLevel.SCHOLAR)
        assert state.current_level == DepthLevel.SCHOLAR
        assert state.previous_level == DepthLevel.EXPLORER
        assert state.change_count == 1

    @pytest.mark.asyncio
    async def test_change_preserves_history(self, manager: DepthDialManager) -> None:
        await manager.change_depth_dial("sess-1", DepthLevel.SCHOLAR)
        state = await manager.change_depth_dial("sess-1", DepthLevel.EXPERT)
        assert state.current_level == DepthLevel.EXPERT
        assert state.previous_level == DepthLevel.SCHOLAR
        assert state.change_count == 2

    @pytest.mark.asyncio
    async def test_change_same_level_noop(self, manager: DepthDialManager) -> None:
        await manager.change_depth_dial("sess-1", DepthLevel.SCHOLAR)
        state = await manager.change_depth_dial("sess-1", DepthLevel.SCHOLAR)
        assert state.change_count == 1  # No increment

    @pytest.mark.asyncio
    async def test_change_persists_to_session_memory(self, manager: DepthDialManager) -> None:
        """Req 14.6: Adapt subsequent content without restart → persist to memory."""
        mock_memory = MagicMock()
        mock_memory.update_session = AsyncMock()

        await manager.change_depth_dial("sess-1", DepthLevel.EXPERT, session_memory=mock_memory)

        mock_memory.update_session.assert_called_once_with(
            "sess-1",
            {"depth_dial": "expert"},
        )

    @pytest.mark.asyncio
    async def test_change_memory_error_graceful(self, manager: DepthDialManager) -> None:
        """Session memory failure should not break depth dial change."""
        mock_memory = MagicMock()
        mock_memory.update_session = AsyncMock(side_effect=RuntimeError("Firestore down"))

        state = await manager.change_depth_dial("sess-1", DepthLevel.SCHOLAR, session_memory=mock_memory)
        # State should still update even if persistence fails
        assert state.current_level == DepthLevel.SCHOLAR

    @pytest.mark.asyncio
    async def test_multiple_sessions_independent(self, manager: DepthDialManager) -> None:
        await manager.change_depth_dial("sess-1", DepthLevel.EXPERT)
        await manager.change_depth_dial("sess-2", DepthLevel.SCHOLAR)

        assert manager.get_current_level("sess-1") == DepthLevel.EXPERT
        assert manager.get_current_level("sess-2") == DepthLevel.SCHOLAR

    def test_reset_session(self, manager: DepthDialManager) -> None:
        manager.get_session_state("sess-1")
        manager.reset_session("sess-1")
        # New state should be default
        state = manager.get_session_state("sess-1")
        assert state.current_level == DepthLevel.EXPLORER
        assert state.change_count == 0

    def test_reset_nonexistent_session(self, manager: DepthDialManager) -> None:
        manager.reset_session("nonexistent")  # Should not raise


# ── Integration with ConnectionManager ────────────────────────────────────────


class TestConnectionManagerIntegration:
    """Verify depth dial manager can be stored per-client."""

    def test_set_and_get_depth_dial_manager(self) -> None:
        from backend.services.websocket_gateway.connection_manager import ConnectionManager

        cm = ConnectionManager()
        mgr = DepthDialManager()
        cm.set_depth_dial_manager("client-1", mgr)

        retrieved = cm.get_depth_dial_manager("client-1")
        assert retrieved is mgr

    def test_get_nonexistent_returns_none(self) -> None:
        from backend.services.websocket_gateway.connection_manager import ConnectionManager

        cm = ConnectionManager()
        assert cm.get_depth_dial_manager("client-1") is None

    def test_cleanup_removes_depth_dial_manager(self) -> None:
        from backend.services.websocket_gateway.connection_manager import ConnectionManager
        from backend.services.websocket_gateway.models import ConnectionInfo

        cm = ConnectionManager()
        mgr = DepthDialManager()

        # Simulate a disconnected client with old last_seen
        cm._info["stale-client"] = ConnectionInfo(
            client_id="stale-client",
            user_id="user-1",
            connected_at=0.0,
            last_seen=0.0,  # Very old
        )
        cm._depth_dial_managers["stale-client"] = mgr

        cm.cleanup_stale_buffers()

        assert cm.get_depth_dial_manager("stale-client") is None


# ── Orchestrator integration ──────────────────────────────────────────────────


class TestOrchestratorIntegration:
    """Verify DepthDialManager parameter in Orchestrator."""

    def test_orchestrator_accepts_depth_dial_manager(self) -> None:
        from backend.services.orchestrator.orchestrator import DocumentaryOrchestrator

        mgr = DepthDialManager()
        orch = DocumentaryOrchestrator(depth_dial_manager=mgr)
        assert orch._depth_dial is mgr

    def test_orchestrator_works_without_depth_dial(self) -> None:
        from backend.services.orchestrator.orchestrator import DocumentaryOrchestrator

        orch = DocumentaryOrchestrator()
        assert orch._depth_dial is None
