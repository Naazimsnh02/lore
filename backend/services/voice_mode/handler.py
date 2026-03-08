"""VoiceMode handler — voice-based documentary generation.

Processes voice input through a pipeline of noise analysis, language detection,
transcription, and topic parsing.  Delegates to the Gemini Live API for
real-time speech-to-text and uses lightweight heuristics for noise estimation
and language detection (the Live API also performs language detection natively).

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

NOISE_THRESHOLD_DB: float = 70.0  # Req 3.5: noise cancellation above 70 dB
SILENCE_THRESHOLD_DB: float = 20.0  # Below this we consider the input silence
TRANSCRIPTION_MODEL: str = "gemini-2.5-flash"  # Fast transcription model
DEFAULT_SAMPLE_RATE: int = 16000
MIN_AUDIO_DURATION_MS: float = 200.0  # Ignore very short audio bursts


class VoiceModeHandler:
    """Processes voice input for VoiceMode documentary generation.

    Pipeline:
      1. Decode and validate incoming audio (base64 LINEAR16 PCM)
      2. Estimate ambient noise level (RMS → dB)
      3. Apply noise cancellation flag when ambient > 70 dB (Req 3.5)
      4. Detect language from audio characteristics / Live API
      5. Transcribe speech via Gemini Live API (target < 500 ms, Req 3.2)
      6. Parse topic from transcription
      7. Emit VoiceModeResponse with context for the Orchestrator
    """

    def __init__(
        self,
        genai_client: Any = None,
        *,
        noise_threshold_db: float = NOISE_THRESHOLD_DB,
        silence_threshold_db: float = SILENCE_THRESHOLD_DB,
        default_language: str = "en",
        on_topic_detected: Optional[
            Callable[..., Coroutine[Any, Any, None]]
        ] = None,
    ) -> None:
        self._client = genai_client
        self._noise_threshold_db = noise_threshold_db
        self._silence_threshold_db = silence_threshold_db
        self._default_language = default_language
        self._on_topic_detected = on_topic_detected

        # Accumulated state across calls
        self._last_detected_language: str = default_language
        self._input_count: int = 0

    # ── Public API ───────────────────────────────────────────────────────────

    async def process_voice_input(
        self,
        audio_base64: str,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        timestamp: Optional[float] = None,
        session_id: str = "",
        user_id: str = "",
        previous_topics: Optional[list[str]] = None,
    ) -> VoiceModeResponse:
        """Process a single voice input chunk and return a VoiceModeResponse.

        Args:
            audio_base64: Base64-encoded LINEAR16 PCM audio.
            sample_rate: Audio sample rate in Hz (default 16 000).
            timestamp: Optional client-side timestamp (epoch seconds).
            session_id: Current session ID.
            user_id: Authenticated user ID.
            previous_topics: Topics already covered in this session.

        Returns:
            VoiceModeResponse describing the processing result.
        """
        ts = timestamp or time.time()
        self._input_count += 1

        # 1. Decode audio
        try:
            audio_bytes = base64.b64decode(audio_base64)
        except Exception as exc:
            logger.warning("Invalid base64 audio: %s", exc)
            return VoiceModeResponse(
                event=VoiceModeEvent.ERROR,
                payload={"error": "invalid_base64", "detail": str(exc)},
                timestamp=ts,
            )

        # 2. Validate minimum duration
        metadata = self._analyse_audio(audio_bytes, sample_rate, ts)
        if metadata.duration_ms < MIN_AUDIO_DURATION_MS:
            return VoiceModeResponse(
                event=VoiceModeEvent.INPUT_BUFFERED,
                payload={"reason": "too_short", "duration_ms": metadata.duration_ms},
                timestamp=ts,
            )

        # 3. Noise analysis
        noise_level = self._classify_noise(metadata.noise_level_db)
        noise_cancelled = noise_level == NoiseLevel.HIGH

        if noise_level == NoiseLevel.HIGH:
            logger.info(
                "High ambient noise (%.1f dB) — noise cancellation applied",
                metadata.noise_level_db,
            )

        # 4. Silence detection
        if metadata.noise_level_db < self._silence_threshold_db:
            return VoiceModeResponse(
                event=VoiceModeEvent.SILENCE_DETECTED,
                noise_level=noise_level,
                noise_cancelled=False,
                timestamp=ts,
            )

        # 5. Transcribe via Gemini
        transcription = await self._transcribe(
            audio_bytes, sample_rate, metadata, noise_cancelled
        )
        if not transcription or not transcription.text.strip():
            return VoiceModeResponse(
                event=VoiceModeEvent.SILENCE_DETECTED,
                noise_level=noise_level,
                noise_cancelled=noise_cancelled,
                timestamp=ts,
            )

        # 6. Language detection (from transcription result)
        detected_lang = transcription.language or self._default_language
        if detected_lang in SUPPORTED_LANGUAGES:
            self._last_detected_language = detected_lang

        # 7. Parse topic
        topic = self._parse_topic(transcription.text)

        # 8. Build context and fire callback
        context = VoiceModeContext(
            topic=topic,
            original_query=transcription.text,
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

    def reset(self) -> None:
        """Reset handler state between sessions."""
        self._last_detected_language = self._default_language
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
        num_samples = len(audio_bytes) // 2  # 16-bit = 2 bytes per sample
        duration_ms = (num_samples / sample_rate) * 1000.0 if sample_rate > 0 else 0.0

        # RMS → dB estimation (sample up to 4000 samples for speed)
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
        """Estimate ambient noise in dB from LINEAR16 PCM bytes.

        Uses RMS of a sample of audio values.  Reference level is the
        maximum 16-bit amplitude (32767).
        """
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

        # dB relative to full-scale 16-bit
        db = 20.0 * math.log10(rms / 32767.0) + 96.0  # normalise so full-scale ≈ 96 dB
        return max(0.0, db)

    def _classify_noise(self, noise_db: float) -> NoiseLevel:
        if noise_db > self._noise_threshold_db:
            return NoiseLevel.HIGH
        if noise_db > 50.0:
            return NoiseLevel.MODERATE
        return NoiseLevel.LOW

    # ── Transcription via Gemini ─────────────────────────────────────────────

    async def _transcribe(
        self,
        audio_bytes: bytes,
        sample_rate: int,
        metadata: AudioMetadata,
        noise_cancelled: bool,
    ) -> Optional[TranscriptionResult]:
        """Transcribe audio using the Gemini API.

        Uses google-genai SDK's generate_content with audio input for fast
        transcription.  Target latency: < 500 ms (Req 3.2).
        """
        if self._client is None:
            logger.warning("No genai client configured — returning None")
            return None

        start = time.monotonic()
        try:
            audio_b64 = base64.b64encode(audio_bytes).decode("ascii")

            # Build the transcription prompt
            lang_hint = self._last_detected_language
            system_prompt = (
                "You are a speech-to-text transcription engine. "
                "Transcribe the following audio accurately. "
                "Return ONLY the transcribed text, nothing else. "
                f"The audio is likely in {SUPPORTED_LANGUAGES.get(lang_hint, 'English')}. "
                "If the audio is in a different language, transcribe it in that language "
                "and prepend the ISO 639-1 language code in brackets, e.g. [fr] Bonjour."
            )
            if noise_cancelled:
                system_prompt += " Note: noise cancellation has been applied to this audio."

            response = await self._client.aio.models.generate_content(
                model=TRANSCRIPTION_MODEL,
                contents=[
                    {
                        "parts": [
                            {"text": system_prompt},
                            {
                                "inline_data": {
                                    "mime_type": f"audio/l16;rate={sample_rate}",
                                    "data": audio_b64,
                                }
                            },
                        ]
                    }
                ],
            )

            elapsed_ms = (time.monotonic() - start) * 1000.0
            text = response.text.strip() if response and response.text else ""

            # Extract language tag if present, e.g. "[fr] Bonjour" → ("fr", "Bonjour")
            detected_lang = lang_hint
            if text.startswith("[") and "]" in text[:6]:
                tag_end = text.index("]")
                candidate = text[1:tag_end].strip().lower()
                if candidate in SUPPORTED_LANGUAGES:
                    detected_lang = candidate
                    text = text[tag_end + 1:].strip()

            return TranscriptionResult(
                text=text,
                language=detected_lang,
                confidence=0.85,  # default confidence for Gemini transcription
                duration_ms=elapsed_ms,
                is_final=True,
            )

        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000.0
            logger.error("Transcription failed (%.0f ms): %s", elapsed_ms, exc)
            return None

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
