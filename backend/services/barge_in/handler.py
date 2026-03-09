"""Barge-In Handler for managing user interruptions during documentary playback.

This module implements the core barge-in functionality, allowing users to
naturally interrupt documentary narration with questions, topic changes, or
commands.

Design reference: LORE design.md, Section 9 (Barge-In Handler).
Requirements: 19.1-19.6 (Barge-In Handling).

Key Features:
- Pause playback within 200ms of speech detection (Req 19.2)
- Process interjections: questions, topic changes, commands (Req 19.3)
- Answer questions before resuming (Req 19.4)
- Handle topic changes via branch or redirect (Req 19.5)
- Resume from exact interruption point (Req 19.6)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any, Callable, Optional

from ..voice_mode.handler import VoiceModeHandler
from ..voice_mode.conversation_manager import ConversationManager
from ..voice_mode.models import ConversationIntent, VoiceModeContext, VoiceModeEvent
from .models import (
    BargeInResult,
    InterjectionResponse,
    InterjectionType,
    Interruption,
    PlaybackState,
    ResumeAction,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

MAX_ACKNOWLEDGMENT_TIME_MS = 200.0  # Requirement 19.2
DEFAULT_SAMPLE_RATE = 16000  # 16 kHz PCM audio


class BargeInHandler:
    """Manages user interruptions during documentary playback.
    
    The BargeInHandler coordinates with VoiceModeHandler for audio transcription
    and ConversationManager for intent classification, then determines the
    appropriate response and resume action.
    
    Attributes:
        voice_handler: VoiceModeHandler for audio transcription
        conversation_manager: ConversationManager for intent classification
        playback_states: Dict tracking playback state per client
    """

    def __init__(
        self,
        *,
        voice_handler: Optional[VoiceModeHandler] = None,
        conversation_manager: Optional[ConversationManager] = None,
        genai_client: Any = None,
        on_pause_callback: Optional[Callable[[str, float], None]] = None,
        on_resume_callback: Optional[Callable[[str, float], None]] = None,
    ) -> None:
        """Initialize the BargeInHandler.
        
        Args:
            voice_handler: Handler for voice transcription (optional, created if None)
            conversation_manager: Manager for intent classification (optional)
            genai_client: Google GenAI client for transcription
            on_pause_callback: Callback when playback is paused (client_id, position)
            on_resume_callback: Callback when playback resumes (client_id, position)
        """
        self._voice_handler = voice_handler or VoiceModeHandler(genai_client=genai_client)
        self._conversation_manager = conversation_manager
        self._genai_client = genai_client
        self._on_pause = on_pause_callback
        self._on_resume = on_resume_callback
        
        # Track playback state per client
        self._playback_states: dict[str, PlaybackState] = {}
        
        logger.info("BargeInHandler initialized")

    # ── Public API ───────────────────────────────────────────────────────────

    async def process_interruption(
        self,
        interruption: Interruption,
    ) -> BargeInResult:
        """Process a user interruption during documentary playback.
        
        This is the main entry point for barge-in handling. It:
        1. Acknowledges the interruption within 200ms (Req 19.2)
        2. Pauses playback immediately
        3. Transcribes the audio
        4. Classifies the interjection type
        5. Generates an appropriate response
        6. Determines resume action
        
        Args:
            interruption: The interruption event with audio data
            
        Returns:
            BargeInResult with acknowledgment timing and response details
            
        Requirements:
        - 19.1: Monitor for user voice input during documentary stream
        - 19.2: Pause within 200ms of speech detection
        - 19.3: Process interjection appropriately
        - 19.4: Answer questions before resuming
        - 19.5: Handle topic changes
        - 19.6: Resume from interruption point
        """
        start_time = time.monotonic()
        
        try:
            # 1. Immediate acknowledgment and pause (Req 19.2)
            await self._pause_playback(
                interruption.client_id,
                interruption.stream_position,
                interruption.session_id,
            )
            
            ack_time_ms = (time.monotonic() - start_time) * 1000.0
            
            if ack_time_ms > MAX_ACKNOWLEDGMENT_TIME_MS:
                logger.warning(
                    "Acknowledgment took %.2f ms (exceeds %d ms requirement)",
                    ack_time_ms,
                    MAX_ACKNOWLEDGMENT_TIME_MS,
                )
            
            # 2. Process the interjection asynchronously
            interjection_response = await self._process_interjection(
                interruption,
                start_time,
            )
            
            return BargeInResult(
                acknowledged=True,
                acknowledgment_time_ms=ack_time_ms,
                interjection_response=interjection_response,
            )
            
        except Exception as e:
            logger.error(
                "Error processing interruption from client %s: %s",
                interruption.client_id,
                e,
                exc_info=True,
            )
            ack_time_ms = (time.monotonic() - start_time) * 1000.0
            return BargeInResult(
                acknowledged=True,
                acknowledgment_time_ms=ack_time_ms,
                error=str(e),
            )

    async def resume_playback(
        self,
        client_id: str,
        from_position: Optional[float] = None,
    ) -> bool:
        """Resume documentary playback for a client.
        
        Args:
            client_id: Client to resume playback for
            from_position: Position to resume from (None = use stored position)
            
        Returns:
            True if playback was resumed, False if client not found or error
            
        Requirement 19.6: Resume from interruption point.
        """
        state = self._playback_states.get(client_id)
        if not state:
            logger.warning("No playback state found for client %s", client_id)
            return False
        
        resume_pos = from_position if from_position is not None else state.current_position
        
        state.is_playing = True
        state.current_position = resume_pos
        state.paused_at = None
        
        if self._on_resume:
            try:
                self._on_resume(client_id, resume_pos)
            except Exception as e:
                logger.error("Error in resume callback: %s", e)
        
        logger.info(
            "Resumed playback for client %s at position %.2fs",
            client_id,
            resume_pos,
        )
        return True

    def update_playback_position(
        self,
        client_id: str,
        position: float,
        session_id: str = "",
        mode: str = "voice",
    ) -> None:
        """Update the current playback position for a client.
        
        This should be called periodically by the documentary stream to
        track where playback is, enabling accurate resume from interruption.
        
        Args:
            client_id: Client ID
            position: Current position in seconds
            session_id: Session ID
            mode: Current mode (sight, voice, lore)
        """
        if client_id not in self._playback_states:
            self._playback_states[client_id] = PlaybackState(
                client_id=client_id,
                session_id=session_id,
                mode=mode,
            )
        
        state = self._playback_states[client_id]
        state.current_position = position
        state.session_id = session_id
        state.mode = mode

    def get_playback_state(self, client_id: str) -> Optional[PlaybackState]:
        """Get the current playback state for a client."""
        return self._playback_states.get(client_id)

    def is_paused(self, client_id: str) -> bool:
        """Check if playback is currently paused for a client."""
        state = self._playback_states.get(client_id)
        return state is not None and not state.is_playing

    # ── Internal Methods ─────────────────────────────────────────────────────

    async def _pause_playback(
        self,
        client_id: str,
        position: float,
        session_id: str,
    ) -> None:
        """Pause documentary playback immediately.
        
        Requirement 19.2: Pause within 200ms of speech detection.
        """
        if client_id not in self._playback_states:
            self._playback_states[client_id] = PlaybackState(
                client_id=client_id,
                session_id=session_id,
            )
        
        state = self._playback_states[client_id]
        state.is_playing = False
        state.current_position = position
        state.paused_at = time.time()
        
        if self._on_pause:
            try:
                self._on_pause(client_id, position)
            except Exception as e:
                logger.error("Error in pause callback: %s", e)
        
        logger.info(
            "Paused playback for client %s at position %.2fs",
            client_id,
            position,
        )

    async def _process_interjection(
        self,
        interruption: Interruption,
        start_time: float,
    ) -> InterjectionResponse:
        """Process the interjection audio and determine response.
        
        Steps:
        1. Transcribe audio using VoiceModeHandler
        2. Classify intent using ConversationManager
        3. Determine interjection type
        4. Generate response content
        5. Determine resume action
        
        Requirements:
        - 19.3: Process interjection appropriately
        - 19.4: Answer questions before resuming
        - 19.5: Handle topic changes
        """
        # 1. Transcribe audio
        audio_bytes = base64.b64decode(interruption.audio_data)
        
        voice_response = await self._voice_handler.process_voice_input(
            audio_bytes=audio_bytes,
            sample_rate=DEFAULT_SAMPLE_RATE,
            client_id=interruption.client_id,
        )
        
        if not voice_response or voice_response.event == VoiceModeEvent.SILENCE_DETECTED:
            # Silence or transcription failure
            return InterjectionResponse(
                type=InterjectionType.FOLLOW_UP,
                resume_action=ResumeAction.CONTINUE,
                resume_position=interruption.stream_position,
                transcription="",
                confidence=0.0,
                processing_time_ms=(time.monotonic() - start_time) * 1000.0,
            )
        
        # Extract context from payload
        context_dict = voice_response.payload.get("context", {})
        if not context_dict:
            # No context available
            return InterjectionResponse(
                type=InterjectionType.FOLLOW_UP,
                resume_action=ResumeAction.CONTINUE,
                resume_position=interruption.stream_position,
                transcription=voice_response.transcription.text if voice_response.transcription else "",
                confidence=0.0,
                processing_time_ms=(time.monotonic() - start_time) * 1000.0,
            )
        
        # Reconstruct VoiceModeContext from dict
        from ..voice_mode.models import VoiceModeContext
        context = VoiceModeContext(**context_dict)
        transcription = context.original_query or context.topic
        
        # 2. Classify intent
        interjection_type, resume_action, branch_topic, confidence = (
            await self._classify_interjection(context, interruption)
        )
        
        # 3. Generate response content (placeholder - actual response generation
        # would be handled by Orchestrator in production)
        content = self._generate_response_content(
            interjection_type,
            transcription,
            context,
        )
        
        processing_time_ms = (time.monotonic() - start_time) * 1000.0
        
        return InterjectionResponse(
            type=interjection_type,
            content=content,
            resume_action=resume_action,
            resume_position=interruption.stream_position,
            transcription=transcription,
            confidence=confidence,
            branch_topic=branch_topic,
            processing_time_ms=processing_time_ms,
        )

    async def _classify_interjection(
        self,
        context: VoiceModeContext,
        interruption: Interruption,
    ) -> tuple[InterjectionType, ResumeAction, Optional[str], float]:
        """Classify the interjection type and determine resume action.
        
        Uses ConversationManager if available, otherwise falls back to
        heuristic classification.
        
        Returns:
            Tuple of (interjection_type, resume_action, branch_topic, confidence)
        """
        # Use ConversationManager if available
        if self._conversation_manager:
            classification = await self._conversation_manager.handle_input(context)
            
            # Map ConversationIntent to InterjectionType
            intent_map = {
                ConversationIntent.QUESTION: InterjectionType.QUESTION,
                ConversationIntent.COMMAND: InterjectionType.COMMAND,
                ConversationIntent.BRANCH: InterjectionType.BRANCH_REQUEST,
                ConversationIntent.NEW_TOPIC: InterjectionType.TOPIC_CHANGE,
                ConversationIntent.FOLLOW_UP: InterjectionType.FOLLOW_UP,
            }
            
            interjection_type = intent_map.get(
                classification.intent,
                InterjectionType.FOLLOW_UP,
            )
            
            # Determine resume action based on intent
            if interjection_type == InterjectionType.QUESTION:
                resume_action = ResumeAction.CONTINUE  # Answer then continue (Req 19.4)
            elif interjection_type == InterjectionType.BRANCH_REQUEST:
                resume_action = ResumeAction.BRANCH  # Create branch (Req 19.5)
            elif interjection_type == InterjectionType.TOPIC_CHANGE:
                resume_action = ResumeAction.REDIRECT  # Redirect main stream (Req 19.5)
            elif interjection_type == InterjectionType.COMMAND:
                resume_action = self._parse_command_action(context.original_query or "")
            else:
                resume_action = ResumeAction.CONTINUE
            
            return (
                interjection_type,
                resume_action,
                classification.branch_topic,
                classification.confidence,
            )
        
        # Fallback: heuristic classification
        return self._heuristic_classification(context)

    def _heuristic_classification(
        self,
        context: VoiceModeContext,
    ) -> tuple[InterjectionType, ResumeAction, Optional[str], float]:
        """Fallback heuristic classification when ConversationManager unavailable."""
        text = (context.original_query or context.topic).lower().strip()
        
        # Command detection
        command_keywords = ["stop", "pause", "resume", "switch", "change", "go back"]
        if any(kw in text for kw in command_keywords):
            return (
                InterjectionType.COMMAND,
                self._parse_command_action(text),
                None,
                0.8,
            )
        
        # Question detection
        question_keywords = ["what", "who", "where", "when", "why", "how", "is it", "did"]
        if any(text.startswith(kw) for kw in question_keywords) or text.endswith("?"):
            return (
                InterjectionType.QUESTION,
                ResumeAction.CONTINUE,
                None,
                0.85,
            )
        
        # Branch detection
        branch_keywords = ["tell me more about", "what about", "let's explore", "dive into"]
        for kw in branch_keywords:
            if kw in text:
                branch_topic = text.split(kw, 1)[1].strip() if kw in text else None
                return (
                    InterjectionType.BRANCH_REQUEST,
                    ResumeAction.BRANCH,
                    branch_topic,
                    0.8,
                )
        
        # Default: follow-up
        return (
            InterjectionType.FOLLOW_UP,
            ResumeAction.CONTINUE,
            None,
            0.6,
        )

    @staticmethod
    def _parse_command_action(text: str) -> ResumeAction:
        """Parse command text to determine resume action."""
        lower = text.lower()
        if "pause" in lower or "stop" in lower:
            return ResumeAction.PAUSE
        elif "go back" in lower or "return" in lower:
            return ResumeAction.CONTINUE
        elif "restart" in lower:
            return ResumeAction.RESTART
        else:
            return ResumeAction.CONTINUE

    @staticmethod
    def _generate_response_content(
        interjection_type: InterjectionType,
        transcription: str,
        context: VoiceModeContext,
    ) -> dict[str, Any]:
        """Generate placeholder response content.
        
        In production, this would be handled by the Orchestrator which would
        generate actual documentary content in response to the interjection.
        """
        return {
            "type": interjection_type.value,
            "transcription": transcription,
            "topic": context.topic,
            "message": f"Acknowledged: {transcription}",
        }
