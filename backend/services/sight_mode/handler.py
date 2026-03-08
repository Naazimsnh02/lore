"""SightMode handler — processes camera frames and triggers documentaries.

Design reference: LORE design.md, SightMode Implementation section.
Requirements:
  2.1 — Capture frames at ≥ 1 fps
  2.2 — Identify location within 3 seconds
  2.3 — Trigger documentary on recognition
  2.4 — Prompt voice clarification after 5 seconds of non-recognition
  2.5 — Maintain camera preview throughout
  2.6 — Suggest flash when lighting is insufficient

Architecture notes
------------------
The handler sits between the WebSocket Gateway (which delivers camera_frame
messages) and the Orchestrator (which generates documentary content).  It owns
the FrameBuffer for quality-based frame selection and delegates actual location
recognition to the existing LocationRecognizer service (Task 7).

The handler tracks per-session state:
  - Consecutive non-recognition count for voice clarification timing
  - Last recognised place_id to avoid duplicate triggers
  - Time of first unrecognised frame (for the 5-second voice prompt)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any, Callable, Coroutine, Optional

from ..location_recognizer.models import GPSCoordinates, LocationResult
from ..location_recognizer.recognizer import LocationRecognizer
from .frame_buffer import FrameBuffer
from .models import (
    DocumentaryContext,
    SightModeEvent,
    SightModeResponse,
)

logger = logging.getLogger(__name__)

# After this many seconds of continuous non-recognition, prompt the user (Req 2.4)
VOICE_CLARIFICATION_TIMEOUT: float = 5.0

# Suppress duplicate triggers for the same place within this window
DUPLICATE_SUPPRESS_SECONDS: float = 30.0


class SightModeHandler:
    """Processes camera frames in SightMode and triggers documentary generation.

    Parameters
    ----------
    location_recognizer:
        An initialised LocationRecognizer instance (Task 7).
    frame_buffer_size:
        Number of frames to keep in the sliding buffer (default 5).
    on_documentary_trigger:
        Optional async callback invoked when a documentary should be generated.
        Receives a ``DocumentaryContext``.  If not set, the handler simply
        returns a DOCUMENTARY_TRIGGER response for the caller to act on.
    """

    def __init__(
        self,
        location_recognizer: LocationRecognizer,
        frame_buffer_size: int = 5,
        on_documentary_trigger: Optional[
            Callable[[DocumentaryContext], Coroutine[Any, Any, None]]
        ] = None,
    ) -> None:
        self._recognizer = location_recognizer
        self._frame_buffer = FrameBuffer(size=frame_buffer_size)
        self._on_documentary_trigger = on_documentary_trigger

        # Per-session tracking
        self._first_unrecognised_at: Optional[float] = None
        self._last_triggered_place_id: Optional[str] = None
        self._last_trigger_time: float = 0.0
        self._voice_prompt_sent: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_frame(
        self,
        frame_base64: str,
        gps_location: Optional[dict[str, float]] = None,
        timestamp: Optional[int] = None,
        mime_type: str = "image/jpeg",
    ) -> SightModeResponse:
        """Process a single camera frame and return the appropriate response.

        Parameters
        ----------
        frame_base64:
            Base64-encoded image data (from the CameraFramePayload).
        gps_location:
            Optional dict with ``latitude`` and ``longitude`` keys.
        timestamp:
            Client-side capture timestamp (ms epoch).  Informational.
        mime_type:
            Image MIME type (default ``"image/jpeg"``).

        Returns
        -------
        SightModeResponse
            One of: DOCUMENTARY_TRIGGER, FLASH_SUGGESTION, VOICE_CLARIFICATION,
            FRAME_BUFFERED, or RECOGNITION_FAILED.
        """
        frame_bytes = base64.b64decode(frame_base64)

        # 1. Buffer the frame and compute quality metadata
        metadata = self._frame_buffer.add(frame_bytes, mime_type)

        # 2. Check lighting conditions (Req 2.6)
        if not self._frame_buffer.check_lighting(frame_bytes):
            logger.info("Low lighting detected (brightness=%.1f)", metadata.brightness)
            return SightModeResponse(
                event=SightModeEvent.FLASH_SUGGESTION,
                payload={
                    "type": "suggestion",
                    "message": "Lighting conditions are low. Consider enabling flash.",
                    "action": "enable_flash",
                    "brightness": metadata.brightness,
                },
            )

        # 3. Pick the best frame from the buffer for recognition
        best_frame = self._frame_buffer.get_best_frame()
        if best_frame is None:
            return SightModeResponse(
                event=SightModeEvent.FRAME_BUFFERED,
                payload={"message": "Frame buffered, waiting for more data"},
            )

        recognition_bytes = best_frame.data
        recognition_mime = best_frame.metadata.mime_type

        # 4. Build optional GPS hint
        gps_hint: Optional[GPSCoordinates] = None
        if gps_location:
            gps_hint = GPSCoordinates(
                latitude=gps_location.get("latitude", 0.0),
                longitude=gps_location.get("longitude", 0.0),
                accuracy=gps_location.get("accuracy", 0.0),
                timestamp=time.time(),
            )

        # 5. Run location recognition (3-second timeout enforced by recognizer)
        result: LocationResult = await self._recognizer.recognize_location(
            frame_bytes=recognition_bytes,
            mime_type=recognition_mime,
            gps_hint=gps_hint,
        )

        # 6. Act on recognition result
        if result.recognized and result.place is not None:
            return await self._handle_recognition_success(result, recognition_bytes)
        else:
            return self._handle_recognition_failure(result)

    def reset(self) -> None:
        """Reset handler state (e.g., on mode switch or new session)."""
        self._frame_buffer.clear()
        self._first_unrecognised_at = None
        self._last_triggered_place_id = None
        self._last_trigger_time = 0.0
        self._voice_prompt_sent = False

    @property
    def frame_buffer(self) -> FrameBuffer:
        """Access the underlying frame buffer (for testing / debugging)."""
        return self._frame_buffer

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _handle_recognition_success(
        self, result: LocationResult, frame_bytes: bytes
    ) -> SightModeResponse:
        """Build a documentary trigger from a successful recognition."""
        place = result.place
        assert place is not None  # guarded by caller

        # Suppress duplicate triggers for the same place in quick succession
        now = time.time()
        if (
            place.place_id == self._last_triggered_place_id
            and (now - self._last_trigger_time) < DUPLICATE_SUPPRESS_SECONDS
        ):
            logger.debug(
                "Suppressing duplicate trigger for %s (%.1fs since last)",
                place.name,
                now - self._last_trigger_time,
            )
            return SightModeResponse(
                event=SightModeEvent.FRAME_BUFFERED,
                payload={
                    "message": f"Already generating documentary for {place.name}",
                    "place_id": place.place_id,
                },
            )

        # Reset non-recognition tracking
        self._first_unrecognised_at = None
        self._voice_prompt_sent = False
        self._last_triggered_place_id = place.place_id
        self._last_trigger_time = now

        context = DocumentaryContext(
            mode="sight",
            place_id=place.place_id,
            place_name=place.name,
            place_description=place.description or place.editorial_summary,
            place_types=place.types,
            latitude=place.location.latitude,
            longitude=place.location.longitude,
            formatted_address=place.formatted_address,
            visual_description=(
                result.visual_features.description
                if result.visual_features
                else ""
            ),
            confidence=result.confidence,
            frame_data=frame_bytes,
        )

        logger.info(
            "SightMode documentary triggered: place=%r confidence=%.3f",
            place.name,
            result.confidence,
        )

        # Fire callback if registered (for Orchestrator integration)
        if self._on_documentary_trigger is not None:
            try:
                await self._on_documentary_trigger(context)
            except Exception:
                logger.exception("Documentary trigger callback failed")

        return SightModeResponse(
            event=SightModeEvent.DOCUMENTARY_TRIGGER,
            payload={
                "type": "documentary_trigger",
                "place_id": place.place_id,
                "place_name": place.name,
                "place_description": context.place_description,
                "place_types": place.types,
                "latitude": place.location.latitude,
                "longitude": place.location.longitude,
                "formatted_address": place.formatted_address,
                "confidence": result.confidence,
                "processing_time": result.processing_time,
            },
        )

    def _handle_recognition_failure(self, result: LocationResult) -> SightModeResponse:
        """Decide between waiting, prompting voice, or reporting failure."""
        now = time.time()

        # Start the non-recognition timer on first failure
        if self._first_unrecognised_at is None:
            self._first_unrecognised_at = now

        elapsed_since_first_failure = now - self._first_unrecognised_at

        # After 5 seconds of continuous non-recognition, prompt for voice (Req 2.4)
        if (
            elapsed_since_first_failure >= VOICE_CLARIFICATION_TIMEOUT
            and not self._voice_prompt_sent
        ):
            self._voice_prompt_sent = True
            logger.info(
                "Voice clarification prompt after %.1fs of non-recognition",
                elapsed_since_first_failure,
            )
            return SightModeResponse(
                event=SightModeEvent.VOICE_CLARIFICATION,
                payload={
                    "type": "prompt",
                    "message": (
                        "I couldn't recognize this location. "
                        "Can you tell me what you're looking at?"
                    ),
                    "action": "switch_to_voice",
                    "elapsed_seconds": round(elapsed_since_first_failure, 1),
                },
            )

        # Otherwise just report the frame was processed but nothing found
        return SightModeResponse(
            event=SightModeEvent.FRAME_BUFFERED,
            payload={
                "message": "Location not yet recognized, continuing to process",
                "confidence": result.confidence,
                "processing_time": result.processing_time,
                "error": result.error,
            },
        )
