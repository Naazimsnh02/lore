"""LoreMode handler — camera + voice fusion for advanced documentary generation.

Design reference: LORE design.md, LoreMode Implementation (Fusion) section.
Requirements:
  4.1 — Process camera frames and voice input concurrently
  4.2 — Fuse contextual information from both sources
  4.3 — Enable Alternate_History_Engine only in LoreMode
  4.5 — Link visual and spoken contexts for cross-modal queries
  4.6 — Prioritise voice input over camera when processing load exceeds capacity

Architecture notes
------------------
LoreModeHandler sits between the WebSocket Gateway and the Orchestrator.  It
delegates camera processing to SightModeHandler and voice processing to
VoiceModeHandler, then fuses their outputs via FusionEngine.

The handler maintains processing load metrics and adjusts camera frame rate
when the system is overloaded (Req 4.6).  It also detects alternate history
("what if") questions to route them appropriately.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Callable, Coroutine, Optional

from .fusion_engine import FusionEngine
from .models import (
    FusedContext,
    LoreModeEvent,
    LoreModeResponse,
    ProcessingLoad,
    ProcessingPriority,
)

logger = logging.getLogger(__name__)

# Patterns that indicate a "what if" / alternate history question
_WHAT_IF_PATTERNS: list[str] = [
    r"\bwhat if\b",
    r"\bimagine if\b",
    r"\bsuppose\b",
    r"\bwhat would happen if\b",
    r"\bhow would .+ be different if\b",
    r"\bwhat could have happened if\b",
    r"\balternate history\b",
    r"\balternative history\b",
]

# Default camera frame rate limits
NORMAL_FRAME_RATE: float = 1.0  # 1 fps normal
DEGRADED_FRAME_RATE: float = 0.5  # 0.5 fps when overloaded

# Timeout for concurrent processing
CONCURRENT_TIMEOUT_S: float = 5.0


class LoreModeHandler:
    """Processes multimodal input (camera + voice) for LoreMode documentaries.

    Combines SightModeHandler and VoiceModeHandler outputs via FusionEngine
    to produce a unified FusedContext.  Handles processing priority, alternate
    history detection, and load-based degradation.

    Parameters
    ----------
    sight_handler:
        SightModeHandler instance (Task 8).
    voice_handler:
        VoiceModeHandler instance (Task 15).
    fusion_engine:
        FusionEngine instance for context fusion.  If None, a default
        FusionEngine is created.
    on_documentary_trigger:
        Optional async callback invoked when a fused documentary context
        is ready.  Receives a ``FusedContext``.
    """

    def __init__(
        self,
        sight_handler: Any = None,
        voice_handler: Any = None,
        fusion_engine: Optional[FusionEngine] = None,
        on_documentary_trigger: Optional[
            Callable[[FusedContext], Coroutine[Any, Any, None]]
        ] = None,
    ) -> None:
        self._sight_handler = sight_handler
        self._voice_handler = voice_handler
        self._fusion_engine = fusion_engine or FusionEngine()
        self._on_documentary_trigger = on_documentary_trigger

        # Processing load tracking
        self._load = ProcessingLoad()
        self._concurrent_tasks: int = 0

    # ── Public API ────────────────────────────────────────────────────────────

    async def process_multimodal_input(
        self,
        camera_frame: Optional[str] = None,
        voice_audio: Optional[str] = None,
        voice_topic: Optional[str] = None,
        gps_location: Optional[dict[str, float]] = None,
        *,
        sample_rate: int = 16000,
        timestamp: Optional[float] = None,
        session_id: str = "",
        user_id: str = "",
        language: str = "en",
        previous_topics: Optional[list[str]] = None,
    ) -> LoreModeResponse:
        """Process camera frame and voice input concurrently, then fuse.

        This is the primary entry point for LoreMode.  Both inputs are
        processed in parallel (Req 4.1), then fused (Req 4.2).  If only
        one input is available, it still produces a result (graceful
        degradation).

        Args:
            camera_frame: Base64-encoded camera frame (JPEG).
            voice_audio: Base64-encoded audio (LINEAR16 PCM).
            voice_topic: Pre-transcribed voice topic (alternative to audio).
            gps_location: Dict with latitude, longitude, accuracy.
            sample_rate: Audio sample rate in Hz.
            timestamp: Client-side timestamp (epoch seconds).
            session_id: Current session ID.
            user_id: Authenticated user ID.
            language: ISO 639-1 language code.
            previous_topics: Previously covered topics.

        Returns:
            LoreModeResponse with the fused context or an appropriate event.
        """
        ts = timestamp or time.time()

        # Check what inputs we have
        has_camera = camera_frame is not None
        has_voice = voice_audio is not None or voice_topic is not None

        if not has_camera and not has_voice:
            return LoreModeResponse(
                event=LoreModeEvent.ERROR,
                payload={"error": "no_input", "detail": "Neither camera nor voice input provided"},
                timestamp=ts,
            )

        # Update load tracking
        self._concurrent_tasks += 1
        self._load.concurrent_tasks = self._concurrent_tasks

        try:
            # Determine processing priority (Req 4.6)
            priority = self._fusion_engine._determine_priority(self._load)

            # Process camera and voice in parallel (Req 4.1)
            visual_result, verbal_result = await self._process_inputs_parallel(
                camera_frame=camera_frame,
                voice_audio=voice_audio,
                voice_topic=voice_topic,
                gps_location=gps_location,
                sample_rate=sample_rate,
                timestamp=ts,
                session_id=session_id,
                user_id=user_id,
                language=language,
                previous_topics=previous_topics or [],
                priority=priority,
            )

            # Handle single-mode fallbacks
            if not has_camera and has_voice:
                return self._build_voice_only_response(verbal_result, ts)
            if has_camera and not has_voice:
                return self._build_camera_only_response(visual_result, ts)

            # Fuse contexts (Req 4.2)
            frame_bytes = None
            if camera_frame:
                import base64
                try:
                    frame_bytes = base64.b64decode(camera_frame)
                except Exception:
                    pass

            fused = self._fusion_engine.fuse(
                visual_context=visual_result,
                verbal_context=verbal_result,
                gps_context=gps_location,
                frame_data=frame_bytes,
                processing_load=self._load,
            )

            # Check for alternate history request (Req 4.3, 4.4)
            topic = verbal_result.get("topic", "") if verbal_result else ""
            original_query = verbal_result.get("original_query", topic)
            if self.is_what_if(original_query):
                fused_with_alt = fused.model_copy()
                return LoreModeResponse(
                    event=LoreModeEvent.ALTERNATE_HISTORY,
                    fused_context=fused_with_alt,
                    payload={
                        "type": "alternate_history",
                        "what_if_query": original_query,
                        "place_name": fused.place_name,
                        "topic": fused.topic,
                        "fused_topic": fused.fused_topic,
                        "connections": len(fused.cross_modal_connections),
                    },
                    timestamp=ts,
                )

            # Fire callback if registered
            if self._on_documentary_trigger:
                try:
                    await self._on_documentary_trigger(fused)
                except Exception:
                    logger.exception("Documentary trigger callback failed")

            # Determine if we should report load degradation
            event = LoreModeEvent.DOCUMENTARY_TRIGGER
            if priority in (ProcessingPriority.VOICE_PRIORITY, ProcessingPriority.DEGRADED):
                event = LoreModeEvent.LOAD_DEGRADED

            return LoreModeResponse(
                event=event,
                fused_context=fused,
                payload={
                    "type": "documentary_trigger",
                    "place_name": fused.place_name,
                    "topic": fused.topic,
                    "fused_topic": fused.fused_topic,
                    "connections": len(fused.cross_modal_connections),
                    "priority": fused.processing_priority.value,
                },
                timestamp=ts,
            )

        finally:
            self._concurrent_tasks = max(0, self._concurrent_tasks - 1)
            self._load.concurrent_tasks = self._concurrent_tasks

    def reset(self) -> None:
        """Reset handler state (e.g., on mode switch or new session)."""
        self._load = ProcessingLoad()
        self._concurrent_tasks = 0
        if self._sight_handler:
            self._sight_handler.reset()
        if self._voice_handler:
            self._voice_handler.reset()

    @property
    def processing_load(self) -> ProcessingLoad:
        """Current processing load metrics."""
        return self._load

    @property
    def current_frame_rate(self) -> float:
        """Recommended camera frame rate based on load."""
        if self._load.is_overloaded:
            return DEGRADED_FRAME_RATE
        return NORMAL_FRAME_RATE

    # ── Alternate history detection ──────────────────────────────────────────

    @staticmethod
    def is_what_if(text: str) -> bool:
        """Detect if the user is asking a what-if / alternate history question.

        Uses regex pattern matching against common alternate history phrases.
        """
        if not text:
            return False
        text_lower = text.lower()
        return any(re.search(p, text_lower) for p in _WHAT_IF_PATTERNS)

    # ── Internal processing ──────────────────────────────────────────────────

    async def _process_inputs_parallel(
        self,
        *,
        camera_frame: Optional[str],
        voice_audio: Optional[str],
        voice_topic: Optional[str],
        gps_location: Optional[dict[str, float]],
        sample_rate: int,
        timestamp: float,
        session_id: str,
        user_id: str,
        language: str,
        previous_topics: list[str],
        priority: ProcessingPriority,
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        """Process camera and voice inputs in parallel.

        When priority is VOICE_PRIORITY or DEGRADED, camera processing
        may be skipped or given a shorter timeout.

        Returns (visual_result, verbal_result) — either may be None.
        """
        tasks: dict[str, asyncio.Task[Any]] = {}

        # Camera processing task
        if camera_frame and priority != ProcessingPriority.DEGRADED:
            tasks["camera"] = asyncio.create_task(
                self._process_camera(
                    camera_frame, gps_location, timestamp
                )
            )

        # Voice processing task
        if voice_audio or voice_topic:
            tasks["voice"] = asyncio.create_task(
                self._process_voice(
                    voice_audio=voice_audio,
                    voice_topic=voice_topic,
                    sample_rate=sample_rate,
                    timestamp=timestamp,
                    session_id=session_id,
                    user_id=user_id,
                    language=language,
                    previous_topics=previous_topics,
                )
            )

        visual_result: Optional[dict[str, Any]] = None
        verbal_result: Optional[dict[str, Any]] = None

        # Wait for all tasks with timeout
        if tasks:
            timeout = CONCURRENT_TIMEOUT_S
            if priority == ProcessingPriority.VOICE_PRIORITY:
                timeout = 3.0  # Shorter timeout, voice is priority

            done, pending = await asyncio.wait(
                tasks.values(),
                timeout=timeout,
                return_when=asyncio.ALL_COMPLETED,
            )

            # Cancel timed-out tasks
            for task in pending:
                task.cancel()
                logger.warning("Task timed out and was cancelled")

            # Extract results
            if "camera" in tasks:
                camera_task = tasks["camera"]
                if camera_task.done() and not camera_task.cancelled():
                    try:
                        visual_result = camera_task.result()
                        if visual_result:
                            self._load.camera_latency_ms = visual_result.get(
                                "_latency_ms", 0.0
                            )
                    except Exception as exc:
                        logger.warning("Camera processing failed: %s", exc)

            if "voice" in tasks:
                voice_task = tasks["voice"]
                if voice_task.done() and not voice_task.cancelled():
                    try:
                        verbal_result = voice_task.result()
                        if verbal_result:
                            self._load.voice_latency_ms = verbal_result.get(
                                "_latency_ms", 0.0
                            )
                    except Exception as exc:
                        logger.warning("Voice processing failed: %s", exc)

        return visual_result, verbal_result

    async def _process_camera(
        self,
        camera_frame: str,
        gps_location: Optional[dict[str, float]],
        timestamp: float,
    ) -> Optional[dict[str, Any]]:
        """Process a camera frame through SightModeHandler.

        Returns a dict with location context or None on failure.
        """
        if not self._sight_handler:
            logger.warning("SightModeHandler not configured — skipping camera")
            return None

        start = time.monotonic()
        try:
            response = await self._sight_handler.process_frame(
                frame_base64=camera_frame,
                gps_location=gps_location,
                timestamp=int(timestamp * 1000),
            )

            elapsed_ms = (time.monotonic() - start) * 1000

            # Import SightModeEvent to check response type
            from ..sight_mode.models import SightModeEvent

            if response.event == SightModeEvent.DOCUMENTARY_TRIGGER:
                result = dict(response.payload)
                result["_latency_ms"] = elapsed_ms
                return result

            logger.info("SightMode event=%s (not documentary)", response.event.value)
            return None

        except Exception:
            logger.exception("Camera processing failed")
            return None

    async def _process_voice(
        self,
        *,
        voice_audio: Optional[str],
        voice_topic: Optional[str],
        sample_rate: int,
        timestamp: float,
        session_id: str,
        user_id: str,
        language: str,
        previous_topics: list[str],
    ) -> Optional[dict[str, Any]]:
        """Process voice input through VoiceModeHandler or use pre-transcribed topic.

        Returns a dict with topic context or None on failure.
        """
        start = time.monotonic()

        # If we have a pre-transcribed topic, use it directly
        if voice_topic and not voice_audio:
            elapsed_ms = (time.monotonic() - start) * 1000
            return {
                "topic": voice_topic,
                "original_query": voice_topic,
                "language": language,
                "confidence": 1.0,
                "_latency_ms": elapsed_ms,
            }

        # Process raw audio through VoiceModeHandler
        if not self._voice_handler:
            if voice_topic:
                return {
                    "topic": voice_topic,
                    "original_query": voice_topic,
                    "language": language,
                    "confidence": 1.0,
                    "_latency_ms": 0.0,
                }
            logger.warning("VoiceModeHandler not configured — skipping voice")
            return None

        try:
            from ..voice_mode.models import VoiceModeEvent

            response = await self._voice_handler.process_voice_input(
                audio_base64=voice_audio,
                sample_rate=sample_rate,
                timestamp=timestamp,
                session_id=session_id,
                user_id=user_id,
                previous_topics=previous_topics,
            )

            elapsed_ms = (time.monotonic() - start) * 1000

            if response.event == VoiceModeEvent.TOPIC_DETECTED:
                return {
                    "topic": response.topic or "",
                    "original_query": (
                        response.transcription.text
                        if response.transcription
                        else response.topic or ""
                    ),
                    "language": response.detected_language or language,
                    "confidence": (
                        response.transcription.confidence
                        if response.transcription
                        else 0.0
                    ),
                    "_latency_ms": elapsed_ms,
                }

            # For other events (silence, error, buffered), use fallback
            if voice_topic:
                return {
                    "topic": voice_topic,
                    "original_query": voice_topic,
                    "language": language,
                    "confidence": 0.5,
                    "_latency_ms": elapsed_ms,
                }

            return None

        except Exception:
            logger.exception("Voice processing failed")
            if voice_topic:
                return {
                    "topic": voice_topic,
                    "original_query": voice_topic,
                    "language": language,
                    "confidence": 0.5,
                    "_latency_ms": 0.0,
                }
            return None

    # ── Fallback responses ───────────────────────────────────────────────────

    @staticmethod
    def _build_voice_only_response(
        verbal_result: Optional[dict[str, Any]], timestamp: float
    ) -> LoreModeResponse:
        """Build response when only voice input is available."""
        if not verbal_result:
            return LoreModeResponse(
                event=LoreModeEvent.ERROR,
                payload={"error": "voice_failed", "detail": "Voice processing returned no result"},
                timestamp=timestamp,
            )

        fused = FusedContext(
            mode="lore",
            topic=verbal_result.get("topic", ""),
            original_query=verbal_result.get("original_query", ""),
            language=verbal_result.get("language", "en"),
            verbal_confidence=verbal_result.get("confidence", 0.0),
            fused_topic=verbal_result.get("topic", ""),
        )
        return LoreModeResponse(
            event=LoreModeEvent.VOICE_ONLY,
            fused_context=fused,
            payload={
                "type": "voice_only",
                "topic": verbal_result.get("topic", ""),
                "message": "Camera input not available; using voice only.",
            },
            timestamp=timestamp,
        )

    @staticmethod
    def _build_camera_only_response(
        visual_result: Optional[dict[str, Any]], timestamp: float
    ) -> LoreModeResponse:
        """Build response when only camera input is available."""
        if not visual_result:
            return LoreModeResponse(
                event=LoreModeEvent.ERROR,
                payload={"error": "camera_failed", "detail": "Camera processing returned no result"},
                timestamp=timestamp,
            )

        place_name = visual_result.get("place_name", "")
        fused = FusedContext(
            mode="lore",
            place_id=visual_result.get("place_id", ""),
            place_name=place_name,
            place_description=visual_result.get("place_description", ""),
            place_types=visual_result.get("place_types", []),
            latitude=visual_result.get("latitude", 0.0),
            longitude=visual_result.get("longitude", 0.0),
            visual_description=visual_result.get("visual_description", ""),
            visual_confidence=visual_result.get("confidence", 0.0),
            fused_topic=place_name or "Unknown location",
        )
        return LoreModeResponse(
            event=LoreModeEvent.CAMERA_ONLY,
            fused_context=fused,
            payload={
                "type": "camera_only",
                "place_name": place_name,
                "message": "Voice input not available; using camera only.",
            },
            timestamp=timestamp,
        )
