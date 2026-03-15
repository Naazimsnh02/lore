"""Message router — dispatches validated client messages to handlers.

Each handler corresponds to one ClientMessage.type.  Downstream services
(Orchestrator, SessionMemoryManager, GPSWalker, etc.) are not yet implemented;
handlers that need them contain clearly labelled ``TODO(Task-N)`` stubs.

The router is intentionally synchronous in its dispatch logic.  Heavy work
happens asynchronously inside each handler, keeping p99 routing latency well
under the 100 ms WebSocket target (Requirement 20.7).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import TYPE_CHECKING, Any, Optional

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
    VoiceChunkPayload,
    VoiceInputPayload,
    VoiceMicStopPayload,
    VoiceSessionEndPayload,
    VoiceSessionStartPayload,
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

        # ── Gemini client (shared across handlers) ─────────────────────────
        self._genai_client: Optional[Any] = None
        _use_vertex = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "false").lower() == "true"
        try:
            import google.genai as genai
            api_key = os.getenv("GEMINI_API_KEY")
            use_vertex = _use_vertex
            if use_vertex:
                self._genai_client = genai.Client(
                    vertexai=True,
                    project=os.getenv("GCP_PROJECT_ID"),
                    location=os.getenv("VERTEX_AI_LOCATION", "us-central1"),
                )
            elif api_key:
                self._genai_client = genai.Client(api_key=api_key)
            else:
                logger.warning("No Gemini credentials found — voice transcription disabled")
        except Exception as e:
            logger.warning("Failed to initialise Gemini client: %s", e)

        # ── VoiceModeHandler (Task 9) ──────────────────────────────────────
        self._voice_handler: Optional[Any] = None
        try:
            from ..voice_mode.handler import VoiceModeHandler
            self._voice_handler = VoiceModeHandler(genai_client=self._genai_client)
            logger.info("VoiceModeHandler initialized successfully")
        except Exception as e:
            logger.warning("Failed to initialize VoiceModeHandler: %s", e)

        # ── NarrationEngine (Task 9) ──────────────────────────────────────
        self._narration_engine: Optional[Any] = None
        if self._genai_client:
            try:
                from ..narration_engine.engine import NarrationEngine
                self._narration_engine = NarrationEngine(client=self._genai_client)
                logger.info("NarrationEngine initialized successfully")
            except Exception as e:
                logger.warning("Failed to initialize NarrationEngine: %s", e)

        # ── NanoIllustrator (Task 10) ─────────────────────────────────────
        self._nano_illustrator: Optional[Any] = None
        if self._genai_client:
            try:
                from ..nano_illustrator.illustrator import NanoIllustrator
                # Image generation models require location=global on Vertex AI
                _image_client = self._genai_client
                if _use_vertex:
                    _image_client = genai.Client(
                        vertexai=True,
                        project=os.getenv("GCP_PROJECT_ID"),
                        location="global",
                    )
                self._nano_illustrator = NanoIllustrator(client=_image_client)
                logger.info("NanoIllustrator initialized successfully")
            except Exception as e:
                logger.warning("Failed to initialize NanoIllustrator: %s", e)

        # ── SearchGrounder (Task 11) ──────────────────────────────────────
        self._search_grounder: Optional[Any] = None
        if self._genai_client:
            try:
                from ..search_grounder.grounder import SearchGrounder
                self._search_grounder = SearchGrounder(client=self._genai_client)
                logger.info("SearchGrounder initialized successfully")
            except Exception as e:
                logger.warning("Failed to initialize SearchGrounder: %s", e)

        # ── VeoGenerator (Task 26) ────────────────────────────────────────
        self._veo_generator: Optional[Any] = None
        if self._genai_client:
            try:
                from ..veo_generator.generator import VeoGenerator
                self._veo_generator = VeoGenerator(client=self._genai_client)
                logger.info("VeoGenerator initialized successfully")
            except Exception as e:
                logger.warning("Failed to initialize VeoGenerator: %s", e)

        # ── Orchestrator (Task 12) ─────────────────────────────────────────
        self._orchestrator: Optional[Any] = None
        try:
            from ..orchestrator.orchestrator import DocumentaryOrchestrator
            from ..voice_mode.conversation_manager import ConversationManager
            self._conversation_manager = ConversationManager(genai_client=self._genai_client)
            self._orchestrator = DocumentaryOrchestrator(
                narration_engine=self._narration_engine,
                nano_illustrator=self._nano_illustrator,
                search_grounder=self._search_grounder,
                veo_generator=self._veo_generator,
                voice_mode_handler=self._voice_handler,
                conversation_manager=self._conversation_manager,
                on_stream_element=self._on_stream_element,
            )
            logger.info("DocumentaryOrchestrator initialized successfully")
        except Exception as e:
            logger.warning("Failed to initialize DocumentaryOrchestrator: %s", e)
            self._orchestrator = None

        # ── BargeInHandler (Task 32) ───────────────────────────────────────
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

        # ── LiveSessionManager (Option B — persistent Live API sessions) ───
        self._live_session_manager: Optional[Any] = None
        if self._genai_client:
            try:
                from ..voice_mode.live_session_manager import LiveSessionManager
                self._live_session_manager = LiveSessionManager(
                    genai_client=self._genai_client
                )
                logger.info("LiveSessionManager initialized successfully")
            except Exception as e:
                logger.warning("Failed to initialize LiveSessionManager: %s", e)

        self._handlers = {
            "mode_select": self._handle_mode_select,
            "mode_switch": self._handle_mode_switch,
            "camera_frame": self._handle_camera_frame,
            "voice_input": self._handle_voice_input,
            "voice_session_start": self._handle_voice_session_start,
            "voice_chunk": self._handle_voice_chunk,
            "voice_mic_stop": self._handle_voice_mic_stop,
            "voice_session_end": self._handle_voice_session_end,
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
        """Transcribe voice audio and trigger documentary generation.

        Pipeline:
          1. Parse payload and pull connection metadata.
          2. Run VoiceModeHandler to transcribe + detect topic.
          3. Echo transcription back to the client immediately.
          4. Fire orchestrator.voice_mode_workflow() as a background task
             so documentary content streams back asynchronously.
        """
        payload = VoiceInputPayload(**message.payload)
        conn_info = self._cm.get_connection_info(client_id)
        session_id = (conn_info.session_id if conn_info and conn_info.session_id
                      else str(uuid.uuid4()))
        user_id = conn_info.user_id if conn_info else ""
        language = conn_info.language if conn_info else "en"
        depth_dial = conn_info.depth_dial.value if conn_info else "explorer"

        # Ensure the connection has a stable session_id
        if conn_info and not conn_info.session_id:
            self._cm.update_session(client_id, session_id)

        logger.debug(
            "Voice input from client %s sampleRate=%d ts=%d",
            client_id,
            payload.sampleRate,
            payload.timestamp,
        )

        if not self._voice_handler:
            return [self._error("VOICE_NOT_AVAILABLE", "Voice processing is not configured")]

        # Run transcription synchronously so we can echo it back right away
        from ..voice_mode.models import VoiceModeEvent
        try:
            voice_response = await self._voice_handler.process_voice_input(
                audio_base64=payload.audioData,
                sample_rate=payload.sampleRate,
                timestamp=payload.timestamp / 1000.0,
                session_id=session_id,
                user_id=user_id,
            )
        except Exception as exc:
            logger.exception("VoiceModeHandler raised: %s", exc)
            return [self._error("VOICE_PROCESSING_ERROR", str(exc))]

        # Silence / too-short / buffered — nothing to do yet
        if voice_response.event in (
            VoiceModeEvent.SILENCE_DETECTED,
            VoiceModeEvent.INPUT_BUFFERED,
        ):
            return []

        if voice_response.event == VoiceModeEvent.ERROR:
            err = voice_response.payload.get("error", "unknown")
            return [self._error("VOICE_PROCESSING_ERROR", err)]

        # TOPIC_DETECTED — echo transcription to client
        transcription_msg = ServerMessage(
            type="transcription",
            payload={
                "text": voice_response.transcription.text if voice_response.transcription else "",
                "topic": voice_response.topic or "",
                "language": voice_response.detected_language or language,
                "branchDepth": 0,
                "timestamp": int(time.time() * 1000),
            },
        )

        # Kick off documentary generation in the background ONLY when there is
        # no active Live API session for this client.  During a live session,
        # Gemini's native audio stream IS the narration — firing the orchestrator
        # here would start a second NarrationEngine TTS stream simultaneously,
        # causing the "double audio / overlapping voices" bug.
        live_session_active = (
            self._live_session_manager is not None
            and self._live_session_manager.has_session(client_id)
        )
        if self._orchestrator and not live_session_active:
            asyncio.create_task(
                self._generate_documentary_async(
                    client_id=client_id,
                    session_id=session_id,
                    user_id=user_id,
                    voice_audio=payload.audioData,
                    voice_topic=voice_response.topic or "",
                    language=language,
                    depth_dial=depth_dial,
                ),
                name=f"voice-doc-{client_id[:8]}",
            )

        return [transcription_msg]

    async def _handle_voice_session_start(
        self, client_id: str, message: ClientMessage
    ) -> list[ServerMessage]:
        """Open a persistent Gemini Live API session for this client.

        Called when the user enters VoiceMode.  Mirrors AudioLoop.run() from
        the reference script — the session stays open until voice_session_end.
        """
        if not self._live_session_manager:
            return [self._error("VOICE_NOT_AVAILABLE", "Live session manager not configured")]

        payload = VoiceSessionStartPayload(**message.payload)
        conn_info = self._cm.get_connection_info(client_id)
        session_id = (conn_info.session_id if conn_info and conn_info.session_id
                      else str(uuid.uuid4()))
        user_id = conn_info.user_id if conn_info else ""
        language = payload.language or (conn_info.language if conn_info else "en")

        if conn_info and not conn_info.session_id:
            self._cm.update_session(client_id, session_id)

        # Build the on_transcript callback — called by LiveSession._receive_audio()
        # when input_transcription arrives from the Live API.
        async def on_transcript(transcript: str) -> None:
            await self._on_live_transcript(
                client_id=client_id,
                session_id=session_id,
                user_id=user_id,
                language=language,
                transcript=transcript,
            )

        # on_audio_chunk — model's spoken PCM bytes, forwarded chunk-by-chunk so
        # Flutter's AudioPlaybackService starts playing immediately (no accumulation).
        # None signals turn_complete — Flutter flushes its buffer into a WAV and plays.
        async def on_audio_chunk(pcm_bytes: Optional[bytes]) -> None:
            import base64 as _b64
            if pcm_bytes is None:
                # Turn complete — tell Flutter to flush its PCM buffer
                await self._cm.send_to_client(
                    client_id,
                    ServerMessage(
                        type="live_audio",
                        payload={
                            "data": "",
                            "final": True,
                            "sampleRate": 24000,
                            "timestamp": int(time.time() * 1000),
                        },
                    ),
                )
            else:
                # Regular chunk — stream immediately
                await self._cm.send_to_client(
                    client_id,
                    ServerMessage(
                        type="live_audio",
                        payload={
                            "data": _b64.b64encode(pcm_bytes).decode(),
                            "final": False,
                            "sampleRate": 24000,
                            "timestamp": int(time.time() * 1000),
                        },
                    ),
                )

        # on_output_transcript — text of what the model just spoke.
        # partial=True → word-by-word update so Flutter updates the text box in place.
        # partial=False → final consolidated text (sent at turn_complete).
        # Flutter uses the partial flag to update the last assistant bubble rather
        # than creating a new one for each word.
        async def on_output_transcript(text: str, partial: bool = False) -> None:
            await self._cm.send_to_client(
                client_id,
                ServerMessage(
                    type="transcription",
                    payload={
                        "text": text,
                        "role": "assistant",
                        "partial": partial,
                        "topic": "",
                        "language": language,
                        "branchDepth": 0,
                        "timestamp": int(time.time() * 1000),
                    },
                ),
            )

        # on_function_call — model called generate_illustration or generate_video.
        # Execute the actual generation and return a result dict so the model can
        # continue narrating with knowledge of what was generated.
        async def on_function_call(call_id: str, name: str, args: dict) -> dict:
            return await self._dispatch_live_function_call(
                client_id=client_id,
                session_id=session_id,
                call_id=call_id,
                name=name,
                args=args,
            )

        await self._live_session_manager.start_session(
            client_id=client_id,
            session_id=session_id,
            on_transcript=on_transcript,
            on_audio_chunk=on_audio_chunk,
            on_output_transcript=on_output_transcript,
            on_function_call=on_function_call,
            language=language,
        )

        logger.info("Live session started for client %s session %s", client_id, session_id)
        return [
            ServerMessage(
                type="status",
                payload={
                    "event": "voice_session_started",
                    "sessionId": session_id,
                    "timestamp": int(time.time() * 1000),
                },
            )
        ]

    async def _handle_voice_chunk(
        self, client_id: str, message: ClientMessage
    ) -> list[ServerMessage]:
        """Forward a raw PCM chunk into the persistent Live API session.

        Mirrors AudioLoop.listen_audio() → out_queue.put() from the reference
        script.  The chunk is base64-encoded on the wire; we decode it and
        pass raw bytes to LiveSessionManager.send_audio_chunk().

        Also runs a fast noise estimate so the VoiceModeHandler can classify
        noise level when the transcript arrives.
        """
        if not self._live_session_manager:
            return []

        payload = VoiceChunkPayload(**message.payload)
        try:
            import base64 as _b64
            pcm_bytes = _b64.b64decode(payload.data)
        except Exception as exc:
            logger.warning("Invalid base64 in voice_chunk from %s: %s", client_id, exc)
            return []

        # Update noise reading on the handler (non-blocking)
        if self._voice_handler:
            self._voice_handler.estimate_noise_from_chunk(pcm_bytes)

        await self._live_session_manager.send_audio_chunk(client_id, pcm_bytes)
        return []  # No synchronous response — transcript arrives asynchronously

    async def _handle_voice_mic_stop(
        self, client_id: str, message: ClientMessage
    ) -> list[ServerMessage]:
        """Signal end of mic input — sends audioStreamEnd to flush VAD.

        Mirrors the reference script's end_of_turn / audioStreamEnd pattern.
        The Live API VAD will fire and deliver input_transcription shortly after.
        """
        if not self._live_session_manager:
            return []

        await self._live_session_manager.signal_mic_stop(client_id)
        logger.debug("voice_mic_stop for client %s", client_id)
        return []

    async def _handle_voice_session_end(
        self, client_id: str, message: ClientMessage
    ) -> list[ServerMessage]:
        """Close the persistent Live API session when the user leaves VoiceMode."""
        if self._live_session_manager:
            await self._live_session_manager.end_session(client_id)
            logger.info("Live session ended for client %s", client_id)
        return [
            ServerMessage(
                type="status",
                payload={
                    "event": "voice_session_ended",
                    "timestamp": int(time.time() * 1000),
                },
            )
        ]

    async def _on_live_transcript(
        self,
        client_id: str,
        session_id: str,
        user_id: str,
        language: str,
        transcript: str,
    ) -> None:
        """Called by LiveSession._receive_audio() when input_transcription arrives.

        The Live model now handles narration, search grounding, and function calls
        directly — so this callback only needs to:
          1. Parse the topic via VoiceModeHandler (for branch depth tracking).
          2. Echo the user's transcription to Flutter as a 'user' role message.

        Documentary generation is no longer kicked off here — the Live model
        speaks the narration itself and calls generate_illustration/generate_video
        via function calls when it wants visuals.
        """
        if not self._voice_handler:
            # Fallback: echo raw transcript without topic parsing
            await self._cm.send_to_client(
                client_id,
                ServerMessage(
                    type="transcription",
                    payload={
                        "text": transcript,
                        "role": "user",
                        "topic": "",
                        "language": language,
                        "branchDepth": 0,
                        "timestamp": int(time.time() * 1000),
                    },
                ),
            )
            return

        from ..voice_mode.models import VoiceModeEvent
        conn_info = self._cm.get_connection_info(client_id)

        try:
            voice_response = await self._voice_handler.process_transcript(
                transcript=transcript,
                timestamp=time.time(),
                session_id=session_id,
                user_id=user_id,
            )
        except Exception as exc:
            logger.exception("process_transcript raised: %s", exc)
            return

        # Echo user's transcription to Flutter (role=user)
        await self._cm.send_to_client(
            client_id,
            ServerMessage(
                type="transcription",
                payload={
                    "text": (
                        voice_response.transcription.text
                        if voice_response.transcription else transcript
                    ),
                    "role": "user",
                    "topic": voice_response.topic or "",
                    "language": voice_response.detected_language or language,
                    "branchDepth": 0,
                    "timestamp": int(time.time() * 1000),
                },
            ),
        )

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

    # ── Voice documentary generation ───────────────────────────────────────────

    async def _dispatch_live_function_call(
        self,
        client_id: str,
        session_id: str,
        call_id: str,
        name: str,
        args: dict,
    ) -> dict:
        """Execute a function call requested by the Live API model.

        Called by LiveSession._handle_tool_call() when the model invokes
        generate_illustration or generate_video during narration.

        Runs the actual generation (NanoIllustrator / VeoGenerator), pushes
        the result to the Flutter client as a documentary_content message,
        and returns a compact result dict so the model knows what was generated.
        """
        conn_info = self._cm.get_connection_info(client_id)
        language = conn_info.language if conn_info else "en"

        if name == "generate_illustration":
            if not self._nano_illustrator:
                return {"status": "unavailable", "reason": "illustrator not configured"}
            try:
                from ..nano_illustrator.models import ConceptDescription, DocumentaryContext
                concept = ConceptDescription(
                    prompt=args.get("description", ""),
                    context=DocumentaryContext(
                        topic=args.get("topic", ""),
                        language=language,
                        session_id=session_id,
                    ),
                )
                result = await self._nano_illustrator.generate_illustration(concept)
                # Encode image bytes to base64 for JSON serialization.
                # Raw bytes cannot be embedded in JSON and cause a UTF-8 error.
                import base64 as _b64
                raw_bytes = result.illustration.image_data if result.illustration else None
                image_data_b64 = _b64.b64encode(raw_bytes).decode("utf-8") if raw_bytes else ""
                # Push to Flutter client immediately
                await self._cm.send_to_client(
                    client_id,
                    ServerMessage(
                        type="documentary_content",
                        payload={
                            "id": call_id,
                            "contentType": "illustration",
                            "sequenceId": 0,
                            "timestamp": int(time.time() * 1000),
                            "content": {
                                "imageData": image_data_b64,
                                "imageUrl": result.illustration.url if result.illustration else "",
                                "caption": args.get("caption", ""),
                                "visualStyle": result.illustration.style.value if result.illustration else "",
                            },
                        },
                    ),
                )
                return {"status": "ok", "caption": args.get("caption", "")}
            except Exception as exc:
                logger.warning("generate_illustration failed: %s", exc)
                return {"status": "error", "error": str(exc)}

        elif name == "generate_video":
            if not self._veo_generator:
                return {"status": "unavailable", "reason": "video generator not configured"}
            try:
                from ..veo_generator.models import SceneDescription, VideoStyle
                scene = SceneDescription(
                    prompt=args.get("description", ""),
                    style=VideoStyle.CINEMATIC,
                )
                result = await self._veo_generator.generate_clip(
                    scene=scene,
                    session_id=session_id,
                )
                await self._cm.send_to_client(
                    client_id,
                    ServerMessage(
                        type="documentary_content",
                        payload={
                            "id": call_id,
                            "contentType": "video",
                            "sequenceId": 0,
                            "timestamp": int(time.time() * 1000),
                            "content": {
                                "videoUrl": (result.clip.url or result.media_url or "") if result.clip else "",
                                "videoDuration": result.clip.duration if result.clip else 0,
                            },
                        },
                    ),
                )
                return {"status": "ok"}
            except Exception as exc:
                logger.warning("generate_video failed: %s", exc)
                return {"status": "error", "error": str(exc)}

        logger.warning("Unknown function call from Live API: %s", name)
        return {"status": "error", "error": f"unknown function: {name}"}

    async def _generate_documentary_async(
        self,
        client_id: str,
        session_id: str,
        user_id: str,
        voice_audio: str,
        voice_topic: str,
        language: str,
        depth_dial: str,
    ) -> None:
        """Run the orchestrator voice workflow and stream results to the client."""
        from ..orchestrator.models import DocumentaryRequest, Mode

        request = DocumentaryRequest(
            user_id=user_id,
            session_id=session_id,
            mode=Mode.VOICE,
            voice_audio=voice_audio,
            voice_topic=voice_topic,
            language=language,
            depth_dial=depth_dial,
        )

        try:
            stream = await self._orchestrator.voice_mode_workflow(request)
            for element in stream.elements:
                await self._cm.send_to_client(
                    client_id,
                    ServerMessage(
                        type="documentary_content",
                        payload=self._content_element_to_payload(element),
                    ),
                )
        except Exception as exc:
            logger.exception("Documentary generation failed for client %s: %s", client_id, exc)
            await self._cm.send_to_client(
                client_id,
                self._error("GENERATION_ERROR", f"Documentary generation failed: {exc}"),
            )

    @staticmethod
    def _content_element_to_payload(element: Any) -> dict:
        """Convert a ContentElement to the wire payload the Flutter client expects."""
        from ..orchestrator.models import ContentElementType

        base = {
            "id": element.id,
            "contentType": element.type.value,
            "sequenceId": element.sequence_id,
            "timestamp": int(element.timestamp * 1000),
        }

        if element.type == ContentElementType.NARRATION:
            base["content"] = {
                "text": element.narration_text or "",
                "audioData": element.audio_data or "",
                "audioDuration": element.audio_duration,
                "emotionalTone": element.emotional_tone or "",
            }
        elif element.type == ContentElementType.ILLUSTRATION:
            base["content"] = {
                "imageUrl": element.image_url or "",
                "imageData": element.image_data or "",
                "caption": element.caption or "",
                "visualStyle": element.visual_style or "",
            }
        elif element.type == ContentElementType.FACT:
            base["content"] = {
                "text": element.claim_text or "",
                "verified": element.verified,
                "confidence": element.confidence,
                "sources": element.sources,
            }
        elif element.type == ContentElementType.VIDEO:
            base["content"] = {
                "videoUrl": element.video_url or "",
                "videoDuration": element.video_duration,
            }
        elif element.type == ContentElementType.TRANSITION:
            base["content"] = {"text": element.transition_text or ""}
        else:
            base["content"] = {}

        return base

    def _on_stream_element(self, client_id: str, element: Any) -> None:
        """Callback for the orchestrator to push individual elements as they complete."""
        # Orchestrator calls this with (session_id, element) — we use session_id as client_id
        # when wired directly; for now schedule the send on the event loop.
        asyncio.create_task(
            self._cm.send_to_client(
                client_id,
                ServerMessage(
                    type="documentary_content",
                    payload=self._content_element_to_payload(element),
                ),
            )
        )

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
