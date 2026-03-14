"""VoiceMode handler — voice-based documentary generation.

Processes transcripts received from LiveSessionManager through a pipeline of
noise analysis, language detection, and topic parsing.

With Option B (true Live API streaming), transcription is handled by
LiveSessionManager which keeps a persistent session open.  This handler's
role is now:
  1. Estimate ambient noise level from raw PCM bytes (RMS → dB)
  2. Detect language from transcript text
  3. Parse topic from transcript
  4. Build VoiceModeContext and fire the on_topic_detected callback

Design reference: LORE design.md, VoiceMode Implementation section.
Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6.
"""

from __future__ import annotations

import base64
import logging
import math
import struct
import time
from typing import Any, Callable, Coroutine, Optional

from .models import (
    AudioMetadata,
    NoiseLevel,
    SUPPORTED_LANGUAGES,
    TranscriptionResult,
    VoiceModeContext,
    VoiceModeEvent,
    VoiceModeResponse,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

NOISE_THRESHOLD_DB: float = 70.0   # Req 3.5: noise cancellation above 70 dB
SILENCE_THRESHOLD_DB: float = 20.0  # Below this we consider the input silence
DEFAULT_SAMPLE_RATE: int = 16_000
MIN_AUDIO_DURATION_MS: float = 200.0  # Ignore very short audio bursts


class VoiceModeHandler:
    """Processes transcripts from LiveSessionManager for VoiceMode documentary generation.

    With Option B (true Live API streaming), transcription is done by
    LiveSessionManager.  This handler receives the finished transcript string
    and runs the downstream pipeline:
      1. Noise level classification (from last known noise reading)
      2. Language detection from transcript text
      3. Topic parsing
      4. Build VoiceModeContext and fire on_topic_detected callback
    """

    def __init__(
        self,
        genai_client: Any = None,  # kept for API compatibility, no longer used here
        *,
        noise_threshold_db: float = NOISE_THRESHOLD_DB,
        silence_threshold_db: float = SILENCE_THRESHOLD_DB,
        default_language: str = "en",
        on_topic_detected: Optional[
            Callable[..., Coroutine[Any, Any, None]]
        ] = None,
    ) -> None:
        self._noise_threshold_db = noise_threshold_db
        self._silence_threshold_db = silence_threshold_db
        self._default_language = default_language
        self._on_topic_detected = on_topic_detected

        self._last_detected_language: str = default_language
        self._last_noise_db: float = 0.0
        self._input_count: int = 0

    # ── Public API ───────────────────────────────────────────────────────────

    async def process_transcript(
        self,
        transcript: str,
        *,
        timestamp: Optional[float] = None,
        session_id: str = "",
        user_id: str = "",
        previous_topics: Optional[list[str]] = None,
    ) -> VoiceModeResponse:
        """Process a transcript string delivered by LiveSessionManager.

        Called by the message router's on_transcript callback after the
        Live API session fires input_transcription.

        Args:
            transcript: Plain text transcript from the Live API.
            timestamp:  Optional epoch seconds timestamp.
            session_id: Current session ID.
            user_id:    Authenticated user ID.
            previous_topics: Topics already covered in this session.

        Returns:
            VoiceModeResponse with event=TOPIC_DETECTED on success.
        """
        ts = timestamp or time.time()
        self._input_count += 1

        if not transcript.strip():
            return VoiceModeResponse(
                event=VoiceModeEvent.SILENCE_DETECTED,
                noise_level=NoiseLevel.LOW,
                noise_cancelled=False,
                timestamp=ts,
            )

        noise_level = self._classify_noise(self._last_noise_db)
        noise_cancelled = noise_level == NoiseLevel.HIGH

        # Language detection from transcript text
        detected_lang, clean_text = self._detect_language(transcript)

        # Parse topic
        topic = self._parse_topic(clean_text)

        transcription = TranscriptionResult(
            text=clean_text,
            language=detected_lang,
            confidence=0.92,
            duration_ms=0.0,
            is_final=True,
        )

        context = VoiceModeContext(
            topic=topic,
            original_query=clean_text,
            language=detected_lang,
            confidence=transcription.confidence,
            noise_cancelled=noise_cancelled,
            session_id=session_id,
            user_id=user_id,
            previous_topics=previous_topics or [],
        )

        if self._on_topic_detected:
            try:
                await self._on_topic_detected(context)
            except Exception:
                logger.exception("on_topic_detected callback failed")

        return VoiceModeResponse(
            event=VoiceModeEvent.TOPIC_DETECTED,
            transcription=transcription,
            topic=topic,
            detected_language=detected_lang,
            noise_level=noise_level,
            noise_cancelled=noise_cancelled,
            payload={"context": context.model_dump()},
            timestamp=ts,
        )

    def update_noise_reading(self, noise_db: float) -> None:
        """Update the last known noise level from a PCM chunk.

        Called by the message router for each voice_chunk so noise
        classification stays current without blocking the audio path.
        """
        self._last_noise_db = noise_db

    def estimate_noise_from_chunk(self, pcm_bytes: bytes) -> float:
        """Estimate noise dB from a raw PCM chunk and update internal state."""
        db = self._estimate_noise_db(pcm_bytes)
        self._last_noise_db = db
        return db

    def reset(self) -> None:
        """Reset handler state between sessions."""
        self._last_detected_language = self._default_language
        self._last_noise_db = 0.0
        self._input_count = 0

    @property
    def last_detected_language(self) -> str:
        return self._last_detected_language

    @property
    def input_count(self) -> int:
        return self._input_count

    # ── Audio analysis (lightweight, no NumPy dependency) ────────────────────

    @staticmethod
    def _analyse_audio(
        audio_bytes: bytes, sample_rate: int, timestamp: float
    ) -> AudioMetadata:
        """Compute basic audio metadata from raw LINEAR16 PCM bytes."""
        num_samples = len(audio_bytes) // 2
        duration_ms = (num_samples / sample_rate) * 1000.0 if sample_rate > 0 else 0.0
        noise_db = VoiceModeHandler._estimate_noise_db(audio_bytes)
        return AudioMetadata(
            sample_rate=sample_rate,
            channels=1,
            encoding="LINEAR16",
            duration_ms=duration_ms,
            noise_level_db=noise_db,
            timestamp=timestamp,
        )

    @staticmethod
    def _estimate_noise_db(audio_bytes: bytes, max_samples: int = 4000) -> float:
        """Estimate ambient noise in dB from LINEAR16 PCM bytes."""
        num_samples = len(audio_bytes) // 2
        if num_samples == 0:
            return 0.0

        step = max(1, num_samples // max_samples)
        sum_sq = 0.0
        count = 0
        for i in range(0, num_samples * 2, step * 2):
            if i + 2 > len(audio_bytes):
                break
            (sample,) = struct.unpack_from("<h", audio_bytes, i)
            sum_sq += sample * sample
            count += 1

        if count == 0:
            return 0.0

        rms = math.sqrt(sum_sq / count)
        if rms < 1.0:
            return 0.0

        db = 20.0 * math.log10(rms / 32767.0) + 96.0
        return max(0.0, db)

    def _classify_noise(self, noise_db: float) -> NoiseLevel:
        if noise_db > self._noise_threshold_db:
            return NoiseLevel.HIGH
        if noise_db > 50.0:
            return NoiseLevel.MODERATE
        return NoiseLevel.LOW

    def _detect_language(self, text: str) -> tuple[str, str]:
        """Extract optional language tag from transcript and return (lang, clean_text).

        The Live API may prepend a tag like "[fr] Bonjour" when it detects
        a non-default language.
        """
        if text.startswith("[") and "]" in text[:6]:
            tag_end = text.index("]")
            candidate = text[1:tag_end].strip().lower()
            if candidate in SUPPORTED_LANGUAGES:
                self._last_detected_language = candidate
                return candidate, text[tag_end + 1:].strip()
        return self._last_detected_language, text

    # ── Topic parsing ────────────────────────────────────────────────────────

    @staticmethod
    def _parse_topic(text: str) -> str:
        """Extract the main topic from transcribed text.

        Strips common conversational prefixes like "tell me about",
        "what is", etc. to extract the core topic.
        """
        if not text:
            return ""

        cleaned = text.strip()

        # Remove common conversational prefixes (case-insensitive)
        prefixes = [
            "tell me about ",
            "tell me more about ",
            "i want to know about ",
            "i want to learn about ",
            "i'd like to know about ",
            "i'd like to learn about ",
            "what is ",
            "what are ",
            "what was ",
            "what were ",
            "who is ",
            "who was ",
            "who were ",
            "where is ",
            "where was ",
            "how did ",
            "how does ",
            "how was ",
            "why did ",
            "why does ",
            "why was ",
            "can you tell me about ",
            "could you tell me about ",
            "please tell me about ",
            "explain ",
            "describe ",
            "show me ",
            "let's explore ",
            "let's learn about ",
            "i'm curious about ",
            "i'm interested in ",
        ]

        lower = cleaned.lower()
        for prefix in prefixes:
            if lower.startswith(prefix):
                cleaned = cleaned[len(prefix):]
                break

        # Remove trailing punctuation
        cleaned = cleaned.rstrip("?.!,;:")
        return cleaned.strip() if cleaned.strip() else text.strip()
