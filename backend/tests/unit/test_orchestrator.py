"""Unit tests for the Orchestrator service.

Tests cover:
  - Request routing to the correct workflow
  - SightMode workflow (location → parallel generation)
  - VoiceMode workflow (topic → parallel generation)
  - LoreMode workflow (fusion → parallel generation)
  - Branch documentary workflow (depth capping)
  - Alternate history workflow
  - Retry logic with exponential backoff
  - Graceful degradation when services fail
  - Mode determination from inputs
  - Stream assembly integration
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.orchestrator.models import (
    ContentElement,
    ContentElementType,
    DocumentaryRequest,
    DocumentaryStream,
    Mode,
    TaskFailure,
)
from backend.services.orchestrator.orchestrator import (
    DocumentaryOrchestrator,
    MAX_BRANCH_DEPTH,
    MAX_RETRIES,
)
from backend.services.orchestrator.stream_assembler import StreamAssembler


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_request(**overrides: Any) -> DocumentaryRequest:
    """Build a DocumentaryRequest with sensible defaults."""
    defaults = {
        "user_id": "test-user-1",
        "session_id": "test-session-1",
        "mode": Mode.SIGHT,
        "camera_frame": "base64_jpeg_data",
        "gps_location": {"latitude": 41.8902, "longitude": 12.4922},
        "depth_dial": "explorer",
        "language": "en",
    }
    defaults.update(overrides)
    return DocumentaryRequest(**defaults)


class FakeNarrationScript:
    """Mimics NarrationScript for mocking."""

    def __init__(self, segments: list[dict] | None = None):
        from backend.services.narration_engine.models import (
            EmotionalTone,
            NarrationSegment,
        )

        if segments is None:
            segments = [
                {"text": "Welcome to the Colosseum.", "duration": 5.0},
                {"text": "Built in 70-80 AD under Emperor Vespasian.", "duration": 8.0},
            ]
        self.segments = [
            NarrationSegment(
                text=s["text"],
                duration=s["duration"],
                tone=EmotionalTone.NEUTRAL,
            )
            for s in segments
        ]


class FakeIllustrationResult:
    """Mimics IllustrationResult."""

    def __init__(self, error: str | None = None):
        self.error = error
        self.illustration = MagicMock()
        self.illustration.image_data = b"fake_png_bytes"
        self.illustration.url = "https://storage.example.com/img.png"
        self.illustration.caption = "Colosseum view"
        self.illustration.concept_description = "wide angle colosseum"
        self.illustration.style = MagicMock()
        self.illustration.style.value = "illustrated"


class FakeVerificationResult:
    """Mimics VerificationResult."""

    def __init__(self):
        self.claim = MagicMock()
        self.claim.text = "The Colosseum was built in 70-80 AD"
        self.verified = True
        self.confidence = 0.92
        self.sources = [
            MagicMock(url="https://en.wikipedia.org/wiki/Colosseum", title="Colosseum - Wikipedia", authority=MagicMock(value="media")),
        ]


class FakeSightModeResponse:
    """Mimics SightModeResponse for documentary trigger."""

    def __init__(self, event_name: str = "documentary_trigger"):
        from backend.services.sight_mode.models import SightModeEvent

        self.event = SightModeEvent(event_name)
        self.payload = {
            "place_name": "Colosseum",
            "place_description": "Ancient Roman amphitheatre",
            "place_types": ["tourist_attraction", "point_of_interest"],
            "visual_description": "Large ancient stone structure",
            "latitude": 41.8902,
            "longitude": 12.4922,
            "place_id": "ChIJrRMgU7ZhLxMRxAOFkC7I8Sg",
            "confidence": 0.95,
        }
        self.timestamp = time.time()


def _build_orchestrator(
    narration: Any = None,
    illustrator: Any = None,
    grounder: Any = None,
    sight_mode: Any = None,
    session_memory: Any = None,
    on_stream_element: Any = None,
) -> DocumentaryOrchestrator:
    """Build an orchestrator with mock services."""
    return DocumentaryOrchestrator(
        narration_engine=narration,
        nano_illustrator=illustrator,
        search_grounder=grounder,
        sight_mode_handler=sight_mode,
        session_memory=session_memory,
        on_stream_element=on_stream_element,
    )


# ── Mode determination tests ─────────────────────────────────────────────────


class TestModeDetermination:
    def test_camera_only_returns_sight(self):
        orch = _build_orchestrator()
        req = _make_request(camera_frame="data", voice_topic=None, voice_audio=None)
        assert orch.determine_mode(req) == Mode.SIGHT

    def test_voice_only_returns_voice(self):
        orch = _build_orchestrator()
        req = _make_request(camera_frame=None, voice_topic="Roman history")
        assert orch.determine_mode(req) == Mode.VOICE

    def test_camera_and_voice_returns_lore(self):
        orch = _build_orchestrator()
        req = _make_request(camera_frame="data", voice_topic="Roman history")
        assert orch.determine_mode(req) == Mode.LORE

    def test_no_inputs_returns_explicit_mode(self):
        orch = _build_orchestrator()
        req = _make_request(camera_frame=None, voice_topic=None, voice_audio=None, mode=Mode.VOICE)
        assert orch.determine_mode(req) == Mode.VOICE

    def test_voice_audio_without_topic_returns_voice(self):
        orch = _build_orchestrator()
        req = _make_request(camera_frame=None, voice_topic=None, voice_audio="audio_data")
        assert orch.determine_mode(req) == Mode.VOICE


# ── Mode transition tests ────────────────────────────────────────────────────


class TestModeTransition:
    def test_all_transitions_valid(self):
        orch = _build_orchestrator()
        for from_mode in Mode:
            for to_mode in Mode:
                assert orch.validate_mode_transition(from_mode, to_mode) is True


# ── SightMode workflow tests ─────────────────────────────────────────────────


class TestSightModeWorkflow:
    @pytest.mark.asyncio
    async def test_sight_mode_full_pipeline(self):
        """Happy path: camera → location → narration + illustration + facts."""
        sight_mock = AsyncMock()
        sight_mock.process_frame = AsyncMock(return_value=FakeSightModeResponse())

        narration_mock = AsyncMock()
        narration_mock.generate_script = AsyncMock(return_value=FakeNarrationScript())

        illustrator_mock = AsyncMock()
        illustrator_mock.generate_batch = AsyncMock(
            return_value=[FakeIllustrationResult(), FakeIllustrationResult()]
        )

        grounder_mock = AsyncMock()
        grounder_mock.verify_batch = AsyncMock(
            return_value=[FakeVerificationResult()]
        )

        orch = _build_orchestrator(
            narration=narration_mock,
            illustrator=illustrator_mock,
            grounder=grounder_mock,
            sight_mode=sight_mock,
        )

        req = _make_request(mode=Mode.SIGHT)
        stream = await orch.sight_mode_workflow(req)

        assert isinstance(stream, DocumentaryStream)
        assert stream.mode == Mode.SIGHT
        assert len(stream.elements) > 0
        assert stream.error is None

        # Verify narration elements present
        narr_elems = [e for e in stream.elements if e.type == ContentElementType.NARRATION]
        assert len(narr_elems) == 2

        # Verify illustration elements present
        ill_elems = [e for e in stream.elements if e.type == ContentElementType.ILLUSTRATION]
        assert len(ill_elems) == 2

        # Verify fact elements present
        fact_elems = [e for e in stream.elements if e.type == ContentElementType.FACT]
        assert len(fact_elems) >= 1

    @pytest.mark.asyncio
    async def test_sight_mode_no_camera_frame(self):
        """SightMode with no camera frame should return empty stream."""
        orch = _build_orchestrator(sight_mode=AsyncMock())
        req = _make_request(mode=Mode.SIGHT, camera_frame=None)
        stream = await orch.sight_mode_workflow(req)
        assert stream.error is not None
        assert len(stream.elements) == 0

    @pytest.mark.asyncio
    async def test_sight_mode_recognition_failure(self):
        """If location recognition fails, return error stream."""
        sight_mock = AsyncMock()
        sight_mock.process_frame = AsyncMock(side_effect=Exception("API error"))

        orch = _build_orchestrator(sight_mode=sight_mock)
        req = _make_request(mode=Mode.SIGHT)
        stream = await orch.sight_mode_workflow(req)
        assert stream.error is not None

    @pytest.mark.asyncio
    async def test_sight_mode_non_trigger_event(self):
        """If SightMode returns non-trigger event, return empty stream."""
        sight_mock = AsyncMock()
        resp = FakeSightModeResponse("frame_buffered")
        sight_mock.process_frame = AsyncMock(return_value=resp)

        orch = _build_orchestrator(sight_mode=sight_mock)
        req = _make_request(mode=Mode.SIGHT)
        stream = await orch.sight_mode_workflow(req)
        assert stream.error is not None


# ── VoiceMode workflow tests ─────────────────────────────────────────────────


class TestVoiceModeWorkflow:
    @pytest.mark.asyncio
    async def test_voice_mode_full_pipeline(self):
        """Happy path: voice topic → narration + illustration + facts."""
        narration_mock = AsyncMock()
        narration_mock.generate_script = AsyncMock(return_value=FakeNarrationScript())

        illustrator_mock = AsyncMock()
        illustrator_mock.generate_batch = AsyncMock(
            return_value=[FakeIllustrationResult()]
        )

        grounder_mock = AsyncMock()
        grounder_mock.verify_batch = AsyncMock(
            return_value=[FakeVerificationResult()]
        )

        orch = _build_orchestrator(
            narration=narration_mock,
            illustrator=illustrator_mock,
            grounder=grounder_mock,
        )

        req = _make_request(mode=Mode.VOICE, voice_topic="Roman Empire history", camera_frame=None)
        stream = await orch.voice_mode_workflow(req)

        assert stream.mode == Mode.VOICE
        assert len(stream.elements) > 0
        assert stream.error is None

    @pytest.mark.asyncio
    async def test_voice_mode_no_topic_uses_fallback(self):
        """VoiceMode with no topic should use 'Unknown Topic'."""
        narration_mock = AsyncMock()
        narration_mock.generate_script = AsyncMock(return_value=FakeNarrationScript())

        illustrator_mock = AsyncMock()
        illustrator_mock.generate_batch = AsyncMock(return_value=[])

        grounder_mock = AsyncMock()
        grounder_mock.verify_batch = AsyncMock(return_value=[])

        orch = _build_orchestrator(
            narration=narration_mock,
            illustrator=illustrator_mock,
            grounder=grounder_mock,
        )

        req = _make_request(mode=Mode.VOICE, voice_topic=None, camera_frame=None)
        stream = await orch.voice_mode_workflow(req)
        assert stream.error is None


# ── LoreMode workflow tests ──────────────────────────────────────────────────


class TestLoreModeWorkflow:
    @pytest.mark.asyncio
    async def test_lore_mode_full_pipeline(self):
        """Camera + voice → fused documentary."""
        sight_mock = AsyncMock()
        sight_mock.process_frame = AsyncMock(return_value=FakeSightModeResponse())

        narration_mock = AsyncMock()
        narration_mock.generate_script = AsyncMock(return_value=FakeNarrationScript())

        illustrator_mock = AsyncMock()
        illustrator_mock.generate_batch = AsyncMock(
            return_value=[FakeIllustrationResult()]
        )

        grounder_mock = AsyncMock()
        grounder_mock.verify_batch = AsyncMock(
            return_value=[FakeVerificationResult()]
        )

        orch = _build_orchestrator(
            narration=narration_mock,
            illustrator=illustrator_mock,
            grounder=grounder_mock,
            sight_mode=sight_mock,
        )

        req = _make_request(mode=Mode.LORE, voice_topic="Gladiator fights")
        stream = await orch.lore_mode_workflow(req)

        assert stream.mode == Mode.LORE
        assert len(stream.elements) > 0

    @pytest.mark.asyncio
    async def test_lore_mode_without_camera(self):
        """LoreMode with failed camera still generates from voice."""
        sight_mock = AsyncMock()
        sight_mock.process_frame = AsyncMock(side_effect=Exception("No camera"))

        narration_mock = AsyncMock()
        narration_mock.generate_script = AsyncMock(return_value=FakeNarrationScript())

        illustrator_mock = AsyncMock()
        illustrator_mock.generate_batch = AsyncMock(return_value=[])

        grounder_mock = AsyncMock()
        grounder_mock.verify_batch = AsyncMock(return_value=[])

        orch = _build_orchestrator(
            narration=narration_mock,
            illustrator=illustrator_mock,
            grounder=grounder_mock,
            sight_mode=sight_mock,
        )

        req = _make_request(mode=Mode.LORE, voice_topic="Ancient Rome")
        stream = await orch.lore_mode_workflow(req)
        # Should still have narration elements from voice topic
        assert len(stream.elements) >= 2


# ── Branch documentary tests ─────────────────────────────────────────────────


class TestBranchDocumentaryWorkflow:
    @pytest.mark.asyncio
    async def test_branch_generates_content(self):
        narration_mock = AsyncMock()
        narration_mock.generate_script = AsyncMock(return_value=FakeNarrationScript())

        illustrator_mock = AsyncMock()
        illustrator_mock.generate_batch = AsyncMock(return_value=[])

        grounder_mock = AsyncMock()
        grounder_mock.verify_batch = AsyncMock(return_value=[])

        orch = _build_orchestrator(
            narration=narration_mock,
            illustrator=illustrator_mock,
            grounder=grounder_mock,
        )

        req = _make_request(
            mode=Mode.VOICE,
            branch_topic="Roman concrete technology",
            previous_topics=["Roman Empire"],
            camera_frame=None,
        )
        stream = await orch.branch_documentary_workflow(req)
        assert len(stream.elements) > 0

    @pytest.mark.asyncio
    async def test_branch_depth_exceeded(self):
        """Exceeding MAX_BRANCH_DEPTH returns a transition element."""
        orch = _build_orchestrator()
        req = _make_request(
            mode=Mode.VOICE,
            branch_topic="Deep subtopic",
            previous_topics=["t1", "t2", "t3"],  # depth=3, at limit
            camera_frame=None,
        )
        stream = await orch.branch_documentary_workflow(req)
        assert len(stream.elements) == 1
        assert stream.elements[0].type == ContentElementType.TRANSITION


# ── Alternate history tests ──────────────────────────────────────────────────


class TestAlternateHistoryWorkflow:
    @pytest.mark.asyncio
    async def test_alternate_history_generates_content(self):
        narration_mock = AsyncMock()
        narration_mock.generate_script = AsyncMock(return_value=FakeNarrationScript())

        illustrator_mock = AsyncMock()
        illustrator_mock.generate_batch = AsyncMock(return_value=[])

        grounder_mock = AsyncMock()
        grounder_mock.verify_batch = AsyncMock(return_value=[])

        orch = _build_orchestrator(
            narration=narration_mock,
            illustrator=illustrator_mock,
            grounder=grounder_mock,
        )

        req = _make_request(
            mode=Mode.LORE,
            voice_topic="the Roman Empire never fell",
            camera_frame=None,
        )
        stream = await orch.alternate_history_workflow(req)
        assert stream.mode == Mode.LORE
        assert len(stream.elements) > 0


# ── Retry logic tests ────────────────────────────────────────────────────────


class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self):
        """Task fails once then succeeds."""
        call_count = 0

        async def flaky_task(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Transient failure")
            return [ContentElement(type=ContentElementType.NARRATION, narration_text="ok")]

        orch = _build_orchestrator()
        result = await orch._retry_task("test_task", flaky_task)
        assert len(result) == 1
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted(self):
        """Task fails all MAX_RETRIES times → returns empty list."""
        call_count = 0

        async def always_fails(**kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("Permanent failure")

        orch = _build_orchestrator()
        result = await orch._retry_task("test_task", always_fails)
        assert result == []
        assert call_count == MAX_RETRIES
        assert len(orch.failures) == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_retry_records_failures(self):
        """Each failed attempt is recorded in the failures list."""
        async def always_fails(**kwargs):
            raise ValueError("bad input")

        orch = _build_orchestrator()
        await orch._retry_task("my_task", always_fails)
        assert len(orch.failures) == MAX_RETRIES
        for i, f in enumerate(orch.failures):
            assert f.task_name == "my_task"
            assert f.attempt == i + 1


# ── Graceful degradation tests ───────────────────────────────────────────────


class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_narration_failure_still_returns_illustrations(self):
        """If narration fails, illustrations and facts still appear."""
        narration_mock = AsyncMock()
        narration_mock.generate_script = AsyncMock(side_effect=RuntimeError("NarrationEngine down"))

        illustrator_mock = AsyncMock()
        illustrator_mock.generate_batch = AsyncMock(
            return_value=[FakeIllustrationResult()]
        )

        grounder_mock = AsyncMock()
        grounder_mock.verify_batch = AsyncMock(
            return_value=[FakeVerificationResult()]
        )

        orch = _build_orchestrator(
            narration=narration_mock,
            illustrator=illustrator_mock,
            grounder=grounder_mock,
        )

        req = _make_request(mode=Mode.VOICE, voice_topic="Test", camera_frame=None)
        stream = await orch.voice_mode_workflow(req)

        # Narration failed, but illustrations and facts present
        ill = [e for e in stream.elements if e.type == ContentElementType.ILLUSTRATION]
        facts = [e for e in stream.elements if e.type == ContentElementType.FACT]
        assert len(ill) >= 1
        assert len(facts) >= 1

    @pytest.mark.asyncio
    async def test_all_services_fail_returns_empty_stream(self):
        """If all services fail, return a stream with no elements (not crash)."""
        narration_mock = AsyncMock()
        narration_mock.generate_script = AsyncMock(side_effect=RuntimeError("fail"))

        illustrator_mock = AsyncMock()
        illustrator_mock.generate_batch = AsyncMock(side_effect=RuntimeError("fail"))

        grounder_mock = AsyncMock()
        grounder_mock.verify_batch = AsyncMock(side_effect=RuntimeError("fail"))

        orch = _build_orchestrator(
            narration=narration_mock,
            illustrator=illustrator_mock,
            grounder=grounder_mock,
        )

        req = _make_request(mode=Mode.VOICE, voice_topic="Test", camera_frame=None)
        stream = await orch.voice_mode_workflow(req)
        assert isinstance(stream, DocumentaryStream)
        assert len(stream.elements) == 0

    @pytest.mark.asyncio
    async def test_no_services_configured(self):
        """Orchestrator with no services returns empty stream."""
        orch = _build_orchestrator()
        req = _make_request(mode=Mode.VOICE, voice_topic="Test", camera_frame=None)
        stream = await orch.voice_mode_workflow(req)
        assert len(stream.elements) == 0


# ── Process request routing tests ────────────────────────────────────────────


class TestProcessRequest:
    @pytest.mark.asyncio
    async def test_routes_sight_mode(self):
        sight_mock = AsyncMock()
        sight_mock.process_frame = AsyncMock(return_value=FakeSightModeResponse())

        narration_mock = AsyncMock()
        narration_mock.generate_script = AsyncMock(return_value=FakeNarrationScript())

        illustrator_mock = AsyncMock()
        illustrator_mock.generate_batch = AsyncMock(return_value=[])

        grounder_mock = AsyncMock()
        grounder_mock.verify_batch = AsyncMock(return_value=[])

        orch = _build_orchestrator(
            narration=narration_mock,
            illustrator=illustrator_mock,
            grounder=grounder_mock,
            sight_mode=sight_mock,
        )

        req = _make_request(mode=Mode.SIGHT)
        stream = await orch.process_request(req)
        assert stream.mode == Mode.SIGHT

    @pytest.mark.asyncio
    async def test_routes_voice_mode(self):
        narration_mock = AsyncMock()
        narration_mock.generate_script = AsyncMock(return_value=FakeNarrationScript())

        illustrator_mock = AsyncMock()
        illustrator_mock.generate_batch = AsyncMock(return_value=[])

        grounder_mock = AsyncMock()
        grounder_mock.verify_batch = AsyncMock(return_value=[])

        orch = _build_orchestrator(
            narration=narration_mock,
            illustrator=illustrator_mock,
            grounder=grounder_mock,
        )

        req = _make_request(mode=Mode.VOICE, voice_topic="Test", camera_frame=None)
        stream = await orch.process_request(req)
        assert stream.mode == Mode.VOICE

    @pytest.mark.asyncio
    async def test_routes_branch_documentary(self):
        narration_mock = AsyncMock()
        narration_mock.generate_script = AsyncMock(return_value=FakeNarrationScript())

        illustrator_mock = AsyncMock()
        illustrator_mock.generate_batch = AsyncMock(return_value=[])

        grounder_mock = AsyncMock()
        grounder_mock.verify_batch = AsyncMock(return_value=[])

        orch = _build_orchestrator(
            narration=narration_mock,
            illustrator=illustrator_mock,
            grounder=grounder_mock,
        )

        req = _make_request(
            mode=Mode.VOICE,
            branch_topic="Sub-topic",
            camera_frame=None,
        )
        stream = await orch.process_request(req)
        assert len(stream.elements) > 0

    @pytest.mark.asyncio
    async def test_process_request_catches_unhandled_errors(self):
        """An exception in workflow returns error stream, not crash."""
        orch = _build_orchestrator()
        req = _make_request(mode=Mode.SIGHT, camera_frame=None)
        stream = await orch.process_request(req)
        # No crash; error is recorded
        assert stream.error is not None or len(stream.elements) == 0


# ── Stream assembler tests ───────────────────────────────────────────────────


class TestStreamAssembler:
    def test_assemble_empty(self):
        asm = StreamAssembler()
        stream = asm.assemble(
            request_id="r1", session_id="s1", mode=Mode.SIGHT
        )
        assert len(stream.elements) == 0

    def test_assemble_narration_only(self):
        asm = StreamAssembler()
        elems = [
            asm.create_narration_element("Segment 1", audio_duration=5.0),
            asm.create_narration_element("Segment 2", audio_duration=8.0),
        ]
        stream = asm.assemble(
            request_id="r1", session_id="s1", mode=Mode.SIGHT,
            narration_elements=elems,
        )
        assert len(stream.elements) == 2
        assert all(e.type == ContentElementType.NARRATION for e in stream.elements)

    def test_assemble_interleaved(self):
        asm = StreamAssembler()
        narr = [
            asm.create_narration_element("N1"),
            asm.create_narration_element("N2"),
        ]
        ills = [
            asm.create_illustration_element(caption="I1"),
        ]
        facts = [
            asm.create_fact_element("F1", verified=True),
        ]
        stream = asm.assemble(
            request_id="r1", session_id="s1", mode=Mode.VOICE,
            narration_elements=narr,
            illustration_elements=ills,
            fact_elements=facts,
        )
        # N1, I1, N2, F1  (illustration after 1st narration, fact after 2nd)
        assert len(stream.elements) == 4
        assert stream.elements[0].type == ContentElementType.NARRATION
        assert stream.elements[1].type == ContentElementType.ILLUSTRATION
        assert stream.elements[2].type == ContentElementType.NARRATION
        assert stream.elements[3].type == ContentElementType.FACT

    def test_sequence_ids_assigned(self):
        asm = StreamAssembler()
        elems = [
            asm.create_narration_element("N1"),
            asm.create_illustration_element(caption="I1"),
        ]
        stream = asm.assemble(
            request_id="r1", session_id="s1", mode=Mode.SIGHT,
            narration_elements=[elems[0]],
            illustration_elements=[elems[1]],
        )
        for i, elem in enumerate(stream.elements):
            assert elem.sequence_id == i

    def test_create_transition_element(self):
        asm = StreamAssembler()
        elem = asm.create_transition_element("Transitioning to next topic")
        assert elem.type == ContentElementType.TRANSITION
        assert elem.transition_text == "Transitioning to next topic"


# ── On-stream-element callback tests ─────────────────────────────────────────


class TestStreamCallback:
    @pytest.mark.asyncio
    async def test_callback_invoked_for_each_element(self):
        pushed = []

        async def on_element(session_id, element):
            pushed.append((session_id, element))

        narration_mock = AsyncMock()
        narration_mock.generate_script = AsyncMock(return_value=FakeNarrationScript())

        illustrator_mock = AsyncMock()
        illustrator_mock.generate_batch = AsyncMock(return_value=[])

        grounder_mock = AsyncMock()
        grounder_mock.verify_batch = AsyncMock(return_value=[])

        orch = _build_orchestrator(
            narration=narration_mock,
            illustrator=illustrator_mock,
            grounder=grounder_mock,
            on_stream_element=on_element,
        )

        req = _make_request(mode=Mode.VOICE, voice_topic="Test", camera_frame=None)
        await orch.voice_mode_workflow(req)

        # 2 narration segments pushed
        assert len(pushed) == 2
        for sid, elem in pushed:
            assert sid == "test-session-1"

    @pytest.mark.asyncio
    async def test_callback_failure_does_not_crash(self):
        """A failing callback should not crash the workflow."""
        async def bad_callback(session_id, element):
            raise RuntimeError("callback error")

        narration_mock = AsyncMock()
        narration_mock.generate_script = AsyncMock(return_value=FakeNarrationScript())

        illustrator_mock = AsyncMock()
        illustrator_mock.generate_batch = AsyncMock(return_value=[])

        grounder_mock = AsyncMock()
        grounder_mock.verify_batch = AsyncMock(return_value=[])

        orch = _build_orchestrator(
            narration=narration_mock,
            illustrator=illustrator_mock,
            grounder=grounder_mock,
            on_stream_element=bad_callback,
        )

        req = _make_request(mode=Mode.VOICE, voice_topic="Test", camera_frame=None)
        stream = await orch.voice_mode_workflow(req)
        # Should not crash
        assert isinstance(stream, DocumentaryStream)


# ── Helper method tests ──────────────────────────────────────────────────────


class TestHelperMethods:
    def test_fuse_topic_both(self):
        orch = _build_orchestrator()
        result = orch._fuse_topic("Gladiators", "Colosseum", "stone structure")
        assert "Gladiators" in result
        assert "Colosseum" in result

    def test_fuse_topic_voice_only(self):
        orch = _build_orchestrator()
        result = orch._fuse_topic("Roman Empire", "", "")
        assert result == "Roman Empire"

    def test_fuse_topic_place_only(self):
        orch = _build_orchestrator()
        result = orch._fuse_topic("", "Colosseum", "")
        assert "Colosseum" in result

    def test_fuse_topic_none(self):
        orch = _build_orchestrator()
        result = orch._fuse_topic("", "", "")
        assert result == "Unknown topic"

    def test_build_illustration_prompts(self):
        orch = _build_orchestrator()
        prompts = orch._build_illustration_prompts("Roman Forum", "Roman Forum")
        assert len(prompts) == 2
        assert "Roman Forum" in prompts[0]

    def test_extract_claims(self):
        orch = _build_orchestrator()
        claims = orch._extract_claims("Ancient history", "Colosseum")
        assert len(claims) == 2
        assert any("Colosseum" in c for c in claims)

    def test_extract_claims_empty(self):
        orch = _build_orchestrator()
        claims = orch._extract_claims("", "")
        assert claims == []

    def test_empty_stream(self):
        orch = _build_orchestrator()
        req = _make_request()
        stream = orch._empty_stream(req, error="test error")
        assert stream.error == "test error"
        assert len(stream.elements) == 0
