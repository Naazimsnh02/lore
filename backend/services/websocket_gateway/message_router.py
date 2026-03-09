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
    CharacterInteractionPayload,
    ChronicleExportPayload,
    ClientMessage,
    ComponentsStatus,
    DepthDialChangePayload,
    ErrorPayload,
    GPSUpdatePayload,
    ModeSelectPayload,
    ModeSwitchPayload,
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
        self._connection_manager = connection_manager  # Alias for clarity
        
        # Initialize BargeInHandler (Task 32)
        self._barge_in_handler = None
        try:
            from ..barge_in.handler import BargeInHandler
            self._barge_in_handler = BargeInHandler(
                on_pause_callback=self._on_playback_pause,
                on_resume_callback=self._on_playback_resume,
            )
            logger.info("BargeInHandler initialized successfully")
        except Exception as e:
            logger.warning("Failed to initialize BargeInHandler: %s", e)

        self._handlers = {
            "mode_select": self._handle_mode_select,
            "mode_switch": self._handle_mode_switch,
            "camera_frame": self._handle_camera_frame,
            "voice_input": self._handle_voice_input,
            "gps_update": self._handle_gps_update,
            "barge_in": self._handle_barge_in,
            "query": self._handle_query,
            "branch_request": self._handle_branch_request,
            "depth_dial_change": self._handle_depth_dial_change,
            "chronicle_export": self._handle_chronicle_export,
            "character_interaction": self._handle_character_interaction,
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

    async def _handle_mode_switch(
        self, client_id: str, message: ClientMessage
    ) -> list[ServerMessage]:
        """Switch mode during an active session with content preservation (Req 1.6, 1.7).

        Delegates to the ModeSwitchManager attached to the client connection.
        Updates connection metadata and returns a status message with the
        preserved content snapshot.
        """
        payload = ModeSwitchPayload(**message.payload)
        conn_info = self._cm.get_connection_info(client_id)

        current_mode = conn_info.mode if conn_info else None
        if current_mode is None:
            return [
                self._error(
                    "NO_ACTIVE_MODE",
                    "No active mode set. Use mode_select first.",
                )
            ]

        if current_mode.value == payload.targetMode.value:
            return [
                ServerMessage(
                    type="status",
                    payload={
                        "event": "mode_switch_noop",
                        "currentMode": current_mode.value,
                        "message": f"Already in {current_mode.value} mode.",
                        "timestamp": int(time.time() * 1000),
                    },
                )
            ]

        mode_switch_manager = self._cm.get_mode_switch_manager(client_id)
        if mode_switch_manager is None:
            # Fallback: just update the mode without content preservation tracking
            self._cm.update_mode(client_id, payload.targetMode)
            logger.info(
                "Client %s mode switch %s → %s (no manager, fallback)",
                client_id,
                current_mode.value,
                payload.targetMode.value,
            )
            return [
                ServerMessage(
                    type="status",
                    payload={
                        "event": "mode_switched",
                        "fromMode": current_mode.value,
                        "toMode": payload.targetMode.value,
                        "preserved": {},
                        "timestamp": int(time.time() * 1000),
                    },
                )
            ]

        session_id = conn_info.session_id if conn_info else client_id
        user_id = conn_info.user_id if conn_info else ""

        try:
            from ..mode_switch.models import SwitchableMode

            result = await mode_switch_manager.switch_mode(
                session_id=session_id,
                user_id=user_id,
                from_mode=SwitchableMode(current_mode.value),
                to_mode=SwitchableMode(payload.targetMode.value),
                depth_dial=conn_info.depth_dial.value if conn_info else "explorer",
                language=conn_info.language if conn_info else "en",
            )

            # Update connection metadata to reflect the new mode
            self._cm.update_mode(client_id, payload.targetMode)

            return [
                ServerMessage(
                    type="status",
                    payload={
                        "event": "mode_switched",
                        "switchId": result.switch_id,
                        "fromMode": result.from_mode.value,
                        "toMode": result.to_mode.value,
                        "preserved": result.preserved.model_dump(),
                        "transitionMessage": result.transition_message,
                        "timestamp": int(time.time() * 1000),
                    },
                )
            ]
        except Exception as exc:
            logger.exception(
                "Mode switch failed for client %s: %s", client_id, exc
            )
            return [self._error("MODE_SWITCH_ERROR", f"Mode switch failed: {exc}")]

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

        # Forward to BargeInHandler for processing (Task 32)
        if self._barge_in_handler:
            from ..barge_in.models import Interruption
            
            interruption = Interruption(
                audio_data=payload.audioData,
                stream_position=payload.streamPosition,
                client_id=client_id,
                session_id=self._connection_manager.get_session_id(client_id) or "",
            )
            
            # Process asynchronously - result will be sent via callback
            import asyncio
            asyncio.create_task(self._process_barge_in_async(client_id, interruption))

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

        Delegates to BranchDocumentaryManager (Task 23).  The manager
        handles depth enforcement, stack management, session memory
        persistence, and optionally triggers content generation via
        the Orchestrator.  Documentary content is pushed asynchronously.
        """
        payload = BranchRequestPayload(**message.payload)
        logger.info(
            "Branch request from client %s: topic=%r parent=%s",
            client_id,
            payload.topic,
            payload.parentBranchId,
        )

        branch_manager = self._cm.get_branch_manager(client_id)
        if branch_manager is None:
            return [
                self._error(
                    "BRANCH_NOT_AVAILABLE",
                    "Branch documentary manager not initialised for this session",
                )
            ]

        try:
            result = await branch_manager.create_branch(
                payload.topic,
                stream_position=payload.timestamp / 1000.0,
            )
            return [
                ServerMessage(
                    type="status",
                    payload={
                        "event": "branch_created",
                        "branchId": result.branch_id,
                        "topic": result.context.topic,
                        "depth": result.context.depth,
                        "parentBranchId": result.context.parent_branch_id,
                        "timestamp": int(time.time() * 1000),
                    },
                )
            ]
        except Exception as exc:
            exc_name = type(exc).__name__
            if exc_name == "BranchDepthExceeded":
                return [
                    self._error(
                        "BRANCH_DEPTH_EXCEEDED",
                        str(exc),
                    )
                ]
            logger.exception(
                "Branch creation failed for client %s: %s", client_id, exc
            )
            return [self._error("BRANCH_ERROR", "Failed to create branch documentary")]

    async def _handle_depth_dial_change(
        self, client_id: str, message: ClientMessage
    ) -> list[ServerMessage]:
        """Adjust content complexity for subsequent documentary segments (Req 14.5, 14.6).

        Updates connection metadata and notifies the DepthDialManager so that
        all subsequent content generation uses the new complexity level.
        Already-generated content remains unchanged.
        """
        payload = DepthDialChangePayload(**message.payload)
        old_info = self._cm.get_connection_info(client_id)
        old_level = old_info.depth_dial.value if old_info else "unknown"

        self._cm.update_depth_dial(client_id, payload.newLevel)
        logger.info(
            "Client %s depth dial %s → %s",
            client_id,
            old_level,
            payload.newLevel.value,
        )

        # Notify DepthDialManager if available (Task 24)
        depth_dial_manager = self._cm.get_depth_dial_manager(client_id)
        session_id = old_info.session_id if old_info else None
        if depth_dial_manager and session_id:
            try:
                from ..depth_dial.models import DepthLevel as DDLevel

                await depth_dial_manager.change_depth_dial(
                    session_id=session_id,
                    new_level=DDLevel(payload.newLevel.value),
                )
            except Exception as exc:
                logger.warning("DepthDialManager update failed: %s", exc)

        return [
            ServerMessage(
                type="status",
                payload={
                    "event": "depth_dial_changed",
                    "newLevel": payload.newLevel.value,
                    "previousLevel": old_level,
                    "message": (
                        f"Depth dial changed to {payload.newLevel.value}. "
                        "Subsequent content will be adapted."
                    ),
                    "timestamp": int(time.time() * 1000),
                },
            )
        ]

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

    async def _handle_character_interaction(
        self, client_id: str, message: ClientMessage
    ) -> list[ServerMessage]:
        """Handle historical character encounter interactions (Req 12.1–12.6).

        Actions:
          - "accept": Accept a character encounter offer and start conversation.
          - "message": Send a message to the active historical character.
          - "end": End the current character encounter.

        Delegates to HistoricalCharacterManager (Task 25).
        """
        payload = CharacterInteractionPayload(**message.payload)
        logger.info(
            "Character interaction from client %s: action=%s",
            client_id,
            payload.action,
        )

        character_manager = self._cm.get_historical_character_manager(client_id)
        if character_manager is None:
            return [
                self._error(
                    "CHARACTER_NOT_AVAILABLE",
                    "Historical character manager not initialised for this session",
                )
            ]

        conn_info = self._cm.get_connection_info(client_id)
        session_id = conn_info.session_id if conn_info else client_id

        if payload.action == "accept":
            # Accept the offered encounter — create persona
            offer = await character_manager.offer_character_encounter(
                location="", topic="",
            )
            if offer is None:
                return [self._error("NO_CHARACTER_AVAILABLE", "No character encounter available")]

            persona = await character_manager.create_character_persona(
                offer.character, session_id=session_id,
            )
            return [
                ServerMessage(
                    type="status",
                    payload={
                        "event": "character_encounter_started",
                        "characterName": persona.character.name,
                        "historicalPeriod": persona.character.historical_period,
                        "aiDisclaimer": persona.ai_disclaimer,
                        "timestamp": int(time.time() * 1000),
                    },
                )
            ]

        elif payload.action == "message":
            persona = character_manager.get_active_persona(session_id)
            if persona is None:
                return [self._error("NO_ACTIVE_CHARACTER", "No active character encounter")]

            result = await character_manager.interact_with_character(
                persona, payload.message,
            )
            return [
                ServerMessage(
                    type="documentary_content",
                    payload={
                        "event": "character_response",
                        "characterName": result.character_name,
                        "responseText": result.response_text,
                        "accuracyVerified": result.accuracy_verified,
                        "aiDisclaimer": result.ai_generated_disclaimer,
                        "timestamp": int(time.time() * 1000),
                    },
                )
            ]

        elif payload.action == "end":
            ended = character_manager.end_encounter(session_id)
            return [
                ServerMessage(
                    type="status",
                    payload={
                        "event": "character_encounter_ended",
                        "ended": ended,
                        "timestamp": int(time.time() * 1000),
                    },
                )
            ]

        return [self._error("INVALID_ACTION", f"Unknown character action: {payload.action}")]

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

    # ── Barge-In Handler Integration (Task 32) ────────────────────────────────

    async def _process_barge_in_async(self, client_id: str, interruption: Any) -> None:
        """Process barge-in interruption asynchronously and send response.
        
        This method is called as a background task to process the interjection
        without blocking the acknowledgment response.
        """
        try:
            result = await self._barge_in_handler.process_interruption(interruption)
            
            if result.interjection_response:
                # Send interjection response to client
                response_msg = ServerMessage(
                    type="barge_in_response",
                    payload={
                        "type": result.interjection_response.type.value,
                        "transcription": result.interjection_response.transcription,
                        "resumeAction": result.interjection_response.resume_action.value,
                        "resumePosition": result.interjection_response.resume_position,
                        "confidence": result.interjection_response.confidence,
                        "branchTopic": result.interjection_response.branch_topic,
                        "content": result.interjection_response.content,
                        "processingTimeMs": result.interjection_response.processing_time_ms,
                    },
                )
                
                await self._cm.send_to_client(client_id, response_msg)
                
                logger.info(
                    "Barge-in processed for client %s: type=%s, action=%s",
                    client_id,
                    result.interjection_response.type.value,
                    result.interjection_response.resume_action.value,
                )
            
            if result.error:
                error_msg = ServerMessage(
                    type="error",
                    payload={
                        "errorCode": "BARGE_IN_ERROR",
                        "message": f"Error processing interruption: {result.error}",
                        "degradedFunctionality": [],
                    },
                )
                await self._cm.send_to_client(client_id, error_msg)
                
        except Exception as e:
            logger.error("Error in barge-in async processing: %s", e, exc_info=True)
            error_msg = ServerMessage(
                type="error",
                payload={
                    "errorCode": "BARGE_IN_ERROR",
                    "message": "Internal error processing interruption",
                    "degradedFunctionality": [],
                },
            )
            try:
                await self._cm.send_to_client(client_id, error_msg)
            except Exception:
                pass  # Client may have disconnected

    def _on_playback_pause(self, client_id: str, position: float) -> None:
        """Callback when playback is paused due to barge-in."""
        logger.debug("Playback paused for client %s at position %.2fs", client_id, position)
        # Additional pause handling can be added here (e.g., notify Orchestrator)

    def _on_playback_resume(self, client_id: str, position: float) -> None:
        """Callback when playback resumes after barge-in."""
        logger.debug("Playback resumed for client %s at position %.2fs", client_id, position)
        # Additional resume handling can be added here (e.g., notify Orchestrator)
