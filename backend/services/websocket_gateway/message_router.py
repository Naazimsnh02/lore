"""Message router — dispatches validated client messages to handlers.

Each handler corresponds to one ClientMessage.type.  Downstream services
(Orchestrator, SessionMemoryManager, GPSWalker, etc.) are not yet implemented;
handlers that need them contain clearly labelled ``TODO(Task-N)`` stubs.

The router is intentionally synchronous in its dispatch logic.  Heavy work
happens asynchronously inside each handler, keeping p99 routing latency well
under the 100 ms WebSocket target (Requirement 20.7).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from .models import (
    BargeInPayload,
    BranchRequestPayload,
    CameraFramePayload,
    ChronicleExportPayload,
    ClientMessage,
    ComponentsStatus,
    DepthDialChangePayload,
    ErrorPayload,
    GPSUpdatePayload,
    ModeSelectPayload,
    QueryPayload,
    ServerMessage,
    StatusPayload,
    VoiceInputPayload,
)

if TYPE_CHECKING:
    from .connection_manager import ConnectionManager

logger = logging.getLogger(__name__)


class MessageRouter:
    """Routes a ``ClientMessage`` to the correct handler coroutine.

    Returns a list of ``ServerMessage`` objects that the gateway should
    immediately send back to the originating client.  The list may be empty
    when the response will arrive asynchronously (e.g. pushed by the
    Orchestrator later).
    """

    def __init__(self, connection_manager: "ConnectionManager") -> None:
        self._cm = connection_manager

        self._handlers = {
            "mode_select": self._handle_mode_select,
            "camera_frame": self._handle_camera_frame,
            "voice_input": self._handle_voice_input,
            "gps_update": self._handle_gps_update,
            "barge_in": self._handle_barge_in,
            "query": self._handle_query,
            "branch_request": self._handle_branch_request,
            "depth_dial_change": self._handle_depth_dial_change,
            "chronicle_export": self._handle_chronicle_export,
        }

    async def route(
        self, client_id: str, message: ClientMessage
    ) -> list[ServerMessage]:
        """Dispatch *message* and return synchronous responses."""
        handler = self._handlers.get(message.type)
        if handler is None:
            logger.warning(
                "Unknown message type '%s' from client %s",
                message.type,
                client_id,
            )
            return [
                self._error(
                    "UNKNOWN_MESSAGE_TYPE",
                    f"Unknown message type: {message.type}",
                )
            ]

        try:
            return await handler(client_id, message)
        except Exception as exc:
            logger.exception(
                "Handler error for type=%s client=%s: %s",
                message.type,
                client_id,
                exc,
            )
            return [self._error("HANDLER_ERROR", "Internal error processing request")]

    # ── Handlers ───────────────────────────────────────────────────────────────

    async def _handle_mode_select(
        self, client_id: str, message: ClientMessage
    ) -> list[ServerMessage]:
        """Update connection metadata and acknowledge with a status message."""
        payload = ModeSelectPayload(**message.payload)

        self._cm.update_mode(client_id, payload.mode)
        self._cm.update_depth_dial(client_id, payload.depthDial)
        self._cm.update_language(client_id, payload.language)

        logger.info(
            "Client %s → mode=%s depth=%s lang=%s",
            client_id,
            payload.mode.value,
            payload.depthDial.value,
            payload.language,
        )

        return [
            ServerMessage(
                type="status",
                payload=StatusPayload(
                    activeMode=payload.mode.value,
                    componentsStatus=ComponentsStatus(),
                ).model_dump(),
            )
        ]

    async def _handle_camera_frame(
        self, client_id: str, message: ClientMessage
    ) -> list[ServerMessage]:
        """Process a camera frame captured by the mobile client.

        In SightMode/LoreMode this triggers location recognition and
        documentary generation.  The heavy lifting is delegated to the
        Orchestrator (Task 12) via Pub/Sub; responses arrive asynchronously.
        """
        payload = CameraFramePayload(**message.payload)
        logger.debug(
            "Camera frame from client %s ts=%d gps=%s",
            client_id,
            payload.timestamp,
            payload.gpsLocation,
        )

        # TODO(Task-12): forward to Orchestrator.process_sight_input(client_id, payload)
        # The Orchestrator will push documentary_content back via connection_manager.send()

        return []  # Async response — Orchestrator pushes content when ready

    async def _handle_voice_input(
        self, client_id: str, message: ClientMessage
    ) -> list[ServerMessage]:
        """Forward voice audio to the Narration Engine / Orchestrator.

        In VoiceMode the audio is transcribed by Gemini Live API and the
        resulting topic triggers documentary generation (Task 9/12).
        """
        payload = VoiceInputPayload(**message.payload)
        logger.debug(
            "Voice input from client %s sampleRate=%d ts=%d",
            client_id,
            payload.sampleRate,
            payload.timestamp,
        )

        # TODO(Task-9/12): forward to Orchestrator.process_voice_input(client_id, payload)

        return []

    async def _handle_gps_update(
        self, client_id: str, message: ClientMessage
    ) -> list[ServerMessage]:
        """Pass GPS coordinates to the GPS Walker service.

        Proximity checks and auto-triggering of documentaries are handled
        asynchronously by GPSWalkingTourManager (Task 29).
        """
        payload = GPSUpdatePayload(**message.payload)
        logger.debug(
            "GPS update from client %s: lat=%.4f lon=%.4f acc=%.1fm",
            client_id,
            payload.latitude,
            payload.longitude,
            payload.accuracy,
        )

        # TODO(Task-29): forward to GPSWalkingTourManager.on_location_update(client_id, payload)

        return []

    async def _handle_barge_in(
        self, client_id: str, message: ClientMessage
    ) -> list[ServerMessage]:
        """Handle a user interruption during active documentary playback.

        The barge-in must be acknowledged within 200 ms (Requirement 19.2).
        We send a synchronous acknowledgement immediately; the Barge-In
        Handler (Task 32) processes the audio and sends a follow-up.
        """
        payload = BargeInPayload(**message.payload)
        logger.info(
            "Barge-in from client %s at stream pos=%.2fs",
            client_id,
            payload.streamPosition,
        )

        # Immediate acknowledgement to guarantee < 200 ms response (Req 19.2)
        ack = ServerMessage(
            type="status",
            payload={
                "event": "barge_in_acknowledged",
                "streamPosition": payload.streamPosition,
                "timestamp": int(time.time() * 1000),
            },
        )

        # TODO(Task-32): forward to BargeInHandler.process(client_id, payload)

        return [ack]

    async def _handle_query(
        self, client_id: str, message: ClientMessage
    ) -> list[ServerMessage]:
        """Handle a cross-session query (e.g. "What did I learn about Rome last week?").

        Delegated to SessionMemoryManager (Task 3).
        """
        payload = QueryPayload(**message.payload)
        logger.info(
            "Cross-session query from client %s: %r",
            client_id,
            payload.query[:100],
        )

        # TODO(Task-3): forward to SessionMemoryManager.query_across_sessions(...)

        return []

    async def _handle_branch_request(
        self, client_id: str, message: ClientMessage
    ) -> list[ServerMessage]:
        """Create a branch documentary from the current context.

        Delegated to BranchDocumentaryManager (Task 23).
        """
        payload = BranchRequestPayload(**message.payload)
        logger.info(
            "Branch request from client %s: topic=%r parent=%s",
            client_id,
            payload.topic,
            payload.parentBranchId,
        )

        # TODO(Task-23): forward to BranchDocumentaryManager.create_branch(client_id, payload)

        return []

    async def _handle_depth_dial_change(
        self, client_id: str, message: ClientMessage
    ) -> list[ServerMessage]:
        """Adjust content complexity for subsequent documentary segments."""
        payload = DepthDialChangePayload(**message.payload)
        self._cm.update_depth_dial(client_id, payload.newLevel)
        logger.info(
            "Client %s depth dial → %s",
            client_id,
            payload.newLevel.value,
        )

        # TODO(Task-12): notify Orchestrator to adapt future content generation

        return []

    async def _handle_chronicle_export(
        self, client_id: str, message: ClientMessage
    ) -> list[ServerMessage]:
        """Trigger Chronicle PDF export for a completed session.

        Delegated to ChronicleExporter (Task 33).
        """
        payload = ChronicleExportPayload(**message.payload)
        logger.info(
            "Chronicle export requested by client %s for session %s",
            client_id,
            payload.sessionId,
        )

        # TODO(Task-33): forward to ChronicleExporter.export(payload.sessionId, client_id)
        # When done, push a chronicle_ready message via connection_manager.send()

        return []

    # ── Utilities ──────────────────────────────────────────────────────────────

    @staticmethod
    def _error(code: str, detail: str, degraded: list[str] | None = None) -> ServerMessage:
        return ServerMessage(
            type="error",
            payload=ErrorPayload(
                errorCode=code,
                message=detail,
                degradedFunctionality=degraded or [],
            ).model_dump(),
        )
