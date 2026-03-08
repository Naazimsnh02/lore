"""Unit tests for the enhanced VoiceMode workflow in the Orchestrator (Task 17).

Tests cover:
  - Voice audio transcription integration via VoiceModeHandler
  - Intent-based routing (new_topic, follow_up, branch, question, command)
  - Graceful degradation when VoiceModeHandler is unavailable
  - Graceful degradation when ConversationManager is unavailable
  - Integration with ConversationManager for context tracking
  - Follow-up topic enrichment with conversation context
  - Command handling (stop, pause, resume, mode switch, etc.)
  - Branch documentary delegation
  - Backward compatibility (no voice_mode_handler / conversation_manager)
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.orchestrator.models import (
    ContentElement,
    ContentElementType,
    DocumentaryRequest,
    DocumentaryStream,
    Mode,
)
from backend.services.orchestrator.orchestrator import DocumentaryOrchestrator
from backend.services.voice_mode.models import (
    ConversationIntent,
    IntentClassification,
    NoiseLevel,
    TranscriptionResult,
    VoiceModeContext,
    VoiceModeEvent,
    VoiceModeResponse,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_request(**overrides: Any) -> DocumentaryRequest:
    """Build a DocumentaryRequest with sensible VoiceMode defaults."""
    defaults = {
        "user_id": "test-user-1",
        "session_id": "test-session-1",
        "mode": Mode.VOICE,
        "voice_topic": "Ancient Rome",
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
                {"text": "Ancient Rome was a civilization.", "duration": 5.0},
                {"text": "It shaped Western history.", "duration": 4.0},
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
        self.illustration.caption = "Ancient Rome"
        self.illustration.concept_description = "wide angle rome"
        self.illustration.style = MagicMock()
        self.illustration.style.value = "illustrated"


class FakeVerificationResult:
    """Mimics VerificationResult."""

    def __init__(self):
        self.claim = MagicMock()
        self.claim.text = "Rome was founded in 753 BC"
        self.verified = True
        self.confidence = 0.90
        self.sources = [
            MagicMock(
                url="https://en.wikipedia.org/wiki/Rome",
                title="Rome - Wikipedia",
                authority=MagicMock(value="media"),
            ),
        ]


def _mock_narration() -> AsyncMock:
    mock = AsyncMock()
    mock.generate_script = AsyncMock(return_value=FakeNarrationScript())
    return mock


def _mock_illustrator() -> AsyncMock:
    mock = AsyncMock()
    mock.generate_batch = AsyncMock(
        return_value=[FakeIllustrationResult()]
    )
    return mock


def _mock_grounder() -> AsyncMock:
    mock = AsyncMock()
    mock.verify_batch = AsyncMock(return_value=[FakeVerificationResult()])
    return mock


def _make_voice_response(
    event: VoiceModeEvent = VoiceModeEvent.TOPIC_DETECTED,
    topic: str = "Ancient Rome",
    language: str = "en",
    payload: dict | None = None,
) -> VoiceModeResponse:
    """Build a VoiceModeResponse with sensible defaults."""
    return VoiceModeResponse(
        event=event,
        topic=topic if event == VoiceModeEvent.TOPIC_DETECTED else None,
        detected_language=language if event == VoiceModeEvent.TOPIC_DETECTED else None,
        transcription=TranscriptionResult(text=topic, language=language, confidence=0.85)
        if event == VoiceModeEvent.TOPIC_DETECTED
        else None,
        noise_level=NoiseLevel.LOW,
        noise_cancelled=False,
        payload=payload or {},
        timestamp=time.time(),
    )


def _make_intent(
    intent: ConversationIntent = ConversationIntent.NEW_TOPIC,
    confidence: float = 0.85,
    extracted_topic: str = "Ancient Rome",
    branch_topic: str | None = None,
) -> IntentClassification:
    return IntentClassification(
        intent=intent,
        confidence=confidence,
        extracted_topic=extracted_topic,
        branch_topic=branch_topic,
        reasoning="Test classification",
    )


def _build_orchestrator(
    narration: Any = None,
    illustrator: Any = None,
    grounder: Any = None,
    voice_mode: Any = None,
    conversation_manager: Any = None,
    **kwargs: Any,
) -> DocumentaryOrchestrator:
    """Build an orchestrator with optional mock services."""
    return DocumentaryOrchestrator(
        narration_engine=narration,
        nano_illustrator=illustrator,
        search_grounder=grounder,
        voice_mode_handler=voice_mode,
        conversation_manager=conversation_manager,
        **kwargs,
    )


# ── Backward compatibility (no voice handler / conversation manager) ─────────


class TestVoiceModeBackwardCompatibility:
    """voice_mode_workflow should work exactly as before when no voice_mode_handler
    or conversation_manager is configured."""

    @pytest.mark.asyncio
    async def test_simple_topic_generation_without_handlers(self):
        """Without VoiceModeHandler, the workflow uses voice_topic directly."""
        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
        )
        req = _make_request(voice_topic="Ancient Rome")
        stream = await orch.voice_mode_workflow(req)

        assert isinstance(stream, DocumentaryStream)
        assert stream.mode == Mode.VOICE
        assert stream.error is None
        assert len(stream.elements) > 0

    @pytest.mark.asyncio
    async def test_unknown_topic_fallback(self):
        """With no voice_topic and no voice_audio, topic defaults to 'Unknown Topic'."""
        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
        )
        req = _make_request(voice_topic=None, voice_audio=None)
        stream = await orch.voice_mode_workflow(req)

        assert isinstance(stream, DocumentaryStream)
        assert stream.mode == Mode.VOICE

    @pytest.mark.asyncio
    async def test_voice_audio_ignored_without_handler(self):
        """voice_audio is ignored when voice_mode_handler is not available."""
        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
        )
        req = _make_request(voice_topic="Fallback Topic", voice_audio="base64audio")
        stream = await orch.voice_mode_workflow(req)

        assert isinstance(stream, DocumentaryStream)
        assert stream.mode == Mode.VOICE
        # Should succeed using the voice_topic
        assert len(stream.elements) > 0


# ── Voice audio transcription integration ────────────────────────────────────


class TestVoiceAudioTranscription:
    """Tests for Step 1: transcription via VoiceModeHandler."""

    @pytest.mark.asyncio
    async def test_transcribes_audio_and_uses_topic(self):
        """When voice_audio is provided, VoiceModeHandler transcribes it."""
        voice_mock = AsyncMock()
        voice_mock.process_voice_input = AsyncMock(
            return_value=_make_voice_response(
                event=VoiceModeEvent.TOPIC_DETECTED,
                topic="The Colosseum",
                language="en",
            )
        )

        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
            voice_mode=voice_mock,
        )
        req = _make_request(voice_topic=None, voice_audio="base64_pcm_audio")
        stream = await orch.voice_mode_workflow(req)

        voice_mock.process_voice_input.assert_awaited_once()
        call_kwargs = voice_mock.process_voice_input.call_args
        assert call_kwargs.kwargs["audio_base64"] == "base64_pcm_audio"
        assert stream.mode == Mode.VOICE
        assert stream.error is None

    @pytest.mark.asyncio
    async def test_silence_detected_uses_fallback_topic(self):
        """When VoiceModeHandler detects silence, fallback to voice_topic."""
        voice_mock = AsyncMock()
        voice_mock.process_voice_input = AsyncMock(
            return_value=_make_voice_response(event=VoiceModeEvent.SILENCE_DETECTED)
        )

        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
            voice_mode=voice_mock,
        )
        req = _make_request(voice_topic="Fallback Topic", voice_audio="base64_silence")
        stream = await orch.voice_mode_workflow(req)

        assert stream.mode == Mode.VOICE
        assert stream.error is None
        # Should still generate content using the fallback topic
        assert len(stream.elements) > 0

    @pytest.mark.asyncio
    async def test_silence_detected_no_fallback_uses_unknown(self):
        """When VoiceModeHandler detects silence and no voice_topic, use 'Unknown Topic'."""
        voice_mock = AsyncMock()
        voice_mock.process_voice_input = AsyncMock(
            return_value=_make_voice_response(event=VoiceModeEvent.SILENCE_DETECTED)
        )

        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
            voice_mode=voice_mock,
        )
        req = _make_request(voice_topic=None, voice_audio="base64_silence")
        stream = await orch.voice_mode_workflow(req)

        assert stream.mode == Mode.VOICE

    @pytest.mark.asyncio
    async def test_voice_error_falls_back_to_topic(self):
        """When VoiceModeHandler returns an error, fall back to voice_topic."""
        voice_mock = AsyncMock()
        voice_mock.process_voice_input = AsyncMock(
            return_value=_make_voice_response(
                event=VoiceModeEvent.ERROR,
                payload={"error": "invalid_base64"},
            )
        )

        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
            voice_mode=voice_mock,
        )
        req = _make_request(voice_topic="Fallback Topic", voice_audio="bad_audio")
        stream = await orch.voice_mode_workflow(req)

        assert stream.mode == Mode.VOICE
        assert len(stream.elements) > 0

    @pytest.mark.asyncio
    async def test_voice_handler_exception_graceful_degradation(self):
        """When VoiceModeHandler raises an exception, fall back gracefully."""
        voice_mock = AsyncMock()
        voice_mock.process_voice_input = AsyncMock(side_effect=RuntimeError("boom"))

        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
            voice_mode=voice_mock,
        )
        req = _make_request(voice_topic="Fallback Topic", voice_audio="audio_data")
        stream = await orch.voice_mode_workflow(req)

        # Should still produce a stream using the fallback topic
        assert stream.mode == Mode.VOICE
        assert len(stream.elements) > 0

    @pytest.mark.asyncio
    async def test_detected_language_used_for_generation(self):
        """When VoiceModeHandler detects a language, it is used in generation."""
        voice_mock = AsyncMock()
        voice_mock.process_voice_input = AsyncMock(
            return_value=_make_voice_response(
                event=VoiceModeEvent.TOPIC_DETECTED,
                topic="La Tour Eiffel",
                language="fr",
            )
        )

        narration = _mock_narration()
        orch = _build_orchestrator(
            narration=narration,
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
            voice_mode=voice_mock,
        )
        req = _make_request(voice_topic=None, voice_audio="french_audio", language="en")
        stream = await orch.voice_mode_workflow(req)

        assert stream.mode == Mode.VOICE
        # Narration should have been called (language is passed via _parallel_generate)
        narration.generate_script.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_input_buffered_keeps_existing_topic(self):
        """When VoiceModeHandler returns INPUT_BUFFERED, the existing topic is kept."""
        voice_mock = AsyncMock()
        voice_mock.process_voice_input = AsyncMock(
            return_value=_make_voice_response(event=VoiceModeEvent.INPUT_BUFFERED)
        )

        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
            voice_mode=voice_mock,
        )
        req = _make_request(voice_topic="Keep This Topic", voice_audio="short_audio")
        stream = await orch.voice_mode_workflow(req)

        assert stream.mode == Mode.VOICE
        assert len(stream.elements) > 0

    @pytest.mark.asyncio
    async def test_voice_topic_preferred_when_no_audio(self):
        """When no voice_audio, VoiceModeHandler is not called even if available."""
        voice_mock = AsyncMock()
        voice_mock.process_voice_input = AsyncMock()

        orch = _build_orchestrator(
            narration=_mock_narration(),
            voice_mode=voice_mock,
        )
        req = _make_request(voice_topic="Direct Topic", voice_audio=None)
        stream = await orch.voice_mode_workflow(req)

        voice_mock.process_voice_input.assert_not_awaited()
        assert stream.mode == Mode.VOICE


# ── Intent-based routing ─────────────────────────────────────────────────────


class TestIntentRouting:
    """Tests for Step 2-3: ConversationManager intent classification and routing."""

    @pytest.mark.asyncio
    async def test_new_topic_generates_content(self):
        """NEW_TOPIC intent triggers parallel content generation."""
        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(intent=ConversationIntent.NEW_TOPIC)
        )
        cm_mock.add_assistant_turn = MagicMock()
        cm_mock.get_context_summary = MagicMock(return_value="")
        cm_mock.get_current_topic = MagicMock(return_value=None)

        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
            conversation_manager=cm_mock,
        )
        req = _make_request(voice_topic="Ancient Rome")
        stream = await orch.voice_mode_workflow(req)

        cm_mock.handle_input.assert_awaited_once()
        assert stream.mode == Mode.VOICE
        assert len(stream.elements) > 0
        # Assistant turn should be recorded
        cm_mock.add_assistant_turn.assert_called_once()

    @pytest.mark.asyncio
    async def test_question_generates_content(self):
        """QUESTION intent triggers parallel content generation."""
        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(intent=ConversationIntent.QUESTION)
        )
        cm_mock.add_assistant_turn = MagicMock()
        cm_mock.get_context_summary = MagicMock(return_value="")
        cm_mock.get_current_topic = MagicMock(return_value=None)

        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
            conversation_manager=cm_mock,
        )
        req = _make_request(voice_topic="What is the Colosseum?")
        stream = await orch.voice_mode_workflow(req)

        assert stream.mode == Mode.VOICE
        assert len(stream.elements) > 0

    @pytest.mark.asyncio
    async def test_follow_up_enriches_topic_with_context(self):
        """FOLLOW_UP intent enriches the topic with current conversation context."""
        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(
                intent=ConversationIntent.FOLLOW_UP,
                extracted_topic="gladiators",
            )
        )
        cm_mock.add_assistant_turn = MagicMock()
        cm_mock.get_context_summary = MagicMock(
            return_value="User: Tell me about Ancient Rome\nAssistant: Ancient Rome was..."
        )
        cm_mock.get_current_topic = MagicMock(return_value="Ancient Rome")

        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
            conversation_manager=cm_mock,
        )
        req = _make_request(voice_topic="gladiators")
        stream = await orch.voice_mode_workflow(req)

        assert stream.mode == Mode.VOICE
        assert len(stream.elements) > 0
        cm_mock.add_assistant_turn.assert_called_once()

    @pytest.mark.asyncio
    async def test_follow_up_no_enrichment_when_same_topic(self):
        """FOLLOW_UP with same current_topic does not append context suffix."""
        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(
                intent=ConversationIntent.FOLLOW_UP,
                extracted_topic="Ancient Rome",
            )
        )
        cm_mock.add_assistant_turn = MagicMock()
        cm_mock.get_context_summary = MagicMock(return_value="some context")
        cm_mock.get_current_topic = MagicMock(return_value="Ancient Rome")

        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
            conversation_manager=cm_mock,
        )
        req = _make_request(voice_topic="Ancient Rome")
        stream = await orch.voice_mode_workflow(req)

        assert stream.mode == Mode.VOICE
        assert len(stream.elements) > 0

    @pytest.mark.asyncio
    async def test_branch_intent_delegates_to_branch_workflow(self):
        """BRANCH intent delegates to branch_documentary_workflow."""
        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(
                intent=ConversationIntent.BRANCH,
                branch_topic="gladiatorial combat",
                extracted_topic="gladiatorial combat",
            )
        )
        cm_mock.add_assistant_turn = MagicMock()

        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
            conversation_manager=cm_mock,
        )
        req = _make_request(voice_topic="tell me more about gladiatorial combat")
        stream = await orch.voice_mode_workflow(req)

        assert stream.mode == Mode.VOICE
        # Branch workflow should produce elements
        assert isinstance(stream, DocumentaryStream)
        cm_mock.add_assistant_turn.assert_called_once()

    @pytest.mark.asyncio
    async def test_branch_intent_uses_topic_when_no_branch_topic(self):
        """BRANCH intent falls back to main topic when branch_topic is None."""
        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(
                intent=ConversationIntent.BRANCH,
                branch_topic=None,
                extracted_topic="Rome",
            )
        )
        cm_mock.add_assistant_turn = MagicMock()

        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
            conversation_manager=cm_mock,
        )
        req = _make_request(voice_topic="Rome")
        stream = await orch.voice_mode_workflow(req)

        assert isinstance(stream, DocumentaryStream)

    @pytest.mark.asyncio
    async def test_branch_adds_topic_to_previous_topics(self):
        """BRANCH intent adds the current topic to previous_topics for depth tracking."""
        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(
                intent=ConversationIntent.BRANCH,
                branch_topic="aqueducts",
            )
        )
        cm_mock.add_assistant_turn = MagicMock()

        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
            conversation_manager=cm_mock,
        )
        req = _make_request(
            voice_topic="Roman engineering",
            previous_topics=["Ancient Rome"],
        )
        stream = await orch.voice_mode_workflow(req)

        assert isinstance(stream, DocumentaryStream)


# ── Command handling ─────────────────────────────────────────────────────────


class TestCommandHandling:
    """Tests for COMMAND intent routing and _handle_voice_command."""

    @pytest.mark.asyncio
    async def test_stop_command_returns_transition(self):
        """COMMAND intent with 'stop' returns a pause transition."""
        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(intent=ConversationIntent.COMMAND)
        )
        cm_mock.add_assistant_turn = MagicMock()

        orch = _build_orchestrator(conversation_manager=cm_mock)
        req = _make_request(voice_topic="stop")
        stream = await orch.voice_mode_workflow(req)

        assert stream.mode == Mode.VOICE
        assert len(stream.elements) >= 1
        transition_elems = [
            e for e in stream.elements if e.type == ContentElementType.TRANSITION
        ]
        assert len(transition_elems) >= 1
        assert "paused" in transition_elems[0].transition_text.lower()

    @pytest.mark.asyncio
    async def test_pause_command(self):
        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(intent=ConversationIntent.COMMAND)
        )
        cm_mock.add_assistant_turn = MagicMock()

        orch = _build_orchestrator(conversation_manager=cm_mock)
        req = _make_request(voice_topic="pause")
        stream = await orch.voice_mode_workflow(req)

        transition_elems = [
            e for e in stream.elements if e.type == ContentElementType.TRANSITION
        ]
        assert len(transition_elems) >= 1
        assert "paused" in transition_elems[0].transition_text.lower()

    @pytest.mark.asyncio
    async def test_resume_command(self):
        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(intent=ConversationIntent.COMMAND)
        )
        cm_mock.add_assistant_turn = MagicMock()

        orch = _build_orchestrator(conversation_manager=cm_mock)
        req = _make_request(voice_topic="resume")
        stream = await orch.voice_mode_workflow(req)

        transition_elems = [
            e for e in stream.elements if e.type == ContentElementType.TRANSITION
        ]
        assert len(transition_elems) >= 1
        assert "resum" in transition_elems[0].transition_text.lower()

    @pytest.mark.asyncio
    async def test_switch_mode_command(self):
        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(intent=ConversationIntent.COMMAND)
        )
        cm_mock.add_assistant_turn = MagicMock()

        orch = _build_orchestrator(conversation_manager=cm_mock)
        req = _make_request(voice_topic="switch mode to sight")
        stream = await orch.voice_mode_workflow(req)

        transition_elems = [
            e for e in stream.elements if e.type == ContentElementType.TRANSITION
        ]
        assert len(transition_elems) >= 1
        assert "mode" in transition_elems[0].transition_text.lower()

    @pytest.mark.asyncio
    async def test_change_language_command(self):
        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(intent=ConversationIntent.COMMAND)
        )
        cm_mock.add_assistant_turn = MagicMock()

        orch = _build_orchestrator(conversation_manager=cm_mock)
        req = _make_request(voice_topic="change language to French")
        stream = await orch.voice_mode_workflow(req)

        transition_elems = [
            e for e in stream.elements if e.type == ContentElementType.TRANSITION
        ]
        assert len(transition_elems) >= 1
        assert "language" in transition_elems[0].transition_text.lower()

    @pytest.mark.asyncio
    async def test_change_depth_command(self):
        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(intent=ConversationIntent.COMMAND)
        )
        cm_mock.add_assistant_turn = MagicMock()

        orch = _build_orchestrator(conversation_manager=cm_mock)
        req = _make_request(voice_topic="set depth to expert")
        stream = await orch.voice_mode_workflow(req)

        transition_elems = [
            e for e in stream.elements if e.type == ContentElementType.TRANSITION
        ]
        assert len(transition_elems) >= 1
        assert "depth" in transition_elems[0].transition_text.lower()

    @pytest.mark.asyncio
    async def test_go_back_command(self):
        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(intent=ConversationIntent.COMMAND)
        )
        cm_mock.add_assistant_turn = MagicMock()

        orch = _build_orchestrator(conversation_manager=cm_mock)
        req = _make_request(voice_topic="go back")
        stream = await orch.voice_mode_workflow(req)

        transition_elems = [
            e for e in stream.elements if e.type == ContentElementType.TRANSITION
        ]
        assert len(transition_elems) >= 1
        assert "return" in transition_elems[0].transition_text.lower()

    @pytest.mark.asyncio
    async def test_export_command(self):
        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(intent=ConversationIntent.COMMAND)
        )
        cm_mock.add_assistant_turn = MagicMock()

        orch = _build_orchestrator(conversation_manager=cm_mock)
        req = _make_request(voice_topic="export my documentary")
        stream = await orch.voice_mode_workflow(req)

        transition_elems = [
            e for e in stream.elements if e.type == ContentElementType.TRANSITION
        ]
        assert len(transition_elems) >= 1
        assert "export" in transition_elems[0].transition_text.lower()

    @pytest.mark.asyncio
    async def test_unknown_command_acknowledged(self):
        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(intent=ConversationIntent.COMMAND)
        )
        cm_mock.add_assistant_turn = MagicMock()

        orch = _build_orchestrator(conversation_manager=cm_mock)
        req = _make_request(voice_topic="some unknown command")
        stream = await orch.voice_mode_workflow(req)

        transition_elems = [
            e for e in stream.elements if e.type == ContentElementType.TRANSITION
        ]
        assert len(transition_elems) >= 1
        assert "acknowledged" in transition_elems[0].transition_text.lower()

    def test_handle_voice_command_all_types(self):
        """Direct unit test for _handle_voice_command."""
        orch = _build_orchestrator()

        assert "paused" in orch._handle_voice_command("stop").lower()
        assert "paused" in orch._handle_voice_command("pause the documentary").lower()
        assert "resum" in orch._handle_voice_command("resume").lower()
        assert "mode" in orch._handle_voice_command("switch mode").lower()
        assert "mode" in orch._handle_voice_command("change mode").lower()
        assert "language" in orch._handle_voice_command("change language").lower()
        assert "language" in orch._handle_voice_command("switch language").lower()
        assert "depth" in orch._handle_voice_command("change depth").lower()
        assert "depth" in orch._handle_voice_command("set depth").lower()
        assert "return" in orch._handle_voice_command("go back").lower()
        assert "return" in orch._handle_voice_command("exit branch").lower()
        assert "return" in orch._handle_voice_command("close branch").lower()
        assert "return" in orch._handle_voice_command("return to main").lower()
        assert "export" in orch._handle_voice_command("export").lower()
        assert "export" in orch._handle_voice_command("save").lower()
        assert "acknowledged" in orch._handle_voice_command("foo bar").lower()


# ── Graceful degradation ─────────────────────────────────────────────────────


class TestGracefulDegradation:
    """Tests for graceful degradation when services fail."""

    @pytest.mark.asyncio
    async def test_conversation_manager_exception(self):
        """When ConversationManager raises, workflow falls back to generation."""
        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(side_effect=RuntimeError("CM crashed"))
        cm_mock.add_assistant_turn = MagicMock()

        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
            conversation_manager=cm_mock,
        )
        req = _make_request(voice_topic="Ancient Rome")
        stream = await orch.voice_mode_workflow(req)

        # Should still produce content despite CM failure
        assert stream.mode == Mode.VOICE
        assert len(stream.elements) > 0

    @pytest.mark.asyncio
    async def test_record_assistant_turn_exception_swallowed(self):
        """If recording the assistant turn fails, the stream is still returned."""
        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(intent=ConversationIntent.NEW_TOPIC)
        )
        cm_mock.add_assistant_turn = MagicMock(side_effect=RuntimeError("turn recording failed"))
        cm_mock.get_context_summary = MagicMock(return_value="")
        cm_mock.get_current_topic = MagicMock(return_value=None)

        orch = _build_orchestrator(
            narration=_mock_narration(),
            conversation_manager=cm_mock,
        )
        req = _make_request(voice_topic="Rome")
        stream = await orch.voice_mode_workflow(req)

        # Stream should still be returned despite turn recording failure
        assert stream.mode == Mode.VOICE

    @pytest.mark.asyncio
    async def test_all_services_unavailable(self):
        """When no generation services are configured, stream is empty but valid."""
        orch = _build_orchestrator()
        req = _make_request(voice_topic="Rome")
        stream = await orch.voice_mode_workflow(req)

        assert isinstance(stream, DocumentaryStream)
        assert stream.mode == Mode.VOICE
        assert stream.error is None


# ── Full pipeline integration ────────────────────────────────────────────────


class TestFullPipeline:
    """End-to-end tests combining transcription + intent + generation."""

    @pytest.mark.asyncio
    async def test_audio_to_new_topic_full_pipeline(self):
        """Full pipeline: audio → transcribe → new_topic → parallel generate."""
        voice_mock = AsyncMock()
        voice_mock.process_voice_input = AsyncMock(
            return_value=_make_voice_response(
                event=VoiceModeEvent.TOPIC_DETECTED,
                topic="The Great Wall of China",
                language="en",
            )
        )

        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(
                intent=ConversationIntent.NEW_TOPIC,
                extracted_topic="The Great Wall of China",
            )
        )
        cm_mock.add_assistant_turn = MagicMock()
        cm_mock.get_context_summary = MagicMock(return_value="")
        cm_mock.get_current_topic = MagicMock(return_value=None)

        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
            voice_mode=voice_mock,
            conversation_manager=cm_mock,
        )
        req = _make_request(voice_topic=None, voice_audio="base64_audio_data")
        stream = await orch.voice_mode_workflow(req)

        voice_mock.process_voice_input.assert_awaited_once()
        cm_mock.handle_input.assert_awaited_once()
        cm_mock.add_assistant_turn.assert_called_once()
        assert stream.mode == Mode.VOICE
        assert len(stream.elements) > 0

        narr = [e for e in stream.elements if e.type == ContentElementType.NARRATION]
        assert len(narr) >= 1

    @pytest.mark.asyncio
    async def test_audio_to_branch_full_pipeline(self):
        """Full pipeline: audio → transcribe → branch → branch workflow."""
        voice_mock = AsyncMock()
        voice_mock.process_voice_input = AsyncMock(
            return_value=_make_voice_response(
                event=VoiceModeEvent.TOPIC_DETECTED,
                topic="tell me more about gladiators",
            )
        )

        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(
                intent=ConversationIntent.BRANCH,
                branch_topic="gladiators",
            )
        )
        cm_mock.add_assistant_turn = MagicMock()

        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
            voice_mode=voice_mock,
            conversation_manager=cm_mock,
        )
        req = _make_request(voice_topic=None, voice_audio="audio_branch_request")
        stream = await orch.voice_mode_workflow(req)

        assert isinstance(stream, DocumentaryStream)
        cm_mock.add_assistant_turn.assert_called_once()

    @pytest.mark.asyncio
    async def test_audio_to_command_full_pipeline(self):
        """Full pipeline: audio → transcribe → command → transition element."""
        voice_mock = AsyncMock()
        voice_mock.process_voice_input = AsyncMock(
            return_value=_make_voice_response(
                event=VoiceModeEvent.TOPIC_DETECTED,
                topic="stop",
            )
        )

        cm_mock = AsyncMock()
        cm_mock.handle_input = AsyncMock(
            return_value=_make_intent(intent=ConversationIntent.COMMAND)
        )
        cm_mock.add_assistant_turn = MagicMock()

        orch = _build_orchestrator(
            voice_mode=voice_mock,
            conversation_manager=cm_mock,
        )
        req = _make_request(voice_topic=None, voice_audio="audio_stop")
        stream = await orch.voice_mode_workflow(req)

        transition_elems = [
            e for e in stream.elements if e.type == ContentElementType.TRANSITION
        ]
        assert len(transition_elems) >= 1

    @pytest.mark.asyncio
    async def test_process_request_routes_to_voice_workflow(self):
        """process_request with VOICE mode routes to voice_mode_workflow."""
        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
        )
        req = _make_request(mode=Mode.VOICE, voice_topic="Roman aqueducts")
        stream = await orch.process_request(req)

        assert isinstance(stream, DocumentaryStream)
        assert stream.mode == Mode.VOICE
        assert len(stream.elements) > 0

    @pytest.mark.asyncio
    async def test_on_stream_element_callback_fires(self):
        """on_stream_element callback fires for each generated element."""
        callback = AsyncMock()

        orch = _build_orchestrator(
            narration=_mock_narration(),
            illustrator=_mock_illustrator(),
            grounder=_mock_grounder(),
            on_stream_element=callback,
        )
        req = _make_request(voice_topic="Roman Roads")
        stream = await orch.voice_mode_workflow(req)

        assert callback.await_count > 0


# ── Record assistant turn ────────────────────────────────────────────────────


class TestRecordAssistantTurn:
    """Tests for _record_assistant_turn helper."""

    def test_no_cm_does_nothing(self):
        orch = _build_orchestrator()
        stream = DocumentaryStream(
            request_id="r1",
            session_id="s1",
            mode=Mode.VOICE,
            elements=[],
        )
        # Should not raise
        orch._record_assistant_turn(stream, "test topic")

    def test_records_narration_summary(self):
        cm_mock = MagicMock()
        cm_mock.add_assistant_turn = MagicMock()

        orch = _build_orchestrator(conversation_manager=cm_mock)
        elem = ContentElement(
            type=ContentElementType.NARRATION,
            narration_text="Rome was amazing.",
            audio_duration=3.0,
        )
        stream = DocumentaryStream(
            request_id="r1",
            session_id="s1",
            mode=Mode.VOICE,
            elements=[elem],
        )
        orch._record_assistant_turn(stream, "Rome")

        cm_mock.add_assistant_turn.assert_called_once()
        call_args = cm_mock.add_assistant_turn.call_args
        assert "Rome was amazing." in call_args.args[0]
        assert call_args.kwargs["topic"] == "Rome"

    def test_records_fallback_when_no_narration(self):
        cm_mock = MagicMock()
        cm_mock.add_assistant_turn = MagicMock()

        orch = _build_orchestrator(conversation_manager=cm_mock)
        stream = DocumentaryStream(
            request_id="r1",
            session_id="s1",
            mode=Mode.VOICE,
            elements=[],
        )
        orch._record_assistant_turn(stream, "Rome")

        cm_mock.add_assistant_turn.assert_called_once()
        call_args = cm_mock.add_assistant_turn.call_args
        assert "Documentary about Rome" in call_args.args[0]
