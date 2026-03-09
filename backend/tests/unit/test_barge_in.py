"""Unit tests for the Barge-In Handler service.

Tests cover:
- Interruption detection and acknowledgment timing (Req 19.2)
- Interjection classification (Req 19.3)
- Question handling (Req 19.4)
- Topic change handling (Req 19.5)
- Resume from interruption point (Req 19.6)

Design reference: LORE design.md, Section 9 (Barge-In Handler).
Requirements: 19.1-19.6.
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from backend.services.barge_in.handler import BargeInHandler
from backend.services.barge_in.models import (
    BargeInResult,
    InterjectionResponse,
    InterjectionType,
    Interruption,
    PlaybackState,
    ResumeAction,
)
from backend.services.voice_mode.models import (
    ConversationIntent,
    IntentClassification,
    VoiceModeContext,
    VoiceModeResponse,
    VoiceModeEvent,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


def make_voice_response(topic="test", query="test query", language="en"):
    """Helper to create a VoiceModeResponse with proper structure."""
    return VoiceModeResponse(
        event=VoiceModeEvent.TOPIC_DETECTED,
        topic=topic,
        payload={"context": {
            "topic": topic,
            "original_query": query,
            "language": language,
            "mode": "voice",
            "intent": "new_topic",
            "confidence": 0.9,
            "noise_cancelled": False,
        }},
    )


@pytest.fixture
def mock_genai_client():
    """Mock Google GenAI client."""
    client = MagicMock()
    return client


@pytest.fixture
def mock_voice_handler():
    """Mock VoiceModeHandler."""
    handler = AsyncMock()
    return handler


@pytest.fixture
def mock_conversation_manager():
    """Mock ConversationManager."""
    manager = AsyncMock()
    return manager


@pytest.fixture
def barge_in_handler(mock_voice_handler, mock_conversation_manager):
    """Create a BargeInHandler with mocked dependencies."""
    return BargeInHandler(
        voice_handler=mock_voice_handler,
        conversation_manager=mock_conversation_manager,
    )


@pytest.fixture
def sample_interruption():
    """Create a sample interruption."""
    audio_data = base64.b64encode(b"fake_audio_data").decode("ascii")
    return Interruption(
        audio_data=audio_data,
        stream_position=15.5,
        client_id="client_123",
        session_id="session_456",
    )


# ── Test: Initialization ─────────────────────────────────────────────────────


def test_handler_initialization():
    """Test that BargeInHandler initializes correctly."""
    handler = BargeInHandler()
    assert handler is not None
    assert handler._voice_handler is not None
    assert len(handler._playback_states) == 0


def test_handler_initialization_with_callbacks():
    """Test initialization with pause/resume callbacks."""
    pause_callback = Mock()
    resume_callback = Mock()
    
    handler = BargeInHandler(
        on_pause_callback=pause_callback,
        on_resume_callback=resume_callback,
    )
    
    assert handler._on_pause == pause_callback
    assert handler._on_resume == resume_callback


# ── Test: Playback State Management ──────────────────────────────────────────


def test_update_playback_position(barge_in_handler):
    """Test updating playback position for a client."""
    barge_in_handler.update_playback_position(
        client_id="client_1",
        position=10.5,
        session_id="session_1",
        mode="voice",
    )
    
    state = barge_in_handler.get_playback_state("client_1")
    assert state is not None
    assert state.client_id == "client_1"
    assert state.current_position == 10.5
    assert state.session_id == "session_1"
    assert state.mode == "voice"
    assert state.is_playing is True


def test_update_playback_position_multiple_times(barge_in_handler):
    """Test that position updates correctly track progress."""
    barge_in_handler.update_playback_position("client_1", 5.0)
    barge_in_handler.update_playback_position("client_1", 10.0)
    barge_in_handler.update_playback_position("client_1", 15.0)
    
    state = barge_in_handler.get_playback_state("client_1")
    assert state.current_position == 15.0


def test_get_playback_state_nonexistent_client(barge_in_handler):
    """Test getting state for a client that doesn't exist."""
    state = barge_in_handler.get_playback_state("nonexistent")
    assert state is None


def test_is_paused_initially_false(barge_in_handler):
    """Test that clients are not paused initially."""
    barge_in_handler.update_playback_position("client_1", 5.0)
    assert barge_in_handler.is_paused("client_1") is False


def test_is_paused_nonexistent_client(barge_in_handler):
    """Test is_paused for nonexistent client."""
    assert barge_in_handler.is_paused("nonexistent") is False


# ── Test: Interruption Processing ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_interruption_pauses_playback(
    barge_in_handler,
    sample_interruption,
    mock_voice_handler,
):
    """Test that processing an interruption pauses playback.
    
    Requirement 19.2: Pause within 200ms of speech detection.
    """
    mock_voice_handler.process_voice_input.return_value = make_voice_response(
        topic="test topic",
        query="what is this?",
    )
    
    result = await barge_in_handler.process_interruption(sample_interruption)
    
    assert result.acknowledged is True
    assert barge_in_handler.is_paused(sample_interruption.client_id) is True
    
    state = barge_in_handler.get_playback_state(sample_interruption.client_id)
    assert state.current_position == sample_interruption.stream_position
    assert state.paused_at is not None


@pytest.mark.asyncio
async def test_process_interruption_acknowledgment_timing(
    barge_in_handler,
    sample_interruption,
    mock_voice_handler,
):
    """Test that acknowledgment happens within 200ms.
    
    Requirement 19.2: Pause within 200ms of speech detection.
    """
    mock_voice_handler.process_voice_input.return_value = make_voice_response()
    
    result = await barge_in_handler.process_interruption(sample_interruption)
    
    assert result.acknowledgment_time_ms < 200.0


@pytest.mark.asyncio
async def test_process_interruption_calls_pause_callback(
    mock_voice_handler,
    sample_interruption,
):
    """Test that pause callback is invoked."""
    pause_callback = Mock()
    handler = BargeInHandler(
        voice_handler=mock_voice_handler,
        on_pause_callback=pause_callback,
    )
    
    mock_voice_handler.process_voice_input.return_value = make_voice_response()
    
    await handler.process_interruption(sample_interruption)
    
    pause_callback.assert_called_once_with(
        sample_interruption.client_id,
        sample_interruption.stream_position,
    )


@pytest.mark.asyncio
async def test_process_interruption_with_silence(
    barge_in_handler,
    sample_interruption,
    mock_voice_handler,
):
    """Test handling interruption with silence (no transcription)."""
    mock_voice_handler.process_voice_input.return_value = VoiceModeResponse(
        event=VoiceModeEvent.SILENCE_DETECTED,
        payload={},
    )
    
    result = await barge_in_handler.process_interruption(sample_interruption)
    
    assert result.acknowledged is True
    assert result.interjection_response is not None
    assert result.interjection_response.transcription == ""
    assert result.interjection_response.resume_action == ResumeAction.CONTINUE


@pytest.mark.asyncio
async def test_process_interruption_error_handling(
    barge_in_handler,
    sample_interruption,
    mock_voice_handler,
):
    """Test error handling during interruption processing."""
    mock_voice_handler.process_voice_input.side_effect = Exception("Transcription failed")
    
    result = await barge_in_handler.process_interruption(sample_interruption)
    
    assert result.acknowledged is True
    assert result.error is not None
    assert "Transcription failed" in result.error


# ── Test: Interjection Classification ───────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_question_interjection(
    barge_in_handler,
    sample_interruption,
    mock_voice_handler,
    mock_conversation_manager,
):
    """Test classification of question interjections.
    
    Requirement 19.4: Answer questions before resuming.
    """
    mock_voice_handler.process_voice_input.return_value = make_voice_response(
        topic="history",
        query="What happened in 1776?",
    )
    
    mock_conversation_manager.handle_input.return_value = IntentClassification(
        intent=ConversationIntent.QUESTION,
        confidence=0.9,
        extracted_topic="history",
    )
    
    result = await barge_in_handler.process_interruption(sample_interruption)
    
    assert result.interjection_response is not None
    assert result.interjection_response.type == InterjectionType.QUESTION
    assert result.interjection_response.resume_action == ResumeAction.CONTINUE


@pytest.mark.asyncio
async def test_classify_topic_change_interjection(
    barge_in_handler,
    sample_interruption,
    mock_voice_handler,
    mock_conversation_manager,
):
    """Test classification of topic change interjections.
    
    Requirement 19.5: Handle topic changes via branch or redirect.
    """
    mock_voice_handler.process_voice_input.return_value = VoiceModeResponse(
        event=VoiceModeEvent.TOPIC_DETECTED,
        topic="architecture",
        payload={"context": {
            "topic": "architecture",
            "original_query": "Let's talk about architecture instead",
            "language": "en",
            "mode": "voice",
            "intent": "new_topic",
            "confidence": 0.9,
            "noise_cancelled": False,
        }},
    )
    
    mock_conversation_manager.handle_input.return_value = IntentClassification(
        intent=ConversationIntent.NEW_TOPIC,
        confidence=0.85,
        extracted_topic="architecture",
    )
    
    result = await barge_in_handler.process_interruption(sample_interruption)
    
    assert result.interjection_response.type == InterjectionType.TOPIC_CHANGE
    assert result.interjection_response.resume_action == ResumeAction.REDIRECT


@pytest.mark.asyncio
async def test_classify_branch_request_interjection(
    barge_in_handler,
    sample_interruption,
    mock_voice_handler,
    mock_conversation_manager,
):
    """Test classification of branch request interjections.
    
    Requirement 19.5: Handle topic changes via branch or redirect.
    """
    mock_voice_handler.process_voice_input.return_value = VoiceModeResponse(
        event=VoiceModeEvent.TOPIC_DETECTED,
        topic="Roman Empire",
        payload={"context": {
            "topic": "Roman Empire",
            "original_query": "Tell me more about the Roman Empire",
            "language": "en",
            "mode": "voice",
            "intent": "new_topic",
            "confidence": 0.9,
            "noise_cancelled": False,
        }},
    )
    
    mock_conversation_manager.handle_input.return_value = IntentClassification(
        intent=ConversationIntent.BRANCH,
        confidence=0.88,
        extracted_topic="Roman Empire",
        branch_topic="Roman Empire",
    )
    
    result = await barge_in_handler.process_interruption(sample_interruption)
    
    assert result.interjection_response.type == InterjectionType.BRANCH_REQUEST
    assert result.interjection_response.resume_action == ResumeAction.BRANCH
    assert result.interjection_response.branch_topic == "Roman Empire"


@pytest.mark.asyncio
async def test_classify_command_interjection(
    barge_in_handler,
    sample_interruption,
    mock_voice_handler,
    mock_conversation_manager,
):
    """Test classification of command interjections."""
    mock_voice_handler.process_voice_input.return_value = VoiceModeResponse(
        event=VoiceModeEvent.TOPIC_DETECTED,
        topic="",
        payload={"context": {
            "topic": "",
            "original_query": "pause",
            "language": "en",
            "mode": "voice",
            "intent": "new_topic",
            "confidence": 0.9,
            "noise_cancelled": False,
        }},
    )
    
    mock_conversation_manager.handle_input.return_value = IntentClassification(
        intent=ConversationIntent.COMMAND,
        confidence=0.95,
        extracted_topic="",
    )
    
    result = await barge_in_handler.process_interruption(sample_interruption)
    
    assert result.interjection_response.type == InterjectionType.COMMAND
    assert result.interjection_response.resume_action == ResumeAction.PAUSE


@pytest.mark.asyncio
async def test_classify_follow_up_interjection(
    barge_in_handler,
    sample_interruption,
    mock_voice_handler,
    mock_conversation_manager,
):
    """Test classification of follow-up interjections."""
    mock_voice_handler.process_voice_input.return_value = VoiceModeResponse(
        event=VoiceModeEvent.TOPIC_DETECTED,
        topic="history",
        payload={"context": {
            "topic": "history",
            "original_query": "and then what happened?",
            "language": "en",
            "mode": "voice",
            "intent": "new_topic",
            "confidence": 0.9,
            "noise_cancelled": False,
        }},
    )
    
    mock_conversation_manager.handle_input.return_value = IntentClassification(
        intent=ConversationIntent.FOLLOW_UP,
        confidence=0.8,
        extracted_topic="history",
    )
    
    result = await barge_in_handler.process_interruption(sample_interruption)
    
    assert result.interjection_response.type == InterjectionType.FOLLOW_UP
    assert result.interjection_response.resume_action == ResumeAction.CONTINUE


# ── Test: Heuristic Classification (Fallback) ───────────────────────────────


@pytest.mark.asyncio
async def test_heuristic_classification_question(
    mock_voice_handler,
    sample_interruption,
):
    """Test heuristic classification of questions without ConversationManager."""
    handler = BargeInHandler(
        voice_handler=mock_voice_handler,
        conversation_manager=None,  # No conversation manager
    )
    
    mock_voice_handler.process_voice_input.return_value = VoiceModeResponse(
        event=VoiceModeEvent.TOPIC_DETECTED,
        topic="history",
        payload={"context": {
            "topic": "history",
            "original_query": "What is the capital of France?",
            "language": "en",
            "mode": "voice",
            "intent": "new_topic",
            "confidence": 0.9,
            "noise_cancelled": False,
        }},
    )
    
    result = await handler.process_interruption(sample_interruption)
    
    assert result.interjection_response.type == InterjectionType.QUESTION


@pytest.mark.asyncio
async def test_heuristic_classification_command(
    mock_voice_handler,
    sample_interruption,
):
    """Test heuristic classification of commands."""
    handler = BargeInHandler(
        voice_handler=mock_voice_handler,
        conversation_manager=None,
    )
    
    mock_voice_handler.process_voice_input.return_value = VoiceModeResponse(
        event=VoiceModeEvent.TOPIC_DETECTED,
        topic="",
        payload={"context": {
            "topic": "",
            "original_query": "stop playback",
            "language": "en",
            "mode": "voice",
            "intent": "new_topic",
            "confidence": 0.9,
            "noise_cancelled": False,
        }},
    )
    
    result = await handler.process_interruption(sample_interruption)
    
    assert result.interjection_response.type == InterjectionType.COMMAND


@pytest.mark.asyncio
async def test_heuristic_classification_branch(
    mock_voice_handler,
    sample_interruption,
):
    """Test heuristic classification of branch requests."""
    handler = BargeInHandler(
        voice_handler=mock_voice_handler,
        conversation_manager=None,
    )
    
    mock_voice_handler.process_voice_input.return_value = VoiceModeResponse(
        event=VoiceModeEvent.TOPIC_DETECTED,
        topic="ancient Rome",
        payload={"context": {
            "topic": "ancient Rome",
            "original_query": "tell me more about ancient Rome",
            "language": "en",
            "mode": "voice",
            "intent": "new_topic",
            "confidence": 0.9,
            "noise_cancelled": False,
        }},
    )
    
    result = await handler.process_interruption(sample_interruption)
    
    assert result.interjection_response.type == InterjectionType.BRANCH_REQUEST
    assert result.interjection_response.branch_topic == "ancient rome"  # Lowercased by heuristic


# ── Test: Resume Playback ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resume_playback_from_interruption_point(barge_in_handler):
    """Test resuming playback from interruption point.
    
    Requirement 19.6: Resume from interruption point.
    """
    # Setup: pause at position 10.0
    barge_in_handler.update_playback_position("client_1", 10.0)
    await barge_in_handler._pause_playback("client_1", 10.0, "session_1")
    
    # Resume
    success = await barge_in_handler.resume_playback("client_1")
    
    assert success is True
    assert barge_in_handler.is_paused("client_1") is False
    
    state = barge_in_handler.get_playback_state("client_1")
    assert state.current_position == 10.0
    assert state.paused_at is None


@pytest.mark.asyncio
async def test_resume_playback_from_custom_position(barge_in_handler):
    """Test resuming playback from a custom position."""
    barge_in_handler.update_playback_position("client_1", 10.0)
    await barge_in_handler._pause_playback("client_1", 10.0, "session_1")
    
    # Resume from different position
    success = await barge_in_handler.resume_playback("client_1", from_position=15.0)
    
    assert success is True
    state = barge_in_handler.get_playback_state("client_1")
    assert state.current_position == 15.0


@pytest.mark.asyncio
async def test_resume_playback_calls_resume_callback(mock_voice_handler):
    """Test that resume callback is invoked."""
    resume_callback = Mock()
    handler = BargeInHandler(
        voice_handler=mock_voice_handler,
        on_resume_callback=resume_callback,
    )
    
    handler.update_playback_position("client_1", 10.0)
    await handler._pause_playback("client_1", 10.0, "session_1")
    
    await handler.resume_playback("client_1")
    
    resume_callback.assert_called_once_with("client_1", 10.0)


@pytest.mark.asyncio
async def test_resume_playback_nonexistent_client(barge_in_handler):
    """Test resuming playback for nonexistent client."""
    success = await barge_in_handler.resume_playback("nonexistent")
    assert success is False


# ── Test: Command Parsing ────────────────────────────────────────────────────


def test_parse_command_pause():
    """Test parsing pause command."""
    action = BargeInHandler._parse_command_action("pause playback")
    assert action == ResumeAction.PAUSE


def test_parse_command_stop():
    """Test parsing stop command."""
    action = BargeInHandler._parse_command_action("stop")
    assert action == ResumeAction.PAUSE


def test_parse_command_restart():
    """Test parsing restart command."""
    action = BargeInHandler._parse_command_action("restart from beginning")
    assert action == ResumeAction.RESTART


def test_parse_command_go_back():
    """Test parsing go back command."""
    action = BargeInHandler._parse_command_action("go back to main topic")
    assert action == ResumeAction.CONTINUE


def test_parse_command_unknown():
    """Test parsing unknown command defaults to continue."""
    action = BargeInHandler._parse_command_action("something else")
    assert action == ResumeAction.CONTINUE


# ── Test: Integration Scenarios ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_interruption_cycle(
    barge_in_handler,
    mock_voice_handler,
    mock_conversation_manager,
):
    """Test a complete interruption cycle: pause, process, resume."""
    # Setup
    client_id = "client_test"
    barge_in_handler.update_playback_position(client_id, 20.0)
    
    # Create interruption
    audio_data = base64.b64encode(b"test_audio").decode("ascii")
    interruption = Interruption(
        audio_data=audio_data,
        stream_position=20.0,
        client_id=client_id,
        session_id="session_test",
    )
    
    # Mock responses
    mock_voice_handler.process_voice_input.return_value = VoiceModeResponse(
        event=VoiceModeEvent.TOPIC_DETECTED,
        topic="test",
        payload={"context": {
            "topic": "test",
            "original_query": "What is this?",
            "language": "en",
            "mode": "voice",
            "intent": "new_topic",
            "confidence": 0.9,
            "noise_cancelled": False,
        }},
    )
    
    mock_conversation_manager.handle_input.return_value = IntentClassification(
        intent=ConversationIntent.QUESTION,
        confidence=0.9,
        extracted_topic="test",
    )
    
    # Process interruption
    result = await barge_in_handler.process_interruption(interruption)
    
    assert result.acknowledged is True
    assert barge_in_handler.is_paused(client_id) is True
    assert result.interjection_response.type == InterjectionType.QUESTION
    
    # Resume playback
    success = await barge_in_handler.resume_playback(client_id)
    
    assert success is True
    assert barge_in_handler.is_paused(client_id) is False


@pytest.mark.asyncio
async def test_multiple_interruptions_same_client(
    barge_in_handler,
    mock_voice_handler,
    mock_conversation_manager,
):
    """Test handling multiple interruptions from the same client."""
    client_id = "client_multi"
    
    mock_voice_handler.process_voice_input.return_value = VoiceModeResponse(
        event=VoiceModeEvent.TOPIC_DETECTED,
        topic="test", payload={"context": {"topic": "test", "original_query": "test", "language": "en", "mode": "voice", "intent": "new_topic", "confidence": 0.9, "noise_cancelled": False}},
    )
    
    mock_conversation_manager.handle_input.return_value = IntentClassification(
        intent=ConversationIntent.QUESTION,
        confidence=0.9,
        extracted_topic="test",
    )
    
    # First interruption at 10.0
    int1 = Interruption(
        audio_data=base64.b64encode(b"audio1").decode(),
        stream_position=10.0,
        client_id=client_id,
        session_id="session_1",
    )
    await barge_in_handler.process_interruption(int1)
    await barge_in_handler.resume_playback(client_id, from_position=15.0)
    
    # Second interruption at 20.0
    int2 = Interruption(
        audio_data=base64.b64encode(b"audio2").decode(),
        stream_position=20.0,
        client_id=client_id,
        session_id="session_1",
    )
    result = await barge_in_handler.process_interruption(int2)
    
    assert result.acknowledged is True
    state = barge_in_handler.get_playback_state(client_id)
    assert state.current_position == 20.0


@pytest.mark.asyncio
async def test_concurrent_interruptions_different_clients(
    barge_in_handler,
    mock_voice_handler,
    mock_conversation_manager,
):
    """Test handling concurrent interruptions from different clients."""
    mock_voice_handler.process_voice_input.return_value = VoiceModeResponse(
        event=VoiceModeEvent.TOPIC_DETECTED,
        topic="test", payload={"context": {"topic": "test", "original_query": "test", "language": "en", "mode": "voice", "intent": "new_topic", "confidence": 0.9, "noise_cancelled": False}},
    )
    
    mock_conversation_manager.handle_input.return_value = IntentClassification(
        intent=ConversationIntent.QUESTION,
        confidence=0.9,
        extracted_topic="test",
    )
    
    int1 = Interruption(
        audio_data=base64.b64encode(b"audio1").decode(),
        stream_position=10.0,
        client_id="client_1",
        session_id="session_1",
    )
    
    int2 = Interruption(
        audio_data=base64.b64encode(b"audio2").decode(),
        stream_position=15.0,
        client_id="client_2",
        session_id="session_2",
    )
    
    # Process concurrently
    results = await asyncio.gather(
        barge_in_handler.process_interruption(int1),
        barge_in_handler.process_interruption(int2),
    )
    
    assert all(r.acknowledged for r in results)
    assert barge_in_handler.is_paused("client_1") is True
    assert barge_in_handler.is_paused("client_2") is True
    
    state1 = barge_in_handler.get_playback_state("client_1")
    state2 = barge_in_handler.get_playback_state("client_2")
    assert state1.current_position == 10.0
    assert state2.current_position == 15.0
