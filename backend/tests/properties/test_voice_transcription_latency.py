"""Property test: Voice Transcription Latency.

Feature: lore-multimodal-documentary-app
Property 3: Voice Transcription Latency

Validates: Requirement 3.2 — transcription completes within 500ms.

Strategy: Generate random audio payloads and verify that the VoiceModeHandler's
transcription pipeline completes under the target latency (using a mock client
to isolate handler overhead from actual API latency).
"""

from __future__ import annotations

import base64
import math
import struct
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.services.voice_mode.handler import VoiceModeHandler
from backend.services.voice_mode.models import VoiceModeEvent


# ── Strategies ───────────────────────────────────────────────────────────────


@st.composite
def audio_payload(draw: st.DrawFn) -> tuple[str, int]:
    """Generate a random base64 LINEAR16 PCM audio payload.

    Returns (base64_string, sample_rate).
    """
    sample_rate = draw(st.sampled_from([8000, 16000, 44100]))
    duration_ms = draw(st.integers(min_value=300, max_value=5000))
    amplitude = draw(st.integers(min_value=500, max_value=25000))
    frequency = draw(st.floats(min_value=100.0, max_value=2000.0))

    num_samples = int(sample_rate * duration_ms / 1000.0)
    buf = bytearray()
    for i in range(num_samples):
        sample = int(amplitude * math.sin(2 * math.pi * frequency * i / sample_rate))
        buf.extend(struct.pack("<h", max(-32768, min(32767, sample))))
    b64 = base64.b64encode(bytes(buf)).decode("ascii")
    return b64, sample_rate


def _fast_mock_client(text: str = "test topic", latency_ms: float = 5.0) -> MagicMock:
    """Mock genai client with configurable simulated latency."""
    client = MagicMock()
    response = MagicMock()
    response.text = text

    async def _gen(*args, **kwargs):
        # Simulate a tiny processing delay
        import asyncio
        await asyncio.sleep(latency_ms / 1000.0)
        return response

    client.aio.models.generate_content = _gen
    return client


# ── Property tests ───────────────────────────────────────────────────────────


class TestVoiceTranscriptionLatencyProperty:
    """Property 3: Voice Transcription Latency < 500ms.

    We test that the handler's processing pipeline (decode, analyse, noise
    check, transcribe, parse) completes well under 500ms with a fast mock
    client.  This validates that the handler itself does not introduce
    excessive overhead.
    """

    @given(payload=audio_payload())
    @settings(max_examples=120, deadline=5000)
    @pytest.mark.asyncio
    async def test_handler_pipeline_latency(self, payload: tuple[str, int]):
        """Feature: lore-multimodal-documentary-app, Property 3: Voice Transcription Latency."""
        audio_b64, sample_rate = payload
        client = _fast_mock_client(latency_ms=2.0)
        handler = VoiceModeHandler(genai_client=client)

        start = time.monotonic()
        resp = await handler.process_voice_input(
            audio_b64, sample_rate=sample_rate
        )
        elapsed_ms = (time.monotonic() - start) * 1000.0

        # Handler overhead must be well under 500ms
        # (the mock client adds ~2ms; real API latency is separate)
        assert elapsed_ms < 500.0, (
            f"Pipeline latency {elapsed_ms:.1f}ms exceeds 500ms target"
        )

        # Result should be valid
        assert resp.event in (
            VoiceModeEvent.TOPIC_DETECTED,
            VoiceModeEvent.SILENCE_DETECTED,
            VoiceModeEvent.INPUT_BUFFERED,
        )

    @given(payload=audio_payload())
    @settings(max_examples=120, deadline=5000)
    @pytest.mark.asyncio
    async def test_noise_analysis_is_fast(self, payload: tuple[str, int]):
        """Noise analysis should not be a bottleneck."""
        audio_b64, sample_rate = payload
        audio_bytes = base64.b64decode(audio_b64)

        start = time.monotonic()
        _ = VoiceModeHandler._estimate_noise_db(audio_bytes)
        elapsed_ms = (time.monotonic() - start) * 1000.0

        assert elapsed_ms < 50.0, f"Noise analysis took {elapsed_ms:.1f}ms"

    @given(payload=audio_payload())
    @settings(max_examples=120, deadline=5000)
    @pytest.mark.asyncio
    async def test_audio_analysis_is_fast(self, payload: tuple[str, int]):
        """Audio metadata analysis should be sub-millisecond."""
        audio_b64, sample_rate = payload
        audio_bytes = base64.b64decode(audio_b64)

        start = time.monotonic()
        meta = VoiceModeHandler._analyse_audio(audio_bytes, sample_rate, time.time())
        elapsed_ms = (time.monotonic() - start) * 1000.0

        assert elapsed_ms < 50.0
        assert meta.duration_ms > 0
