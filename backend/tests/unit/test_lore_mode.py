"""Unit tests for LoreMode handler and FusionEngine.

Task 21.3 — Tests for context fusion, cross-modal connections,
and processing priority.
Requirements: 4.1, 4.2, 4.5, 4.6.
"""

from __future__ import annotations

import asyncio
import base64
import struct
import time
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.lore_mode.fusion_engine import FusionEngine
from backend.services.lore_mode.handler import LoreModeHandler, _WHAT_IF_PATTERNS
from backend.services.lore_mode.models import (
    ConnectionType,
    CrossModalConnection,
    FusedContext,
    LoreModeEvent,
    LoreModeResponse,
    ProcessingLoad,
    ProcessingPriority,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_audio_base64(duration_ms: float = 500, sample_rate: int = 16000) -> str:
    """Create a base64-encoded LINEAR16 PCM audio chunk."""
    num_samples = int(sample_rate * duration_ms / 1000)
    samples = [1000] * num_samples  # non-silent audio
    audio_bytes = struct.pack(f"<{num_samples}h", *samples)
    return base64.b64encode(audio_bytes).decode("ascii")


def _make_camera_frame() -> str:
    """Create a minimal base64-encoded fake JPEG."""
    return base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 100).decode("ascii")


def _mock_sight_handler(
    place_name: str = "Colosseum",
    place_id: str = "place_123",
    trigger: bool = True,
) -> MagicMock:
    """Create a mock SightModeHandler that returns a documentary trigger."""
    handler = MagicMock()

    async def process_frame(**kwargs):
        from backend.services.sight_mode.models import SightModeEvent, SightModeResponse

        if trigger:
            return SightModeResponse(
                event=SightModeEvent.DOCUMENTARY_TRIGGER,
                payload={
                    "place_name": place_name,
                    "place_id": place_id,
                    "place_description": "Ancient amphitheatre in Rome",
                    "place_types": ["tourist_attraction", "historical_landmark"],
                    "latitude": 41.8902,
                    "longitude": 12.4922,
                    "formatted_address": "Piazza del Colosseo, Rome",
                    "visual_description": "Large ancient stone amphitheatre",
                    "confidence": 0.92,
                },
            )
        return SightModeResponse(
            event=SightModeEvent.FRAME_BUFFERED,
            payload={"message": "Location not recognized"},
        )

    handler.process_frame = AsyncMock(side_effect=process_frame)
    handler.reset = MagicMock()
    return handler


def _mock_voice_handler(topic: str = "gladiators") -> MagicMock:
    """Create a mock VoiceModeHandler that returns a topic."""
    handler = MagicMock()

    async def process_voice_input(**kwargs):
        from backend.services.voice_mode.models import (
            TranscriptionResult,
            VoiceModeEvent,
            VoiceModeResponse,
        )

        return VoiceModeResponse(
            event=VoiceModeEvent.TOPIC_DETECTED,
            transcription=TranscriptionResult(
                text=f"Tell me about {topic}",
                language="en",
                confidence=0.9,
            ),
            topic=topic,
            detected_language="en",
        )

    handler.process_voice_input = AsyncMock(side_effect=process_voice_input)
    handler.reset = MagicMock()
    return handler


# ══════════════════════════════════════════════════════════════════════════════
# FusionEngine tests
# ══════════════════════════════════════════════════════════════════════════════


class TestFusionEngine:
    """Tests for FusionEngine.fuse() and find_connections()."""

    def setup_method(self) -> None:
        self.engine = FusionEngine()

    # ── fuse() basic tests ────────────────────────────────────────────────

    def test_fuse_both_contexts(self) -> None:
        """Fusing visual + verbal produces a combined topic."""
        result = self.engine.fuse(
            visual_context={
                "place_name": "Colosseum",
                "place_id": "p1",
                "place_description": "Ancient amphitheatre",
                "place_types": ["historical_landmark"],
                "latitude": 41.89,
                "longitude": 12.49,
            },
            verbal_context={
                "topic": "gladiators",
                "original_query": "Tell me about gladiators",
                "language": "en",
                "confidence": 0.9,
            },
        )
        assert result.mode == "lore"
        assert "gladiators" in result.fused_topic
        assert "Colosseum" in result.fused_topic
        assert result.place_name == "Colosseum"
        assert result.topic == "gladiators"
        assert result.enable_alternate_history is True

    def test_fuse_visual_only(self) -> None:
        """Fusing with only visual context uses place as topic."""
        result = self.engine.fuse(
            visual_context={
                "place_name": "Eiffel Tower",
                "place_types": ["tourist_attraction"],
            },
        )
        assert "Eiffel Tower" in result.fused_topic
        assert result.topic == ""

    def test_fuse_verbal_only(self) -> None:
        """Fusing with only verbal context uses topic directly."""
        result = self.engine.fuse(
            verbal_context={
                "topic": "quantum physics",
                "language": "en",
            },
        )
        assert result.fused_topic == "quantum physics"
        assert result.place_name == ""

    def test_fuse_empty_inputs(self) -> None:
        """Fusing with no inputs returns a fallback context."""
        result = self.engine.fuse()
        assert result.fused_topic == "Unknown topic"

    def test_fuse_gps_context(self) -> None:
        """GPS context populates gps fields in FusedContext."""
        result = self.engine.fuse(
            gps_context={"latitude": 48.8566, "longitude": 2.3522, "accuracy": 5.0},
        )
        assert result.gps_latitude == 48.8566
        assert result.gps_longitude == 2.3522
        assert result.gps_accuracy == 5.0

    def test_fuse_visual_location_preferred_over_gps(self) -> None:
        """Visual lat/lon takes precedence over GPS."""
        result = self.engine.fuse(
            visual_context={"latitude": 41.89, "longitude": 12.49},
            gps_context={"latitude": 41.0, "longitude": 12.0},
        )
        assert result.latitude == 41.89
        assert result.longitude == 12.49

    def test_fuse_gps_fallback_when_no_visual_location(self) -> None:
        """GPS lat/lon is used when visual has no location."""
        result = self.engine.fuse(
            visual_context={"place_name": "Somewhere"},
            gps_context={"latitude": 48.85, "longitude": 2.35},
        )
        assert result.latitude == 48.85
        assert result.longitude == 2.35

    def test_fuse_frame_data(self) -> None:
        """Frame data is preserved in fused context."""
        frame = b"\xff\xd8\xff\xe0JPEG"
        result = self.engine.fuse(frame_data=frame)
        assert result.frame_data == frame

    def test_fuse_language_preserved(self) -> None:
        """Language from verbal context is preserved."""
        result = self.engine.fuse(
            verbal_context={"topic": "bonjour", "language": "fr"},
        )
        assert result.language == "fr"

    # ── Processing priority tests (Req 4.6) ──────────────────────────────

    def test_normal_priority(self) -> None:
        """Default load → normal priority."""
        load = ProcessingLoad()
        result = self.engine.fuse(processing_load=load)
        assert result.processing_priority == ProcessingPriority.NORMAL

    def test_voice_priority_when_camera_slow(self) -> None:
        """High camera latency → voice priority."""
        load = ProcessingLoad(camera_latency_ms=4000, voice_latency_ms=500)
        result = self.engine.fuse(
            verbal_context={"topic": "test"},
            processing_load=load,
        )
        assert result.processing_priority == ProcessingPriority.VOICE_PRIORITY

    def test_degraded_priority_when_voice_slow(self) -> None:
        """High voice latency → degraded priority."""
        load = ProcessingLoad(camera_latency_ms=4000, voice_latency_ms=2500)
        result = self.engine.fuse(processing_load=load)
        assert result.processing_priority == ProcessingPriority.DEGRADED

    def test_degraded_skips_camera_in_fused_topic(self) -> None:
        """Under DEGRADED priority, only voice topic is used."""
        load = ProcessingLoad(voice_latency_ms=3000, camera_latency_ms=5000)
        result = self.engine.fuse(
            visual_context={"place_name": "Colosseum"},
            verbal_context={"topic": "history"},
            processing_load=load,
        )
        assert result.fused_topic == "history"
        assert "Colosseum" not in result.fused_topic

    def test_overloaded_with_many_tasks(self) -> None:
        """Many concurrent tasks → overloaded."""
        load = ProcessingLoad(concurrent_tasks=15)
        assert load.is_overloaded is True

    # ── find_connections() tests (Req 4.5) ────────────────────────────────

    def test_find_historical_connection(self) -> None:
        """Detects historical connection from place types."""
        connections = self.engine.find_connections(
            place_name="Colosseum",
            place_types=["historical_landmark", "tourist_attraction"],
            place_description="Ancient Roman amphitheatre",
            topic="gladiators",
        )
        types = [c.type for c in connections]
        assert ConnectionType.HISTORICAL in types

    def test_find_cultural_connection(self) -> None:
        """Detects cultural connection from keywords."""
        connections = self.engine.find_connections(
            place_name="Kyoto Temple",
            place_description="Traditional Japanese temple with ceremony hall",
            topic="tea ceremony tradition",
        )
        types = [c.type for c in connections]
        assert ConnectionType.CULTURAL in types

    def test_find_geographic_connection(self) -> None:
        """Detects geographic connection from keywords."""
        connections = self.engine.find_connections(
            place_name="Grand Canyon",
            place_description="Massive canyon carved by river",
            topic="river erosion",
        )
        types = [c.type for c in connections]
        assert ConnectionType.GEOGRAPHIC in types

    def test_find_thematic_connection(self) -> None:
        """Detects thematic connection from direct word overlap."""
        connections = self.engine.find_connections(
            place_name="Louvre Museum",
            place_description="Famous art museum in Paris with paintings",
            topic="paintings from Paris",
        )
        types = [c.type for c in connections]
        assert ConnectionType.THEMATIC in types

    def test_no_connections_empty_inputs(self) -> None:
        """No connections when inputs are empty."""
        connections = self.engine.find_connections(topic="", place_name="")
        assert connections == []

    def test_no_connections_topic_only(self) -> None:
        """No connections when only topic is provided."""
        connections = self.engine.find_connections(topic="quantum physics")
        assert connections == []

    def test_connections_sorted_by_relevance(self) -> None:
        """Connections are sorted by relevance (descending)."""
        connections = self.engine.find_connections(
            place_name="Castle Ruins",
            place_types=["historical_landmark"],
            place_description="Medieval castle ruins with ancient fortress walls",
            topic="medieval castle architecture art",
        )
        if len(connections) >= 2:
            for i in range(len(connections) - 1):
                assert connections[i].relevance >= connections[i + 1].relevance

    def test_connection_has_keywords(self) -> None:
        """Connections include relevant keywords."""
        connections = self.engine.find_connections(
            place_name="Ancient Temple",
            place_types=["temple"],
            place_description="Ancient temple with historical significance",
            topic="ancient temple rituals",
        )
        assert len(connections) > 0
        all_keywords = []
        for c in connections:
            all_keywords.extend(c.keywords)
        assert len(all_keywords) > 0

    # ── calculate_relevance() tests ──────────────────────────────────────

    def test_relevance_zero_no_overlap(self) -> None:
        """Zero relevance when no keyword overlap."""
        from backend.services.lore_mode.fusion_engine import _HISTORICAL_KEYWORDS

        score = FusionEngine.calculate_relevance(
            {"python", "code"}, {"testing", "unit"}, _HISTORICAL_KEYWORDS
        )
        assert score == 0.0

    def test_relevance_nonzero_with_overlap(self) -> None:
        """Non-zero relevance with domain keyword overlap."""
        from backend.services.lore_mode.fusion_engine import _HISTORICAL_KEYWORDS

        score = FusionEngine.calculate_relevance(
            {"ancient", "roman", "stone"},
            {"gladiators", "ancient", "battle"},
            _HISTORICAL_KEYWORDS,
        )
        assert score > 0.0
        assert score <= 1.0

    def test_relevance_max_at_1(self) -> None:
        """Relevance capped at 1.0."""
        from backend.services.lore_mode.fusion_engine import _HISTORICAL_KEYWORDS

        score = FusionEngine.calculate_relevance(
            _HISTORICAL_KEYWORDS,
            _HISTORICAL_KEYWORDS,
            _HISTORICAL_KEYWORDS,
        )
        assert score <= 1.0

    # ── Historical significance detection ─────────────────────────────────

    def test_historical_significance_from_types(self) -> None:
        """Place type triggers historical significance."""
        result = self.engine.fuse(
            visual_context={
                "place_types": ["museum", "tourist_attraction"],
                "place_description": "A modern art gallery",
            },
        )
        assert result.enable_historical_characters is True

    def test_historical_significance_from_description(self) -> None:
        """Historical keywords in description trigger significance."""
        result = self.engine.fuse(
            visual_context={
                "place_types": ["point_of_interest"],
                "place_description": "Ancient ruins of a Roman temple",
            },
        )
        assert result.enable_historical_characters is True

    def test_no_historical_significance(self) -> None:
        """Modern place without historical keywords."""
        result = self.engine.fuse(
            visual_context={
                "place_types": ["restaurant"],
                "place_description": "A modern Italian pizzeria",
                "visual_description": "Outdoor dining area with tables",
            },
        )
        assert result.enable_historical_characters is False


# ══════════════════════════════════════════════════════════════════════════════
# LoreModeHandler tests
# ══════════════════════════════════════════════════════════════════════════════


class TestLoreModeHandler:
    """Tests for LoreModeHandler.process_multimodal_input()."""

    def setup_method(self) -> None:
        self.sight = _mock_sight_handler()
        self.voice = _mock_voice_handler()
        self.handler = LoreModeHandler(
            sight_handler=self.sight,
            voice_handler=self.voice,
        )

    # ── Basic multimodal tests (Req 4.1, 4.2) ────────────────────────────

    @pytest.mark.asyncio
    async def test_process_both_inputs(self) -> None:
        """Camera + voice → fused documentary trigger."""
        response = await self.handler.process_multimodal_input(
            camera_frame=_make_camera_frame(),
            voice_topic="gladiators",
        )
        assert response.event == LoreModeEvent.DOCUMENTARY_TRIGGER
        assert response.fused_context is not None
        assert "gladiators" in response.fused_context.fused_topic
        assert "Colosseum" in response.fused_context.fused_topic

    @pytest.mark.asyncio
    async def test_fused_context_has_visual_fields(self) -> None:
        """Fused context contains visual data from camera."""
        response = await self.handler.process_multimodal_input(
            camera_frame=_make_camera_frame(),
            voice_topic="gladiators",
        )
        ctx = response.fused_context
        assert ctx.place_name == "Colosseum"
        assert ctx.place_id == "place_123"
        assert ctx.visual_confidence > 0.0

    @pytest.mark.asyncio
    async def test_fused_context_has_verbal_fields(self) -> None:
        """Fused context contains verbal data from voice."""
        response = await self.handler.process_multimodal_input(
            camera_frame=_make_camera_frame(),
            voice_topic="gladiators",
        )
        ctx = response.fused_context
        assert ctx.topic == "gladiators"

    @pytest.mark.asyncio
    async def test_cross_modal_connections_detected(self) -> None:
        """Cross-modal connections are found between camera and voice."""
        response = await self.handler.process_multimodal_input(
            camera_frame=_make_camera_frame(),
            voice_topic="ancient Roman battles",
        )
        ctx = response.fused_context
        # The Colosseum + "ancient Roman battles" should yield connections
        assert len(ctx.cross_modal_connections) > 0

    # ── Voice-only fallback ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_voice_only_input(self) -> None:
        """Only voice input → VOICE_ONLY event."""
        response = await self.handler.process_multimodal_input(
            voice_topic="quantum physics",
        )
        assert response.event == LoreModeEvent.VOICE_ONLY
        assert response.fused_context is not None
        assert response.fused_context.topic == "quantum physics"

    # ── Camera-only fallback ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_camera_only_input(self) -> None:
        """Only camera input → CAMERA_ONLY event."""
        response = await self.handler.process_multimodal_input(
            camera_frame=_make_camera_frame(),
        )
        assert response.event == LoreModeEvent.CAMERA_ONLY
        assert response.fused_context is not None
        assert response.fused_context.place_name == "Colosseum"

    # ── No input error ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_no_input_error(self) -> None:
        """No camera or voice → ERROR."""
        response = await self.handler.process_multimodal_input()
        assert response.event == LoreModeEvent.ERROR
        assert "no_input" in response.payload.get("error", "")

    # ── Alternate history detection (Req 4.3, 4.4) ───────────────────────

    @pytest.mark.asyncio
    async def test_what_if_detection(self) -> None:
        """'What if' question triggers ALTERNATE_HISTORY event."""
        response = await self.handler.process_multimodal_input(
            camera_frame=_make_camera_frame(),
            voice_topic="What if the Roman Empire never fell?",
        )
        assert response.event == LoreModeEvent.ALTERNATE_HISTORY
        assert response.fused_context is not None

    @pytest.mark.asyncio
    async def test_imagine_if_detection(self) -> None:
        """'Imagine if' triggers alternate history."""
        response = await self.handler.process_multimodal_input(
            camera_frame=_make_camera_frame(),
            voice_topic="Imagine if gladiators had modern weapons",
        )
        assert response.event == LoreModeEvent.ALTERNATE_HISTORY

    @pytest.mark.asyncio
    async def test_suppose_detection(self) -> None:
        """'Suppose' triggers alternate history."""
        response = await self.handler.process_multimodal_input(
            camera_frame=_make_camera_frame(),
            voice_topic="Suppose the Colosseum was never built",
        )
        assert response.event == LoreModeEvent.ALTERNATE_HISTORY

    def test_is_what_if_various_patterns(self) -> None:
        """All what-if patterns are detected."""
        test_cases = [
            ("What if Caesar lived?", True),
            ("Imagine if Rome conquered China", True),
            ("Suppose the wheel was never invented", True),
            ("What would happen if gravity reversed?", True),
            ("How would Europe be different if the plague never happened?", True),
            ("What could have happened if Napoleon won at Waterloo?", True),
            ("Tell me about alternate history scenarios", True),
            ("Tell me about gladiators", False),
            ("The history of Rome", False),
            ("", False),
        ]
        for text, expected in test_cases:
            assert LoreModeHandler.is_what_if(text) == expected, f"Failed for: {text!r}"

    # ── Camera recognition failure ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_camera_failure_graceful(self) -> None:
        """Camera failure → still produces result from voice."""
        sight = _mock_sight_handler(trigger=False)
        handler = LoreModeHandler(
            sight_handler=sight,
            voice_handler=self.voice,
        )
        response = await handler.process_multimodal_input(
            camera_frame=_make_camera_frame(),
            voice_topic="gladiators",
        )
        # Should still work with voice-only fused context
        assert response.fused_context is not None
        assert "gladiators" in response.fused_context.fused_topic

    @pytest.mark.asyncio
    async def test_camera_exception_graceful(self) -> None:
        """Camera exception → still produces result from voice."""
        sight = MagicMock()
        sight.process_frame = AsyncMock(side_effect=RuntimeError("Camera broke"))
        sight.reset = MagicMock()
        handler = LoreModeHandler(
            sight_handler=sight,
            voice_handler=self.voice,
        )
        response = await handler.process_multimodal_input(
            camera_frame=_make_camera_frame(),
            voice_topic="gladiators",
        )
        assert response.fused_context is not None

    # ── Voice processing with audio ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_voice_audio_processing(self) -> None:
        """Voice audio is processed through VoiceModeHandler."""
        response = await self.handler.process_multimodal_input(
            camera_frame=_make_camera_frame(),
            voice_audio=_make_audio_base64(),
        )
        assert response.fused_context is not None
        self.voice.process_voice_input.assert_called_once()

    # ── Callback tests ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_documentary_trigger_callback(self) -> None:
        """on_documentary_trigger callback is invoked."""
        callback = AsyncMock()
        handler = LoreModeHandler(
            sight_handler=self.sight,
            voice_handler=self.voice,
            on_documentary_trigger=callback,
        )
        await handler.process_multimodal_input(
            camera_frame=_make_camera_frame(),
            voice_topic="gladiators",
        )
        callback.assert_called_once()
        ctx = callback.call_args[0][0]
        assert isinstance(ctx, FusedContext)

    @pytest.mark.asyncio
    async def test_callback_exception_handled(self) -> None:
        """Callback exception doesn't crash handler."""
        callback = AsyncMock(side_effect=RuntimeError("Callback failed"))
        handler = LoreModeHandler(
            sight_handler=self.sight,
            voice_handler=self.voice,
            on_documentary_trigger=callback,
        )
        response = await handler.process_multimodal_input(
            camera_frame=_make_camera_frame(),
            voice_topic="gladiators",
        )
        assert response.event == LoreModeEvent.DOCUMENTARY_TRIGGER

    # ── GPS context ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_gps_context_passed(self) -> None:
        """GPS coordinates are included in fused context."""
        response = await self.handler.process_multimodal_input(
            camera_frame=_make_camera_frame(),
            voice_topic="gladiators",
            gps_location={"latitude": 41.89, "longitude": 12.49, "accuracy": 5.0},
        )
        ctx = response.fused_context
        assert ctx.gps_latitude == 41.89
        assert ctx.gps_longitude == 12.49

    # ── Reset ─────────────────────────────────────────────────────────────

    def test_reset_clears_state(self) -> None:
        """Reset clears load and delegates to sub-handlers."""
        self.handler._concurrent_tasks = 5
        self.handler._load.camera_latency_ms = 5000
        self.handler.reset()
        assert self.handler._concurrent_tasks == 0
        assert self.handler._load.camera_latency_ms == 0.0
        self.sight.reset.assert_called_once()
        self.voice.reset.assert_called_once()

    # ── Frame rate ────────────────────────────────────────────────────────

    def test_normal_frame_rate(self) -> None:
        """Normal load → 1 fps."""
        assert self.handler.current_frame_rate == 1.0

    def test_degraded_frame_rate(self) -> None:
        """Overloaded → 0.5 fps."""
        self.handler._load.camera_latency_ms = 5000
        assert self.handler.current_frame_rate == 0.5

    # ── No handlers configured ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_no_sight_handler(self) -> None:
        """Missing sight handler → camera processing skipped."""
        handler = LoreModeHandler(voice_handler=self.voice)
        response = await handler.process_multimodal_input(
            camera_frame=_make_camera_frame(),
            voice_topic="gladiators",
        )
        # With both inputs but no sight handler, camera returns None →
        # fusion still works with voice
        assert response.fused_context is not None

    @pytest.mark.asyncio
    async def test_no_voice_handler_with_topic(self) -> None:
        """Missing voice handler + pre-transcribed topic → works."""
        handler = LoreModeHandler(sight_handler=self.sight)
        response = await handler.process_multimodal_input(
            camera_frame=_make_camera_frame(),
            voice_topic="gladiators",
        )
        assert response.fused_context is not None
        assert "gladiators" in response.fused_context.fused_topic


# ══════════════════════════════════════════════════════════════════════════════
# ProcessingLoad model tests
# ══════════════════════════════════════════════════════════════════════════════


class TestProcessingLoad:
    """Tests for ProcessingLoad.is_overloaded."""

    def test_not_overloaded_defaults(self) -> None:
        load = ProcessingLoad()
        assert load.is_overloaded is False

    def test_overloaded_camera_latency(self) -> None:
        load = ProcessingLoad(camera_latency_ms=4000)
        assert load.is_overloaded is True

    def test_overloaded_voice_latency(self) -> None:
        load = ProcessingLoad(voice_latency_ms=2500)
        assert load.is_overloaded is True

    def test_overloaded_concurrent_tasks(self) -> None:
        load = ProcessingLoad(concurrent_tasks=15)
        assert load.is_overloaded is True

    def test_not_overloaded_within_thresholds(self) -> None:
        load = ProcessingLoad(
            camera_latency_ms=2000,
            voice_latency_ms=1500,
            concurrent_tasks=8,
        )
        assert load.is_overloaded is False
