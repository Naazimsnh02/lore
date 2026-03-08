"""Unit tests for VoiceMode handler.

Design reference: LORE design.md, VoiceMode Implementation.
Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6.
"""

from __future__ import annotations

import base64
import math
import struct
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.voice_mode.handler import (
    MIN_AUDIO_DURATION_MS,
    NOISE_THRESHOLD_DB,
    VoiceModeHandler,
)
from backend.services.voice_mode.models import (
    AudioMetadata,
    NoiseLevel,
    SUPPORTED_LANGUAGES,
    TranscriptionResult,
    VoiceModeContext,
    VoiceModeEvent,
    VoiceModeResponse,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_pcm_audio(
    frequency: float = 440.0,
    amplitude: int = 10000,
    duration_s: float = 1.0,
    sample_rate: int = 16000,
) -> bytes:
    """Generate a sine-wave LINEAR16 PCM audio buffer."""
    num_samples = int(sample_rate * duration_s)
    buf = bytearray()
    for i in range(num_samples):
        sample = int(amplitude * math.sin(2 * math.pi * frequency * i / sample_rate))
        buf.extend(struct.pack("<h", max(-32768, min(32767, sample))))
    return bytes(buf)


def _make_silence(duration_s: float = 1.0, sample_rate: int = 16000) -> bytes:
    """Generate a silent LINEAR16 PCM buffer (all zeros)."""
    num_samples = int(sample_rate * duration_s)
    return b"\x00\x00" * num_samples


def _make_loud_audio(amplitude: int = 30000, duration_s: float = 1.0) -> bytes:
    """Generate a loud tone (high dB)."""
    return _make_pcm_audio(amplitude=amplitude, duration_s=duration_s)


def _make_short_audio(duration_ms: float = 100.0, sample_rate: int = 16000) -> bytes:
    """Generate audio shorter than MIN_AUDIO_DURATION_MS."""
    return _make_pcm_audio(duration_s=duration_ms / 1000.0, sample_rate=sample_rate)


def _to_b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _mock_client(response_text: str = "ancient Rome") -> MagicMock:
    """Create a mock genai client that returns the given transcription text."""
    client = MagicMock()
    response = MagicMock()
    response.text = response_text
    client.aio.models.generate_content = AsyncMock(return_value=response)
    return client


# ── VoiceModeHandler: basic processing ───────────────────────────────────────


class TestVoiceModeHandlerBasic:
    """Test core voice processing pipeline."""

    @pytest.mark.asyncio
    async def test_process_valid_voice_input(self):
        """Req 3.1: Process voice input and extract topic."""
        client = _mock_client("Tell me about ancient Rome")
        handler = VoiceModeHandler(genai_client=client)

        audio = _make_pcm_audio(duration_s=2.0)
        resp = await handler.process_voice_input(_to_b64(audio))

        assert resp.event == VoiceModeEvent.TOPIC_DETECTED
        assert resp.topic == "ancient Rome"
        assert resp.transcription is not None
        assert resp.transcription.text == "Tell me about ancient Rome"

    @pytest.mark.asyncio
    async def test_process_returns_detected_language(self):
        """Req 3.6: Language detection from transcription."""
        client = _mock_client("[fr] La Tour Eiffel")
        handler = VoiceModeHandler(genai_client=client)

        audio = _make_pcm_audio(duration_s=1.0)
        resp = await handler.process_voice_input(_to_b64(audio))

        assert resp.event == VoiceModeEvent.TOPIC_DETECTED
        assert resp.detected_language == "fr"
        assert resp.topic == "La Tour Eiffel"

    @pytest.mark.asyncio
    async def test_invalid_base64_returns_error(self):
        handler = VoiceModeHandler()
        resp = await handler.process_voice_input("not-valid-base64!!!")
        assert resp.event == VoiceModeEvent.ERROR
        assert "invalid_base64" in resp.payload.get("error", "")

    @pytest.mark.asyncio
    async def test_short_audio_returns_buffered(self):
        """Audio shorter than MIN_AUDIO_DURATION_MS is buffered."""
        handler = VoiceModeHandler()
        audio = _make_short_audio(duration_ms=50.0)
        resp = await handler.process_voice_input(_to_b64(audio))
        assert resp.event == VoiceModeEvent.INPUT_BUFFERED
        assert resp.payload.get("reason") == "too_short"

    @pytest.mark.asyncio
    async def test_silence_detected(self):
        """Silent audio returns SILENCE_DETECTED event."""
        handler = VoiceModeHandler()
        audio = _make_silence(duration_s=1.0)
        resp = await handler.process_voice_input(_to_b64(audio))
        assert resp.event == VoiceModeEvent.SILENCE_DETECTED

    @pytest.mark.asyncio
    async def test_no_client_silence_after_noise(self):
        """When no genai client, transcription returns None → silence."""
        handler = VoiceModeHandler(genai_client=None)
        audio = _make_pcm_audio(duration_s=1.0)
        resp = await handler.process_voice_input(_to_b64(audio))
        assert resp.event == VoiceModeEvent.SILENCE_DETECTED

    @pytest.mark.asyncio
    async def test_empty_transcription_returns_silence(self):
        """Empty transcription text treated as silence."""
        client = _mock_client("")
        handler = VoiceModeHandler(genai_client=client)
        audio = _make_pcm_audio(duration_s=1.0)
        resp = await handler.process_voice_input(_to_b64(audio))
        assert resp.event == VoiceModeEvent.SILENCE_DETECTED


# ── Noise analysis ───────────────────────────────────────────────────────────


class TestNoiseAnalysis:
    """Req 3.5: Noise cancellation when ambient > 70 dB."""

    @pytest.mark.asyncio
    async def test_high_noise_triggers_cancellation(self):
        """Loud audio should set noise_cancelled=True."""
        client = _mock_client("some topic")
        handler = VoiceModeHandler(genai_client=client)
        audio = _make_loud_audio(amplitude=31000, duration_s=1.0)
        resp = await handler.process_voice_input(_to_b64(audio))
        assert resp.noise_cancelled is True
        assert resp.noise_level == NoiseLevel.HIGH

    @pytest.mark.asyncio
    async def test_moderate_noise_no_cancellation(self):
        """Moderate audio should not trigger noise cancellation."""
        client = _mock_client("a topic")
        handler = VoiceModeHandler(genai_client=client)
        audio = _make_pcm_audio(amplitude=1000, duration_s=1.0)
        resp = await handler.process_voice_input(_to_b64(audio))
        assert resp.noise_cancelled is False

    def test_estimate_noise_db_silence(self):
        """Silence should return 0 dB."""
        audio = _make_silence()
        db = VoiceModeHandler._estimate_noise_db(audio)
        assert db == 0.0

    def test_estimate_noise_db_loud(self):
        """Very loud audio should yield high dB."""
        audio = _make_loud_audio(amplitude=30000, duration_s=0.5)
        db = VoiceModeHandler._estimate_noise_db(audio)
        assert db > 60.0

    def test_estimate_noise_db_empty(self):
        db = VoiceModeHandler._estimate_noise_db(b"")
        assert db == 0.0

    def test_classify_noise_low(self):
        handler = VoiceModeHandler()
        assert handler._classify_noise(30.0) == NoiseLevel.LOW

    def test_classify_noise_moderate(self):
        handler = VoiceModeHandler()
        assert handler._classify_noise(60.0) == NoiseLevel.MODERATE

    def test_classify_noise_high(self):
        handler = VoiceModeHandler()
        assert handler._classify_noise(75.0) == NoiseLevel.HIGH


# ── Audio analysis ───────────────────────────────────────────────────────────


class TestAudioAnalysis:
    def test_analyse_audio_duration(self):
        """Duration calculation from PCM bytes."""
        audio = _make_pcm_audio(duration_s=2.0, sample_rate=16000)
        meta = VoiceModeHandler._analyse_audio(audio, 16000, time.time())
        assert abs(meta.duration_ms - 2000.0) < 1.0

    def test_analyse_audio_zero_sample_rate(self):
        audio = _make_pcm_audio(duration_s=1.0)
        meta = VoiceModeHandler._analyse_audio(audio, 0, time.time())
        assert meta.duration_ms == 0.0


# ── Topic parsing ────────────────────────────────────────────────────────────


class TestTopicParsing:
    """Test conversational prefix stripping."""

    def test_strip_tell_me_about(self):
        assert VoiceModeHandler._parse_topic("Tell me about the pyramids") == "the pyramids"

    def test_strip_what_is(self):
        assert VoiceModeHandler._parse_topic("What is quantum physics?") == "quantum physics"

    def test_strip_explain(self):
        assert VoiceModeHandler._parse_topic("explain the French Revolution") == "the French Revolution"

    def test_plain_topic_unchanged(self):
        assert VoiceModeHandler._parse_topic("ancient Rome") == "ancient Rome"

    def test_empty_input(self):
        assert VoiceModeHandler._parse_topic("") == ""

    def test_prefix_only(self):
        """If only the prefix matches and nothing is left, return original."""
        assert VoiceModeHandler._parse_topic("tell me about ") == "tell me about"

    def test_strip_trailing_punctuation(self):
        assert VoiceModeHandler._parse_topic("Who was Julius Caesar?") == "Julius Caesar"

    def test_case_insensitive_prefix(self):
        assert VoiceModeHandler._parse_topic("TELL ME ABOUT cats") == "cats"

    def test_lets_explore(self):
        assert VoiceModeHandler._parse_topic("Let's explore the Colosseum") == "the Colosseum"

    def test_im_curious_about(self):
        assert VoiceModeHandler._parse_topic("I'm curious about black holes") == "black holes"


# ── Callback and state ───────────────────────────────────────────────────────


class TestHandlerState:
    @pytest.mark.asyncio
    async def test_on_topic_detected_callback(self):
        """Callback fires when a topic is detected."""
        callback = AsyncMock()
        client = _mock_client("the Colosseum")
        handler = VoiceModeHandler(genai_client=client, on_topic_detected=callback)

        audio = _make_pcm_audio(duration_s=1.0)
        await handler.process_voice_input(_to_b64(audio))

        callback.assert_awaited_once()
        ctx = callback.call_args[0][0]
        assert isinstance(ctx, VoiceModeContext)
        assert ctx.topic == "the Colosseum"

    @pytest.mark.asyncio
    async def test_callback_error_does_not_crash(self):
        """Handler should not crash if callback raises."""
        callback = AsyncMock(side_effect=RuntimeError("callback boom"))
        client = _mock_client("a topic")
        handler = VoiceModeHandler(genai_client=client, on_topic_detected=callback)

        audio = _make_pcm_audio(duration_s=1.0)
        resp = await handler.process_voice_input(_to_b64(audio))
        assert resp.event == VoiceModeEvent.TOPIC_DETECTED

    def test_reset_clears_state(self):
        handler = VoiceModeHandler()
        handler._last_detected_language = "fr"
        handler._input_count = 5
        handler.reset()
        assert handler.last_detected_language == "en"
        assert handler.input_count == 0

    @pytest.mark.asyncio
    async def test_input_count_increments(self):
        client = _mock_client("test")
        handler = VoiceModeHandler(genai_client=client)
        audio = _make_pcm_audio(duration_s=1.0)
        await handler.process_voice_input(_to_b64(audio))
        await handler.process_voice_input(_to_b64(audio))
        assert handler.input_count == 2


# ── Language support ─────────────────────────────────────────────────────────


class TestLanguageSupport:
    """Req 3.6: Support 24 languages."""

    def test_24_languages_defined(self):
        assert len(SUPPORTED_LANGUAGES) == 24

    def test_all_codes_are_two_chars(self):
        for code in SUPPORTED_LANGUAGES:
            assert len(code) == 2

    @pytest.mark.asyncio
    async def test_language_persists_across_calls(self):
        client = _mock_client("[es] La Alhambra")
        handler = VoiceModeHandler(genai_client=client)
        audio = _make_pcm_audio(duration_s=1.0)
        await handler.process_voice_input(_to_b64(audio))
        assert handler.last_detected_language == "es"

    @pytest.mark.asyncio
    async def test_unsupported_language_code_ignored(self):
        """Unsupported language codes should not update last_detected_language."""
        client = _mock_client("[xx] something")
        handler = VoiceModeHandler(genai_client=client, default_language="en")
        audio = _make_pcm_audio(duration_s=1.0)
        resp = await handler.process_voice_input(_to_b64(audio))
        # "xx" is not in SUPPORTED_LANGUAGES → keeps default
        assert handler.last_detected_language == "en"


# ── Transcription error handling ─────────────────────────────────────────────


class TestTranscriptionErrors:
    @pytest.mark.asyncio
    async def test_api_error_returns_silence(self):
        """API exception during transcription → silence (graceful degradation)."""
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(side_effect=RuntimeError("API error"))
        handler = VoiceModeHandler(genai_client=client)
        audio = _make_pcm_audio(duration_s=1.0)
        resp = await handler.process_voice_input(_to_b64(audio))
        assert resp.event == VoiceModeEvent.SILENCE_DETECTED

    @pytest.mark.asyncio
    async def test_none_response_text_returns_silence(self):
        client = MagicMock()
        response = MagicMock()
        response.text = None
        client.aio.models.generate_content = AsyncMock(return_value=response)
        handler = VoiceModeHandler(genai_client=client)
        audio = _make_pcm_audio(duration_s=1.0)
        resp = await handler.process_voice_input(_to_b64(audio))
        assert resp.event == VoiceModeEvent.SILENCE_DETECTED

    @pytest.mark.asyncio
    async def test_session_and_user_passed_to_context(self):
        client = _mock_client("Rome")
        handler = VoiceModeHandler(genai_client=client)
        audio = _make_pcm_audio(duration_s=1.0)
        resp = await handler.process_voice_input(
            _to_b64(audio), session_id="s1", user_id="u1", previous_topics=["Egypt"]
        )
        ctx = resp.payload.get("context", {})
        assert ctx["session_id"] == "s1"
        assert ctx["user_id"] == "u1"
        assert "Egypt" in ctx["previous_topics"]


# ── Model tests ──────────────────────────────────────────────────────────────


class TestModels:
    def test_voice_mode_response_defaults(self):
        r = VoiceModeResponse(event=VoiceModeEvent.TOPIC_DETECTED)
        assert r.noise_cancelled is False
        assert r.payload == {}

    def test_transcription_result_validation(self):
        t = TranscriptionResult(text="hello", confidence=0.95)
        assert t.language == "en"
        assert t.is_final is True

    def test_voice_mode_context_defaults(self):
        c = VoiceModeContext(topic="test")
        assert c.mode == "voice"
        assert c.language == "en"
        assert c.previous_topics == []

    def test_audio_metadata_defaults(self):
        m = AudioMetadata()
        assert m.sample_rate == 16000
        assert m.encoding == "LINEAR16"
