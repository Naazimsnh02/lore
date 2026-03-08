"""Unit tests for SightMode handler and FrameBuffer.

Design reference: LORE design.md, SightMode Implementation.
Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6.
"""

from __future__ import annotations

import asyncio
import base64
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from backend.services.location_recognizer.models import (
    GPSCoordinates,
    LocationResult,
    PlaceDetails,
    VisualFeatures,
)
from backend.services.sight_mode.frame_buffer import FrameBuffer
from backend.services.sight_mode.handler import (
    DUPLICATE_SUPPRESS_SECONDS,
    VOICE_CLARIFICATION_TIMEOUT,
    SightModeHandler,
)
from backend.services.sight_mode.models import SightModeEvent


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_bright_frame(size: int = 50_000) -> bytes:
    """Create a fake frame with high brightness (all bytes ~200)."""
    return bytes([200] * size)


def _make_dark_frame(size: int = 50_000) -> bytes:
    """Create a fake frame with low brightness (all bytes ~10)."""
    return bytes([10] * size)


def _make_place(place_id: str = "p1", name: str = "Eiffel Tower") -> PlaceDetails:
    return PlaceDetails(
        place_id=place_id,
        name=name,
        location=GPSCoordinates(latitude=48.8584, longitude=2.2945, accuracy=5.0, timestamp=time.time()),
        types=["tourist_attraction", "monument"],
        description="Iconic Parisian landmark",
        formatted_address="Champ de Mars, 5 Avenue Anatole France, 75007 Paris",
    )


def _make_visual_features() -> VisualFeatures:
    return VisualFeatures(
        description="Tall iron lattice tower in Paris",
        landmark_name="Eiffel Tower",
        confidence=0.9,
    )


def _make_recognised_result(place: PlaceDetails | None = None) -> LocationResult:
    p = place or _make_place()
    return LocationResult(
        recognized=True,
        place=p,
        confidence=0.85,
        processing_time=1.2,
        visual_features=_make_visual_features(),
    )


def _make_unrecognised_result() -> LocationResult:
    return LocationResult(
        recognized=False,
        confidence=0.1,
        processing_time=2.5,
    )


def _mock_recognizer(result: LocationResult) -> MagicMock:
    recognizer = MagicMock()
    recognizer.recognize_location = AsyncMock(return_value=result)
    return recognizer


# ── FrameBuffer tests ─────────────────────────────────────────────────────────

class TestFrameBuffer:
    def test_add_and_count(self):
        buf = FrameBuffer(size=3)
        assert buf.count == 0
        buf.add(b"\x80" * 100)
        assert buf.count == 1
        buf.add(b"\x80" * 100)
        buf.add(b"\x80" * 100)
        assert buf.count == 3

    def test_buffer_evicts_oldest(self):
        buf = FrameBuffer(size=2)
        buf.add(b"\x10" * 100, "image/jpeg")  # dark = low quality
        buf.add(b"\xc0" * 100, "image/jpeg")  # bright = higher quality
        buf.add(b"\xff" * 100, "image/jpeg")  # brightest
        assert buf.count == 2  # oldest evicted

    def test_get_best_frame_returns_highest_quality(self):
        buf = FrameBuffer(size=5)
        buf.add(b"\x10" * 100)  # dark
        buf.add(b"\xff" * 200_000)  # bright + large
        buf.add(b"\x40" * 100)  # medium
        best = buf.get_best_frame()
        assert best is not None
        assert best.data == b"\xff" * 200_000

    def test_get_best_frame_empty_returns_none(self):
        buf = FrameBuffer(size=5)
        assert buf.get_best_frame() is None

    def test_get_latest_frame(self):
        buf = FrameBuffer(size=5)
        buf.add(b"\x10" * 100)
        buf.add(b"\x20" * 100)
        latest = buf.get_latest_frame()
        assert latest is not None
        assert latest.data == b"\x20" * 100

    def test_check_lighting_bright(self):
        buf = FrameBuffer(brightness_threshold=30.0)
        bright = _make_bright_frame()
        buf.add(bright)
        assert buf.check_lighting() is True

    def test_check_lighting_dark(self):
        buf = FrameBuffer(brightness_threshold=30.0)
        dark = _make_dark_frame()
        buf.add(dark)
        assert buf.check_lighting() is False

    def test_check_lighting_with_explicit_frame(self):
        buf = FrameBuffer(brightness_threshold=30.0)
        assert buf.check_lighting(_make_dark_frame()) is False
        assert buf.check_lighting(_make_bright_frame()) is True

    def test_check_lighting_empty_buffer_returns_true(self):
        buf = FrameBuffer()
        assert buf.check_lighting() is True

    def test_clear(self):
        buf = FrameBuffer(size=5)
        buf.add(b"\x80" * 100)
        buf.add(b"\x80" * 100)
        buf.clear()
        assert buf.count == 0

    def test_is_full(self):
        buf = FrameBuffer(size=2)
        assert buf.is_full is False
        buf.add(b"\x80" * 100)
        assert buf.is_full is False
        buf.add(b"\x80" * 100)
        assert buf.is_full is True

    def test_metadata_quality_score_range(self):
        buf = FrameBuffer()
        meta = buf.add(b"\x80" * 100)
        assert 0.0 <= meta.quality_score <= 1.0
        assert 0.0 <= meta.brightness <= 255.0


# ── SightModeHandler tests ───────────────────────────────────────────────────

class TestSightModeHandler:
    @pytest.fixture
    def recognised_handler(self):
        recognizer = _mock_recognizer(_make_recognised_result())
        return SightModeHandler(location_recognizer=recognizer)

    @pytest.fixture
    def unrecognised_handler(self):
        recognizer = _mock_recognizer(_make_unrecognised_result())
        return SightModeHandler(location_recognizer=recognizer)

    @pytest.mark.asyncio
    async def test_process_frame_triggers_documentary(self, recognised_handler):
        """Req 2.3: recognised location triggers documentary generation."""
        frame_b64 = base64.b64encode(_make_bright_frame()).decode()
        response = await recognised_handler.process_frame(frame_b64)
        assert response.event == SightModeEvent.DOCUMENTARY_TRIGGER
        assert response.payload["place_name"] == "Eiffel Tower"
        assert response.payload["confidence"] == 0.85

    @pytest.mark.asyncio
    async def test_process_frame_flash_suggestion_dark(self):
        """Req 2.6: suggest flash when lighting is insufficient."""
        recognizer = _mock_recognizer(_make_recognised_result())
        handler = SightModeHandler(location_recognizer=recognizer)
        frame_b64 = base64.b64encode(_make_dark_frame()).decode()
        response = await handler.process_frame(frame_b64)
        assert response.event == SightModeEvent.FLASH_SUGGESTION
        assert response.payload["action"] == "enable_flash"

    @pytest.mark.asyncio
    async def test_unrecognised_frame_buffered(self, unrecognised_handler):
        """Non-recognised frame should be buffered without voice prompt initially."""
        frame_b64 = base64.b64encode(_make_bright_frame()).decode()
        response = await unrecognised_handler.process_frame(frame_b64)
        assert response.event == SightModeEvent.FRAME_BUFFERED

    @pytest.mark.asyncio
    async def test_voice_clarification_after_timeout(self, unrecognised_handler):
        """Req 2.4: prompt for voice after 5 seconds of non-recognition."""
        frame_b64 = base64.b64encode(_make_bright_frame()).decode()

        # First frame — starts the timer
        await unrecognised_handler.process_frame(frame_b64)

        # Simulate that 5+ seconds have passed
        unrecognised_handler._first_unrecognised_at = time.time() - 6.0
        response = await unrecognised_handler.process_frame(frame_b64)
        assert response.event == SightModeEvent.VOICE_CLARIFICATION
        assert "Can you tell me" in response.payload["message"]

    @pytest.mark.asyncio
    async def test_voice_prompt_sent_only_once(self, unrecognised_handler):
        """Voice clarification prompt should not repeat."""
        frame_b64 = base64.b64encode(_make_bright_frame()).decode()

        await unrecognised_handler.process_frame(frame_b64)
        unrecognised_handler._first_unrecognised_at = time.time() - 6.0

        r1 = await unrecognised_handler.process_frame(frame_b64)
        assert r1.event == SightModeEvent.VOICE_CLARIFICATION

        r2 = await unrecognised_handler.process_frame(frame_b64)
        assert r2.event == SightModeEvent.FRAME_BUFFERED  # not repeated

    @pytest.mark.asyncio
    async def test_duplicate_trigger_suppressed(self, recognised_handler):
        """Same place should not trigger again within DUPLICATE_SUPPRESS_SECONDS."""
        frame_b64 = base64.b64encode(_make_bright_frame()).decode()

        r1 = await recognised_handler.process_frame(frame_b64)
        assert r1.event == SightModeEvent.DOCUMENTARY_TRIGGER

        r2 = await recognised_handler.process_frame(frame_b64)
        assert r2.event == SightModeEvent.FRAME_BUFFERED
        assert "Already generating" in r2.payload["message"]

    @pytest.mark.asyncio
    async def test_different_place_triggers_again(self):
        """A different place should trigger a new documentary."""
        place_a = _make_place("p_a", "Eiffel Tower")
        place_b = _make_place("p_b", "Colosseum")

        call_count = 0
        results = [_make_recognised_result(place_a), _make_recognised_result(place_b)]

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            r = results[min(call_count, len(results) - 1)]
            call_count += 1
            return r

        recognizer = MagicMock()
        recognizer.recognize_location = AsyncMock(side_effect=side_effect)
        handler = SightModeHandler(location_recognizer=recognizer)
        frame_b64 = base64.b64encode(_make_bright_frame()).decode()

        r1 = await handler.process_frame(frame_b64)
        assert r1.event == SightModeEvent.DOCUMENTARY_TRIGGER
        assert r1.payload["place_name"] == "Eiffel Tower"

        r2 = await handler.process_frame(frame_b64)
        assert r2.event == SightModeEvent.DOCUMENTARY_TRIGGER
        assert r2.payload["place_name"] == "Colosseum"

    @pytest.mark.asyncio
    async def test_recognition_resets_voice_timer(self, recognised_handler):
        """Successful recognition should reset the non-recognition timer."""
        frame_b64 = base64.b64encode(_make_bright_frame()).decode()

        # Set as if we've been failing for a while
        recognised_handler._first_unrecognised_at = time.time() - 10.0

        r = await recognised_handler.process_frame(frame_b64)
        assert r.event == SightModeEvent.DOCUMENTARY_TRIGGER
        assert recognised_handler._first_unrecognised_at is None
        assert recognised_handler._voice_prompt_sent is False

    @pytest.mark.asyncio
    async def test_gps_hint_forwarded(self):
        """GPS coordinates should be passed to the recognizer."""
        recognizer = _mock_recognizer(_make_recognised_result())
        handler = SightModeHandler(location_recognizer=recognizer)
        frame_b64 = base64.b64encode(_make_bright_frame()).decode()
        gps = {"latitude": 48.8584, "longitude": 2.2945}

        await handler.process_frame(frame_b64, gps_location=gps)

        call_kwargs = recognizer.recognize_location.call_args
        assert call_kwargs.kwargs.get("gps_hint") is not None or (
            len(call_kwargs.args) >= 3 and call_kwargs.args[2] is not None
        )

    @pytest.mark.asyncio
    async def test_on_documentary_trigger_callback(self):
        """The optional callback should fire on documentary trigger."""
        callback = AsyncMock()
        recognizer = _mock_recognizer(_make_recognised_result())
        handler = SightModeHandler(
            location_recognizer=recognizer,
            on_documentary_trigger=callback,
        )
        frame_b64 = base64.b64encode(_make_bright_frame()).decode()

        await handler.process_frame(frame_b64)
        callback.assert_awaited_once()
        ctx = callback.call_args[0][0]
        assert ctx.place_name == "Eiffel Tower"

    @pytest.mark.asyncio
    async def test_callback_failure_does_not_crash(self):
        """Handler should not crash if the callback raises."""
        callback = AsyncMock(side_effect=RuntimeError("boom"))
        recognizer = _mock_recognizer(_make_recognised_result())
        handler = SightModeHandler(
            location_recognizer=recognizer,
            on_documentary_trigger=callback,
        )
        frame_b64 = base64.b64encode(_make_bright_frame()).decode()

        response = await handler.process_frame(frame_b64)
        assert response.event == SightModeEvent.DOCUMENTARY_TRIGGER

    @pytest.mark.asyncio
    async def test_reset_clears_state(self, recognised_handler):
        """reset() should clear buffer and tracking state."""
        frame_b64 = base64.b64encode(_make_bright_frame()).decode()
        await recognised_handler.process_frame(frame_b64)

        recognised_handler.reset()

        assert recognised_handler.frame_buffer.count == 0
        assert recognised_handler._first_unrecognised_at is None
        assert recognised_handler._last_triggered_place_id is None
        assert recognised_handler._voice_prompt_sent is False

    @pytest.mark.asyncio
    async def test_process_frame_with_png_mime(self, recognised_handler):
        """Handler should work with PNG frames too."""
        frame_b64 = base64.b64encode(_make_bright_frame()).decode()
        response = await recognised_handler.process_frame(
            frame_b64, mime_type="image/png"
        )
        assert response.event == SightModeEvent.DOCUMENTARY_TRIGGER
