"""Property-based tests for the Orchestrator service.

Property 5:  Input-to-Output Latency — first output < 3 seconds
Property 19: Agent Retry Behavior — failed tasks retried up to 3 times

Feature: lore-multimodal-documentary-app
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.services.orchestrator.models import (
    ContentElement,
    ContentElementType,
    DocumentaryRequest,
    DocumentaryStream,
    Mode,
)
from backend.services.orchestrator.orchestrator import (
    DocumentaryOrchestrator,
    MAX_RETRIES,
)


# ── Strategies ────────────────────────────────────────────────────────────────

modes = st.sampled_from(list(Mode))
depth_dials = st.sampled_from(["explorer", "scholar", "expert"])
languages = st.sampled_from(["en", "fr", "de", "es", "ja", "ar", "zh"])
topics = st.text(min_size=1, max_size=100, alphabet=st.characters(whitelist_categories=("L", "N", "Z")))


def _fake_narration_script():
    """Return a mock NarrationScript."""
    from backend.services.narration_engine.models import (
        EmotionalTone,
        NarrationScript,
        NarrationSegment,
    )
    return NarrationScript(
        segments=[
            NarrationSegment(text="Test narration.", duration=3.0, tone=EmotionalTone.NEUTRAL),
        ],
        total_duration=3.0,
        language="en",
    )


def _build_fast_orchestrator() -> DocumentaryOrchestrator:
    """Build an orchestrator with fast mocks for latency testing."""
    narration_mock = AsyncMock()
    narration_mock.generate_script = AsyncMock(return_value=_fake_narration_script())

    illustrator_mock = AsyncMock()
    illustrator_mock.generate_batch = AsyncMock(return_value=[])

    grounder_mock = AsyncMock()
    grounder_mock.verify_batch = AsyncMock(return_value=[])

    return DocumentaryOrchestrator(
        narration_engine=narration_mock,
        nano_illustrator=illustrator_mock,
        search_grounder=grounder_mock,
    )


# ── Property 5: Input-to-Output Latency ──────────────────────────────────────


class TestProperty5InputToOutputLatency:
    """Property 5: Input-to-Output Latency.

    Feature: lore-multimodal-documentary-app, Property 5: First output
    appears within 3 seconds of input.
    Validates: Requirements 5.7.
    """

    @pytest.mark.asyncio
    @given(
        topic=topics,
        depth=depth_dials,
        lang=languages,
    )
    @settings(
        max_examples=100,
        deadline=10_000,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    async def test_voice_mode_latency_under_3s(
        self, topic: str, depth: str, lang: str
    ):
        """VoiceMode should produce first output in < 3 seconds (with mocks)."""
        orch = _build_fast_orchestrator()
        req = DocumentaryRequest(
            user_id="prop-test-user",
            session_id="prop-test-session",
            mode=Mode.VOICE,
            voice_topic=topic,
            depth_dial=depth,
            language=lang,
        )

        start = time.monotonic()
        stream = await orch.process_request(req)
        elapsed = time.monotonic() - start

        assert elapsed < 3.0, f"Latency {elapsed:.2f}s exceeds 3s target"
        assert isinstance(stream, DocumentaryStream)

    @pytest.mark.asyncio
    @given(mode=modes, depth=depth_dials)
    @settings(
        max_examples=100,
        deadline=10_000,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    async def test_process_request_returns_stream(self, mode: Mode, depth: str):
        """process_request always returns a DocumentaryStream, never crashes."""
        orch = _build_fast_orchestrator()
        req = DocumentaryRequest(
            user_id="prop-user",
            session_id="prop-session",
            mode=mode,
            voice_topic="Test topic" if mode != Mode.SIGHT else None,
            camera_frame="dGVzdA==" if mode in (Mode.SIGHT, Mode.LORE) else None,
            depth_dial=depth,
        )

        stream = await orch.process_request(req)
        assert isinstance(stream, DocumentaryStream)
        assert stream.request_id == req.request_id


# ── Property 19: Agent Retry Behavior ────────────────────────────────────────


class TestProperty19AgentRetryBehavior:
    """Property 19: Agent Retry Behavior.

    Feature: lore-multimodal-documentary-app, Property 19: Failed tasks
    are retried up to 3 times with exponential backoff.
    Validates: Requirements 21.5, 30.6.
    """

    @pytest.mark.asyncio
    @given(fail_count=st.integers(min_value=1, max_value=MAX_RETRIES))
    @settings(
        max_examples=100,
        deadline=30_000,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    async def test_retry_succeeds_after_n_failures(self, fail_count: int):
        """Task succeeding on attempt (fail_count+1) should return results."""
        attempts = 0

        async def flaky_task(**kwargs):
            nonlocal attempts
            attempts += 1
            if attempts <= fail_count:
                raise RuntimeError(f"Transient failure #{attempts}")
            return [ContentElement(type=ContentElementType.NARRATION, narration_text="ok")]

        orch = DocumentaryOrchestrator()
        result = await orch._retry_task("flaky", flaky_task)

        if fail_count < MAX_RETRIES:
            assert len(result) == 1, f"Expected success after {fail_count} failures"
            assert attempts == fail_count + 1
        else:
            # fail_count == MAX_RETRIES: all attempts fail, last one succeeds
            # but since fail_count == MAX_RETRIES means it succeeds on attempt MAX_RETRIES+1
            # which doesn't exist, so we need to handle this edge case
            # Actually if fail_count == MAX_RETRIES, attempt MAX_RETRIES fails,
            # then there's no more retry, so result is empty
            # Wait, let me re-check: the task succeeds when attempts > fail_count
            # If fail_count == MAX_RETRIES == 3, attempts 1,2,3 all fail
            # But we only retry MAX_RETRIES times (3 total attempts), so it never succeeds
            assert result == []

    @pytest.mark.asyncio
    @given(n=st.integers(min_value=1, max_value=20))
    @settings(
        max_examples=100,
        deadline=30_000,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    async def test_retry_never_exceeds_max_retries(self, n: int):
        """No matter how many failures, retries are capped at MAX_RETRIES."""
        attempts = 0

        async def always_fails(**kwargs):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("fail")

        orch = DocumentaryOrchestrator()
        result = await orch._retry_task("capped", always_fails)

        assert result == []
        assert attempts == MAX_RETRIES
        assert len(orch.failures) == MAX_RETRIES

    @pytest.mark.asyncio
    @given(task_name=st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L",))))
    @settings(
        max_examples=100,
        deadline=10_000,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    async def test_failures_recorded_with_correct_task_name(self, task_name: str):
        """Each failure records the correct task name."""
        async def always_fails(**kwargs):
            raise ValueError("test error")

        orch = DocumentaryOrchestrator()
        await orch._retry_task(task_name, always_fails)

        assert len(orch.failures) == MAX_RETRIES
        for f in orch.failures:
            assert f.task_name == task_name
