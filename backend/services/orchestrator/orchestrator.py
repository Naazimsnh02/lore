"""Documentary Orchestrator — ADK-based multi-agent coordinator for LORE.

Design reference: LORE design.md, Section 2 – Orchestrator.
Requirements:
  1.1  — Three operating modes (SightMode, VoiceMode, LoreMode)
  2.1  — SightMode camera → documentary
  3.1  — VoiceMode voice → documentary
  4.1  — LoreMode camera + voice fusion
  5.1  — Generate interleaved documentary content
  12.1 — Historical character encounters
  13.1 — Branch documentaries up to 3 levels deep
  15.1 — Alternate history scenarios
  21.1 — ADK-based orchestration with Gemini 3 Flash Preview
  21.2 — Parallel task decomposition
  21.3 — Result assembly and streaming
  21.5 — Retry failed tasks up to 3 times with exponential backoff

Architecture notes
------------------
The DocumentaryOrchestrator coordinates all generation services:

  - NarrationEngine  → script + streaming audio (Task 9)
  - NanoIllustrator   → illustrations (Task 10)
  - SearchGrounder    → fact verification (Task 11)
  - VeoGenerator     → cinematic video clips (Task 28)
  - SightModeHandler  → camera frame processing (Task 8)
  - VoiceModeHandler  → voice transcription + topic parsing (Task 15)
  - ConversationManager → intent classification + context (Task 16)
  - SessionMemoryManager → persistence (Task 3)
  - HistoricalCharacterManager → AI historical personas (Task 25)

Parallel execution: narration, illustration, and search verification run
concurrently via ``asyncio.gather`` (Req 21.2).  Each task is wrapped in
``_retry_task`` which implements exponential backoff (Req 21.5).

Mode workflows:
  - sight_mode_workflow: frame → location → parallel(narration, illustration, search)
  - voice_mode_workflow: audio/topic → transcribe → classify intent → route → parallel generation
  - lore_mode_workflow: frame + topic → fusion → parallel generation
  - branch_documentary_workflow: sub-topic → parallel generation (depth ≤ 3)
  - alternate_history_workflow: topic + context → speculative narration

All workflows return a ``DocumentaryStream`` assembled by ``StreamAssembler``.
Failures in individual services are caught and logged; the stream is assembled
from whatever succeeded (graceful degradation, Req 29.1).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any, AsyncIterator, Callable, Optional

from .models import (
    ContentElement,
    ContentElementType,
    DocumentaryRequest,
    DocumentaryStream,
    Mode,
    OrchestratorError,
    TaskFailure,
    WorkflowResult,
)
from .stream_assembler import StreamAssembler

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_RETRIES = 3
INITIAL_BACKOFF_S = 0.5  # First retry after 500 ms
MAX_BRANCH_DEPTH = 3


class DocumentaryOrchestrator:
    """Coordinates multi-agent documentary generation workflows.

    Parameters
    ----------
    narration_engine:
        NarrationEngine instance (Task 9).
    nano_illustrator:
        NanoIllustrator instance (Task 10).
    search_grounder:
        SearchGrounder instance (Task 11).
    sight_mode_handler:
        SightModeHandler instance (Task 8).
    voice_mode_handler:
        VoiceModeHandler instance (Task 15).
    conversation_manager:
        ConversationManager instance (Task 16).
    session_memory:
        SessionMemoryManager instance (Task 3).
    lore_mode_handler:
        LoreModeHandler instance (Task 21).  When provided, the
        ``lore_mode_workflow`` uses full multimodal fusion.  When absent,
        falls back to the basic topic fusion.
    depth_dial_manager:
        DepthDialManager instance (Task 24).  When provided, narration
        prompt instructions are enriched with depth-level guidance so
        generated content matches the requested complexity (Req 14.2–14.4).
    historical_character_manager:
        HistoricalCharacterManager instance (Task 25).  When provided,
        historical character encounters are offered during LoreMode
        workflows when the context has historical significance (Req 12.1).
    on_stream_element:
        Optional async callback invoked for each content element as it is
        produced.  Signature: ``async (session_id, element) -> None``.
        Used by the WebSocket Gateway to push elements to the client
        in real time.
    """

    def __init__(
        self,
        narration_engine: Any = None,
        nano_illustrator: Any = None,
        search_grounder: Any = None,
        sight_mode_handler: Any = None,
        voice_mode_handler: Any = None,
        conversation_manager: Any = None,
        session_memory: Any = None,
        lore_mode_handler: Any = None,
        alternate_history_engine: Any = None,
        branch_documentary_manager: Any = None,
        depth_dial_manager: Any = None,
        historical_character_manager: Any = None,
        mode_switch_manager: Any = None,
        veo_generator: Any = None,
        on_stream_element: Optional[
            Callable[[str, ContentElement], Any]
        ] = None,
    ) -> None:
        self._narration = narration_engine
        self._illustrator = nano_illustrator
        self._grounder = search_grounder
        self._sight_mode = sight_mode_handler
        self._voice_mode = voice_mode_handler
        self._conversation_manager = conversation_manager
        self._session_memory = session_memory
        self._lore_mode_handler = lore_mode_handler
        self._alternate_history = alternate_history_engine
        self._branch_manager = branch_documentary_manager
        self._depth_dial = depth_dial_manager
        self._historical_character = historical_character_manager
        self._mode_switch = mode_switch_manager
        self._veo = veo_generator
        self._on_stream_element = on_stream_element
        self._assembler = StreamAssembler()
        self._failures: list[TaskFailure] = []
        self._last_video_elements: list[ContentElement] = []

    # ── Public API ────────────────────────────────────────────────────────────

    async def process_request(
        self, request: DocumentaryRequest
    ) -> DocumentaryStream:
        """Main entry point — route to the appropriate mode workflow.

        Returns a fully assembled ``DocumentaryStream``.

        Performance target: first output within 3 seconds of invocation
        (Req 5.7 / Property 5).
        """
        self._failures = []
        logger.info(
            "Processing request %s mode=%s user=%s session=%s",
            request.request_id,
            request.mode.value,
            request.user_id,
            request.session_id,
        )

        start = time.time()

        try:
            if request.branch_topic:
                stream = await self.branch_documentary_workflow(request)
            elif request.mode == Mode.SIGHT:
                stream = await self.sight_mode_workflow(request)
            elif request.mode == Mode.VOICE:
                stream = await self.voice_mode_workflow(request)
            elif request.mode == Mode.LORE:
                stream = await self.lore_mode_workflow(request)
            else:
                raise OrchestratorError(f"Unknown mode: {request.mode}")
        except Exception as exc:
            logger.exception("Workflow failed for request %s", request.request_id)
            stream = DocumentaryStream(
                request_id=request.request_id,
                session_id=request.session_id,
                mode=request.mode,
                error=str(exc),
                completed_at=time.time(),
            )

        elapsed_ms = (time.time() - start) * 1000
        logger.info(
            "Request %s completed in %.0f ms — %d elements, %d failures",
            request.request_id,
            elapsed_ms,
            len(stream.elements),
            len(self._failures),
        )

        return stream

    # ── Mode workflows ────────────────────────────────────────────────────────

    async def sight_mode_workflow(
        self, request: DocumentaryRequest
    ) -> DocumentaryStream:
        """SightMode: camera frame → location → parallel content generation.

        Steps:
          1. Process camera frame through SightModeHandler (location recognition)
          2. Build narration context from recognised location
          3. Run narration, illustration, and search in parallel
          4. Assemble interleaved stream
        """
        # Step 1: Location recognition
        doc_context = await self._recognise_location(request)
        if doc_context is None:
            return self._empty_stream(request, error="Location recognition failed")

        place_name = doc_context.get("place_name", "Unknown Location")
        place_description = doc_context.get("place_description", "")
        place_types = doc_context.get("place_types", [])
        visual_description = doc_context.get("visual_description", "")
        latitude = doc_context.get("latitude", 0.0)
        longitude = doc_context.get("longitude", 0.0)

        # Step 2-3: Parallel content generation
        narration_elements, illustration_elements, fact_elements = (
            await self._parallel_generate(
                mode="sight",
                topic=place_name,
                place_name=place_name,
                place_description=place_description,
                place_types=place_types,
                visual_description=visual_description,
                latitude=latitude,
                longitude=longitude,
                depth_dial=request.depth_dial,
                language=request.language,
                session_id=request.session_id,
                user_id=request.user_id,
                previous_topics=request.previous_topics,
            )
        )

        # Step 4: Assemble stream (video elements added by _parallel_generate)
        return self._assembler.assemble(
            request_id=request.request_id,
            session_id=request.session_id,
            mode=Mode.SIGHT,
            narration_elements=narration_elements,
            illustration_elements=illustration_elements,
            fact_elements=fact_elements,
            video_elements=self._last_video_elements,
        )

    async def voice_mode_workflow(
        self, request: DocumentaryRequest
    ) -> DocumentaryStream:
        """VoiceMode: voice audio/topic → transcribe → classify → generate.

        Enhanced pipeline (Task 17):
          1. If ``voice_audio`` is present and VoiceModeHandler is available,
             transcribe the audio to obtain the topic.
          2. If ConversationManager is available, classify the user intent
             (new_topic / follow_up / branch / question / command).
          3. Route based on intent:
             - BRANCH  → delegate to ``branch_documentary_workflow``
             - COMMAND → return a command-acknowledgement stream
             - NEW_TOPIC / FOLLOW_UP / QUESTION → parallel content generation
          4. Record conversation turn for context awareness.

        Falls back to simple topic-based generation when VoiceModeHandler or
        ConversationManager are not configured (backward compatible).

        Requirements: 3.1, 3.2, 3.3, 5.1.
        """
        topic = request.voice_topic or ""
        language = request.language
        intent_info: Optional[Any] = None

        # ── Step 1: Transcribe raw audio if available ─────────────────────
        if request.voice_audio and self._voice_mode:
            try:
                from ..voice_mode.models import VoiceModeEvent

                voice_response = await self._voice_mode.process_voice_input(
                    audio_base64=request.voice_audio,
                    sample_rate=16000,
                    timestamp=request.timestamp,
                    session_id=request.session_id,
                    user_id=request.user_id,
                    previous_topics=request.previous_topics,
                )

                if voice_response.event == VoiceModeEvent.TOPIC_DETECTED:
                    topic = voice_response.topic or topic
                    language = voice_response.detected_language or language
                elif voice_response.event == VoiceModeEvent.SILENCE_DETECTED:
                    logger.info("Voice input was silence — using fallback topic")
                elif voice_response.event == VoiceModeEvent.ERROR:
                    logger.warning(
                        "Voice processing error: %s",
                        voice_response.payload.get("error", "unknown"),
                    )
                # For INPUT_BUFFERED, keep existing topic
            except Exception:
                logger.exception("VoiceModeHandler failed — falling back to request topic")

        # Use fallback topic if still empty
        if not topic:
            topic = "Unknown Topic"

        # ── Step 2: Classify intent via ConversationManager ───────────────
        if self._conversation_manager:
            try:
                from ..voice_mode.models import VoiceModeContext

                voice_ctx = VoiceModeContext(
                    topic=topic,
                    original_query=topic,
                    language=language,
                    session_id=request.session_id,
                    user_id=request.user_id,
                    previous_topics=request.previous_topics,
                )

                intent_info = await self._conversation_manager.handle_input(voice_ctx)
                logger.info(
                    "Intent classified: %s (confidence=%.2f) for topic '%s'",
                    intent_info.intent.value,
                    intent_info.confidence,
                    topic,
                )
            except Exception:
                logger.exception("ConversationManager failed — treating as new topic")

        # ── Step 3: Route based on intent ─────────────────────────────────
        if intent_info is not None:
            from ..voice_mode.models import ConversationIntent

            if intent_info.intent == ConversationIntent.BRANCH:
                branch_topic = intent_info.branch_topic or topic
                branch_request = request.model_copy(
                    update={
                        "branch_topic": branch_topic,
                        "previous_topics": request.previous_topics + [topic],
                    }
                )
                stream = await self.branch_documentary_workflow(branch_request)
                # Record assistant turn
                self._record_assistant_turn(stream, topic)
                return stream

            if intent_info.intent == ConversationIntent.COMMAND:
                command_text = self._handle_voice_command(topic)
                transition = self._assembler.create_transition_element(command_text)
                stream = self._assembler.assemble(
                    request_id=request.request_id,
                    session_id=request.session_id,
                    mode=Mode.VOICE,
                    transition_elements=[transition],
                )
                self._record_assistant_turn(stream, topic)
                return stream

            # For FOLLOW_UP, use context summary to enrich the topic
            if intent_info.intent == ConversationIntent.FOLLOW_UP:
                if self._conversation_manager:
                    context_summary = self._conversation_manager.get_context_summary()
                    if context_summary:
                        current_topic = self._conversation_manager.get_current_topic()
                        if current_topic and current_topic != topic:
                            topic = f"{topic} (in the context of {current_topic})"

        # ── Step 4: Parallel content generation ───────────────────────────
        narration_elements, illustration_elements, fact_elements = (
            await self._parallel_generate(
                mode="voice",
                topic=topic,
                depth_dial=request.depth_dial,
                language=language,
                session_id=request.session_id,
                user_id=request.user_id,
                previous_topics=request.previous_topics,
            )
        )

        stream = self._assembler.assemble(
            request_id=request.request_id,
            session_id=request.session_id,
            mode=Mode.VOICE,
            narration_elements=narration_elements,
            illustration_elements=illustration_elements,
            fact_elements=fact_elements,
            video_elements=self._last_video_elements,
        )

        # ── Step 5: Record conversation turn ──────────────────────────────
        self._record_assistant_turn(stream, topic)

        return stream

    async def lore_mode_workflow(
        self, request: DocumentaryRequest
    ) -> DocumentaryStream:
        """LoreMode: camera + voice fusion → parallel content generation.

        Enhanced with LoreModeHandler (Task 21):
          1. Process camera + voice concurrently via LoreModeHandler
          2. FusionEngine merges visual, verbal, and GPS contexts
          3. Detect alternate history requests and route accordingly
          4. Run parallel content generation from fused context
          5. Assemble interleaved stream

        Requirements: 4.1, 4.2, 4.3, 4.5, 4.6.
        """
        # Use LoreModeHandler if available for full fusion pipeline
        if self._lore_mode_handler:
            return await self._lore_mode_workflow_with_handler(request)

        # Fallback: basic fusion without dedicated handler
        return await self._lore_mode_workflow_basic(request)

    async def _lore_mode_workflow_with_handler(
        self, request: DocumentaryRequest
    ) -> DocumentaryStream:
        """LoreMode workflow using LoreModeHandler for full fusion."""
        from ..lore_mode.models import LoreModeEvent

        response = await self._lore_mode_handler.process_multimodal_input(
            camera_frame=request.camera_frame,
            voice_audio=request.voice_audio,
            voice_topic=request.voice_topic,
            gps_location=request.gps_location,
            timestamp=request.timestamp,
            session_id=request.session_id,
            user_id=request.user_id,
            language=request.language,
            previous_topics=request.previous_topics,
        )

        # Route alternate history requests
        if response.event == LoreModeEvent.ALTERNATE_HISTORY:
            alt_request = request.model_copy(
                update={
                    "voice_topic": response.payload.get("what_if_query", request.voice_topic),
                }
            )
            return await self.alternate_history_workflow(alt_request)

        # Handle errors / no context
        if response.event == LoreModeEvent.ERROR or response.fused_context is None:
            error_msg = response.payload.get("detail", "LoreMode fusion failed")
            return self._empty_stream(request, error=error_msg)

        ctx = response.fused_context

        # Offer historical character encounter if context warrants it (Req 12.1)
        character_offer = None
        if (
            self._historical_character
            and getattr(ctx, "enable_historical_characters", False)
        ):
            try:
                character_offer = await self._historical_character.offer_character_encounter(
                    location=ctx.place_name or "",
                    topic=ctx.fused_topic or "",
                    historical_period="",
                    historical_significance=0.8,  # FusionEngine already validated
                    place_types=ctx.place_types or [],
                )
                if character_offer:
                    logger.info(
                        "Historical character offered: %s (relevance=%.2f)",
                        character_offer.character.name,
                        character_offer.relevance_score,
                    )
                    # Push character offer to client via callback
                    if self._on_stream_element:
                        offer_element = ContentElement(
                            type=ContentElementType.TRANSITION,
                            transition_text=(
                                f"[HISTORICAL CHARACTER] {character_offer.prompt_text} "
                                f"{character_offer.ai_disclaimer}"
                            ),
                        )
                        try:
                            await self._on_stream_element(request.session_id, offer_element)
                        except Exception:
                            logger.warning("Failed to push character offer to client")
            except Exception:
                logger.warning(
                    "Historical character offer failed — continuing without encounter",
                    exc_info=True,
                )

        narration_elements, illustration_elements, fact_elements = (
            await self._parallel_generate(
                mode="lore",
                topic=ctx.fused_topic,
                place_name=ctx.place_name,
                place_description=ctx.place_description,
                place_types=ctx.place_types,
                visual_description=ctx.visual_description,
                latitude=ctx.latitude,
                longitude=ctx.longitude,
                depth_dial=request.depth_dial,
                language=ctx.language or request.language,
                session_id=request.session_id,
                user_id=request.user_id,
                previous_topics=request.previous_topics,
            )
        )

        return self._assembler.assemble(
            request_id=request.request_id,
            session_id=request.session_id,
            mode=Mode.LORE,
            narration_elements=narration_elements,
            illustration_elements=illustration_elements,
            fact_elements=fact_elements,
            video_elements=self._last_video_elements,
        )

    async def _lore_mode_workflow_basic(
        self, request: DocumentaryRequest
    ) -> DocumentaryStream:
        """Fallback LoreMode workflow without LoreModeHandler."""
        # Recognise location from camera
        doc_context = await self._recognise_location(request)
        place_name = ""
        place_description = ""
        place_types: list[str] = []
        visual_description = ""
        latitude = 0.0
        longitude = 0.0

        if doc_context:
            place_name = doc_context.get("place_name", "")
            place_description = doc_context.get("place_description", "")
            place_types = doc_context.get("place_types", [])
            visual_description = doc_context.get("visual_description", "")
            latitude = doc_context.get("latitude", 0.0)
            longitude = doc_context.get("longitude", 0.0)

        # Fuse voice topic with location context
        voice_topic = request.voice_topic or ""
        fused_topic = self._fuse_topic(voice_topic, place_name, visual_description)

        narration_elements, illustration_elements, fact_elements = (
            await self._parallel_generate(
                mode="lore",
                topic=fused_topic,
                place_name=place_name,
                place_description=place_description,
                place_types=place_types,
                visual_description=visual_description,
                latitude=latitude,
                longitude=longitude,
                depth_dial=request.depth_dial,
                language=request.language,
                session_id=request.session_id,
                user_id=request.user_id,
                previous_topics=request.previous_topics,
            )
        )

        return self._assembler.assemble(
            request_id=request.request_id,
            session_id=request.session_id,
            mode=Mode.LORE,
            narration_elements=narration_elements,
            illustration_elements=illustration_elements,
            fact_elements=fact_elements,
            video_elements=self._last_video_elements,
        )

    async def branch_documentary_workflow(
        self, request: DocumentaryRequest
    ) -> DocumentaryStream:
        """Branch documentary: sub-topic exploration up to 3 levels deep.

        Requirement 13.1 — nested sub-topics, max depth 3.

        When a BranchDocumentaryManager is available, delegates depth tracking
        and session memory persistence to it (Task 23).  Otherwise falls back
        to the original heuristic using ``previous_topics`` length.
        """
        branch_topic = request.branch_topic or request.voice_topic or "Unknown"

        # Depth tracking — prefer BranchDocumentaryManager when available
        current_depth = len(request.previous_topics)
        if self._branch_manager is not None:
            current_depth = self._branch_manager.current_depth

        if current_depth >= MAX_BRANCH_DEPTH:
            logger.warning(
                "Branch depth %d exceeds max %d — returning transition",
                current_depth,
                MAX_BRANCH_DEPTH,
            )
            transition = self._assembler.create_transition_element(
                f"Maximum exploration depth reached for '{branch_topic}'. "
                f"Returning to the main documentary."
            )
            return self._assembler.assemble(
                request_id=request.request_id,
                session_id=request.session_id,
                mode=request.mode,
                transition_elements=[transition],
            )

        # Persist branch node via manager if available (Req 13.6)
        if self._branch_manager is not None:
            try:
                from ..branch_documentary.models import BranchDepthExceeded

                await self._branch_manager.create_branch(
                    branch_topic,
                    mode=request.mode.value,
                    language=request.language,
                    depth_dial=request.depth_dial,
                )
            except BranchDepthExceeded:
                transition = self._assembler.create_transition_element(
                    f"Maximum exploration depth reached for '{branch_topic}'. "
                    f"Returning to the main documentary."
                )
                return self._assembler.assemble(
                    request_id=request.request_id,
                    session_id=request.session_id,
                    mode=request.mode,
                    transition_elements=[transition],
                )
            except Exception:
                logger.exception(
                    "BranchDocumentaryManager error for '%s' — continuing with generation",
                    branch_topic,
                )

        narration_elements, illustration_elements, fact_elements = (
            await self._parallel_generate(
                mode=request.mode.value,
                topic=branch_topic,
                depth_dial=request.depth_dial,
                language=request.language,
                session_id=request.session_id,
                user_id=request.user_id,
                previous_topics=request.previous_topics,
            )
        )

        return self._assembler.assemble(
            request_id=request.request_id,
            session_id=request.session_id,
            mode=request.mode,
            narration_elements=narration_elements,
            illustration_elements=illustration_elements,
            fact_elements=fact_elements,
            video_elements=self._last_video_elements,
        )

    async def alternate_history_workflow(
        self, request: DocumentaryRequest
    ) -> DocumentaryStream:
        """Alternate history: 'what if' scenarios grounded in real facts.

        Requirement 15.1 — speculative narration based on verified history.
        Requirement 15.2 — plausible alternative narratives.
        Requirement 15.3 — grounded in historical facts via SearchGrounder.
        Requirement 15.4 — clearly label content as speculative.
        Requirement 15.5 — explain causal reasoning.

        When an AlternateHistoryEngine is available, uses it for structured
        scenario extraction, historical grounding, causal reasoning, and
        speculative labeling.  Falls back to prompt-based generation otherwise.
        """
        topic = request.voice_topic or request.branch_topic or "Unknown"

        # Use AlternateHistoryEngine for structured generation when available
        custom_instructions: str
        if self._alternate_history:
            try:
                scenario = await self._alternate_history.generate_scenario(
                    question=topic,
                    session_id=request.session_id,
                    context_topic=", ".join(request.previous_topics[-3:])
                    if request.previous_topics
                    else "",
                )
                custom_instructions = (
                    self._alternate_history.build_narration_instructions(scenario)
                )
                alt_topic = (
                    f"Alternate history: {scenario.what_if_question.original_question}"
                )
            except Exception:
                logger.warning(
                    "AlternateHistoryEngine failed, falling back to basic generation",
                    exc_info=True,
                )
                alt_topic = f"Alternate history: What if {topic}?"
                custom_instructions = (
                    "Generate a speculative 'what if' alternate history scenario. "
                    "Ground the speculation in verified historical facts, then "
                    "explore a plausible alternative timeline. Clearly distinguish "
                    "between established facts and speculative elements."
                )
        else:
            alt_topic = f"Alternate history: What if {topic}?"
            custom_instructions = (
                "Generate a speculative 'what if' alternate history scenario. "
                "Ground the speculation in verified historical facts, then "
                "explore a plausible alternative timeline. Clearly distinguish "
                "between established facts and speculative elements."
            )

        narration_elements, illustration_elements, fact_elements = (
            await self._parallel_generate(
                mode="lore",
                topic=alt_topic,
                depth_dial=request.depth_dial,
                language=request.language,
                session_id=request.session_id,
                user_id=request.user_id,
                previous_topics=request.previous_topics,
                custom_instructions=custom_instructions,
            )
        )

        # Label narration elements as speculative (Req 15.4)
        for element in narration_elements:
            if element.narration_text and not element.narration_text.startswith(
                "[SPECULATIVE]"
            ):
                element.narration_text = (
                    f"[SPECULATIVE] {element.narration_text}"
                )

        return self._assembler.assemble(
            request_id=request.request_id,
            session_id=request.session_id,
            mode=Mode.LORE,
            narration_elements=narration_elements,
            illustration_elements=illustration_elements,
            fact_elements=fact_elements,
            video_elements=self._last_video_elements,
        )

    # ── Mode transition ──────────────────────────────────────────────────────

    def determine_mode(self, request: DocumentaryRequest) -> Mode:
        """Determine the operating mode from request inputs.

        If both camera and voice are present → LORE.
        Camera only → SIGHT.  Voice only → VOICE.
        Falls back to the explicit ``request.mode``.
        """
        has_camera = request.camera_frame is not None
        has_voice = request.voice_topic is not None or request.voice_audio is not None

        if has_camera and has_voice:
            return Mode.LORE
        if has_camera:
            return Mode.SIGHT
        if has_voice:
            return Mode.VOICE
        return request.mode

    def validate_mode_transition(self, from_mode: Mode, to_mode: Mode) -> bool:
        """Check whether a mode transition is valid.

        All transitions are valid in LORE's design.  This method exists as a
        hook for future constraints and for logging.
        """
        logger.info("Mode transition: %s → %s", from_mode.value, to_mode.value)
        return True

    async def switch_mode(
        self,
        *,
        session_id: str,
        user_id: str,
        from_mode: Mode,
        to_mode: Mode,
        depth_dial: str = "explorer",
        language: str = "en",
    ) -> DocumentaryStream:
        """Switch operating mode with content preservation (Req 1.6, 1.7).

        Delegates to the ModeSwitchManager if available, then returns
        a stream with a transition element confirming the switch.
        """
        if not self.validate_mode_transition(from_mode, to_mode):
            from .models import ModeTransitionError
            raise ModeTransitionError(
                f"Invalid transition: {from_mode.value} → {to_mode.value}"
            )

        transition_text = f"Switching from {from_mode.value} to {to_mode.value}"

        if self._mode_switch is not None:
            try:
                from ..mode_switch.models import SwitchableMode

                result = await self._mode_switch.switch_mode(
                    session_id=session_id,
                    user_id=user_id,
                    from_mode=SwitchableMode(from_mode.value),
                    to_mode=SwitchableMode(to_mode.value),
                    depth_dial=depth_dial,
                    language=language,
                )
                transition_text = result.transition_message or transition_text
            except Exception as exc:
                logger.warning("ModeSwitchManager failed: %s", exc)

        transition = ContentElement(
            type=ContentElementType.TRANSITION,
            transition_text=transition_text,
        )

        stream = DocumentaryStream(
            session_id=session_id,
            mode=to_mode,
            elements=[transition],
            completed_at=time.time(),
        )

        if self._on_stream_element:
            try:
                await self._on_stream_element(session_id, transition)
            except Exception:
                pass

        return stream

    # ── Parallel generation ──────────────────────────────────────────────────

    async def _parallel_generate(
        self,
        *,
        mode: str,
        topic: str,
        place_name: str = "",
        place_description: str = "",
        place_types: list[str] | None = None,
        visual_description: str = "",
        latitude: float = 0.0,
        longitude: float = 0.0,
        depth_dial: str = "explorer",
        language: str = "en",
        session_id: str = "",
        user_id: str = "",
        previous_topics: list[str] | None = None,
        custom_instructions: str | None = None,
    ) -> tuple[list[ContentElement], list[ContentElement], list[ContentElement]]:
        """Run narration, illustration, search, and video tasks in parallel.

        Returns (narration_elements, illustration_elements, fact_elements).
        Video elements are included in the assembled stream via the assembler.
        Each list may be empty if its service failed or is unavailable.
        """
        narration_task = self._retry_task(
            "narration",
            self._generate_narration,
            mode=mode,
            topic=topic,
            place_name=place_name,
            place_description=place_description,
            place_types=place_types or [],
            visual_description=visual_description,
            latitude=latitude,
            longitude=longitude,
            depth_dial=depth_dial,
            language=language,
            session_id=session_id,
            user_id=user_id,
            previous_topics=previous_topics or [],
            custom_instructions=custom_instructions,
        )

        illustration_task = self._retry_task(
            "illustration",
            self._generate_illustrations,
            topic=topic,
            place_name=place_name,
            place_types=place_types or [],
            session_id=session_id,
            mode=mode,
            language=language,
        )

        search_task = self._retry_task(
            "search",
            self._verify_facts,
            topic=topic,
            place_name=place_name,
            session_id=session_id,
            mode=mode,
        )

        # Video generation runs in parallel (Req 21.2, 6.1)
        video_task = self._retry_task(
            "video",
            self._generate_video,
            topic=topic,
            place_name=place_name,
            place_types=place_types or [],
            visual_description=visual_description,
            session_id=session_id,
            user_id=user_id,
            mode=mode,
            language=language,
        )

        results = await asyncio.gather(
            narration_task,
            illustration_task,
            search_task,
            video_task,
            return_exceptions=True,
        )

        narration_elements = self._extract_result(results[0], "narration")
        illustration_elements = self._extract_result(results[1], "illustration")
        fact_elements = self._extract_result(results[2], "search")
        video_elements = self._extract_result(results[3], "video")

        # Push elements to client in real-time if callback is set
        if self._on_stream_element:
            all_elements = (
                narration_elements + illustration_elements
                + fact_elements + video_elements
            )
            for elem in all_elements:
                try:
                    await self._on_stream_element(session_id, elem)
                except Exception:
                    logger.warning("Failed to push stream element to client")

        # Store video_elements on the instance so callers can pass them
        # to the assembler
        self._last_video_elements = video_elements

        return narration_elements, illustration_elements, fact_elements

    # ── Individual generation tasks ──────────────────────────────────────────

    async def _generate_narration(self, **kwargs: Any) -> list[ContentElement]:
        """Generate narration script and convert to content elements."""
        if not self._narration:
            logger.warning("NarrationEngine not configured — skipping narration")
            return []

        # Build NarrationContext — import here to avoid circular deps
        from ..narration_engine.models import DepthLevel, NarrationContext

        depth_map = {
            "explorer": DepthLevel.EXPLORER,
            "scholar": DepthLevel.SCHOLAR,
            "expert": DepthLevel.EXPERT,
        }

        # Enrich custom_instructions with depth-dial guidance (Req 14.2–14.4)
        custom_instructions = kwargs.get("custom_instructions") or ""
        depth_dial_str = kwargs.get("depth_dial", "explorer")
        if self._depth_dial:
            try:
                from ..depth_dial.models import DepthLevel as DDLevel

                dd_level = DDLevel(depth_dial_str)
                depth_instructions = self._depth_dial.build_narration_instructions(dd_level)
                if custom_instructions:
                    custom_instructions = f"{depth_instructions}\n\n{custom_instructions}"
                else:
                    custom_instructions = depth_instructions
            except Exception:
                logger.warning("Failed to build depth dial instructions — continuing without")

        context = NarrationContext(
            mode=kwargs.get("mode", "sight"),
            topic=kwargs.get("topic"),
            place_name=kwargs.get("place_name"),
            place_description=kwargs.get("place_description"),
            place_types=kwargs.get("place_types", []),
            visual_description=kwargs.get("visual_description"),
            latitude=kwargs.get("latitude", 0.0),
            longitude=kwargs.get("longitude", 0.0),
            language=kwargs.get("language", "en"),
            depth_level=depth_map.get(depth_dial_str, DepthLevel.EXPLORER),
            session_id=kwargs.get("session_id"),
            user_id=kwargs.get("user_id"),
            previous_topics=kwargs.get("previous_topics", []),
            custom_instructions=custom_instructions or None,
        )

        script = await self._narration.generate_script(context)

        elements: list[ContentElement] = []
        for segment in script.segments:
            elements.append(
                self._assembler.create_narration_element(
                    segment.text,
                    audio_duration=segment.duration,
                    emotional_tone=segment.tone.value if segment.tone else None,
                )
            )

        return elements

    async def _generate_illustrations(self, **kwargs: Any) -> list[ContentElement]:
        """Generate illustrations and convert to content elements."""
        if not self._illustrator:
            logger.warning("NanoIllustrator not configured — skipping illustrations")
            return []

        from ..nano_illustrator.models import ConceptDescription
        from ..nano_illustrator.models import DocumentaryContext as IllDocContext

        topic = kwargs.get("topic", "")
        place_name = kwargs.get("place_name", "")
        place_types = kwargs.get("place_types", [])

        doc_ctx = IllDocContext(
            session_id=kwargs.get("session_id", ""),
            mode=kwargs.get("mode", "sight"),
            topic=topic,
            place_name=place_name,
            place_types=place_types,
            language=kwargs.get("language", "en"),
        )

        # Generate 1-2 illustrations per documentary segment
        prompts = self._build_illustration_prompts(topic, place_name)
        concepts = [
            ConceptDescription(prompt=p, context=doc_ctx) for p in prompts
        ]

        result = await self._illustrator.generate_batch(concepts)

        elements: list[ContentElement] = []
        for ill_result in result:
            if ill_result.error:
                logger.warning("Illustration failed: %s", ill_result.error)
                continue
            ill = ill_result.illustration
            image_data_b64 = None
            if ill.image_data:
                image_data_b64 = base64.b64encode(ill.image_data).decode("ascii")
            elements.append(
                self._assembler.create_illustration_element(
                    image_url=ill.url,
                    image_data=image_data_b64,
                    caption=ill.caption or ill.concept_description,
                    visual_style=ill.style.value if ill.style else None,
                )
            )

        return elements

    async def _verify_facts(self, **kwargs: Any) -> list[ContentElement]:
        """Extract factual claims and verify them via SearchGrounder."""
        if not self._grounder:
            logger.warning("SearchGrounder not configured — skipping fact verification")
            return []

        from ..search_grounder.models import DocumentaryContext as SGDocContext
        from ..search_grounder.models import FactualClaim

        topic = kwargs.get("topic", "")
        place_name = kwargs.get("place_name", "")

        doc_ctx = SGDocContext(
            session_id=kwargs.get("session_id", ""),
            location_name=place_name or None,
            topic=topic or None,
            mode=kwargs.get("mode"),
        )

        # Build factual claims from the topic
        claims = self._extract_claims(topic, place_name)
        if not claims:
            return []

        factual_claims = [
            FactualClaim(text=c, context=doc_ctx) for c in claims
        ]

        results = await self._grounder.verify_batch(factual_claims)

        elements: list[ContentElement] = []
        for vr in results:
            sources = [
                {"url": s.url, "title": s.title, "authority": s.authority.value}
                for s in vr.sources
            ]
            elements.append(
                self._assembler.create_fact_element(
                    claim_text=vr.claim.text,
                    verified=vr.verified,
                    confidence=vr.confidence,
                    sources=sources,
                )
            )

        return elements

    async def _generate_video(self, **kwargs: Any) -> list[ContentElement]:
        """Generate video clips via VeoGenerator and convert to content elements.

        Requirements: 6.1 (Veo 3.1), 6.6 (graceful degradation).
        """
        if not self._veo:
            logger.debug("VeoGenerator not configured — skipping video generation")
            return []

        from ..veo_generator.models import DocumentaryContext as VeoDocContext
        from ..veo_generator.models import SceneDescription, VideoStyle

        topic = kwargs.get("topic", "")
        place_name = kwargs.get("place_name", "")
        visual_description = kwargs.get("visual_description", "")
        mode = kwargs.get("mode", "sight")

        # Determine style from mode
        style = VideoStyle.CINEMATIC
        if mode == "lore":
            style = VideoStyle.DOCUMENTARY

        # Build scene context
        doc_ctx = VeoDocContext(
            session_id=kwargs.get("session_id", ""),
            mode=mode,
            topic=topic,
            place_name=place_name,
            place_types=kwargs.get("place_types", []),
            language=kwargs.get("language", "en"),
        )

        # Build scene prompt
        prompt_parts = []
        if place_name:
            prompt_parts.append(f"Cinematic view of {place_name}.")
        if visual_description:
            prompt_parts.append(visual_description)
        if topic and topic != place_name:
            prompt_parts.append(f"Documentary about: {topic}.")
        if not prompt_parts:
            prompt_parts.append(f"Documentary scene about {topic or 'an interesting location'}.")

        scene = SceneDescription(
            prompt=" ".join(prompt_parts),
            duration=8,
            style=style,
            context=doc_ctx,
            generate_audio=True,
        )

        result = await self._veo.generate_clip(
            scene,
            user_id=kwargs.get("user_id"),
            session_id=kwargs.get("session_id"),
        )

        if result.error:
            logger.warning("Video generation failed: %s", result.error)
            return []

        elements: list[ContentElement] = []
        if result.clip:
            elements.append(
                self._assembler.create_video_element(
                    video_url=result.media_url or result.clip.url,
                    video_duration=result.clip.duration,
                    caption=topic or place_name,
                )
            )

        return elements

    # ── Location recognition ─────────────────────────────────────────────────

    async def _recognise_location(
        self, request: DocumentaryRequest
    ) -> Optional[dict[str, Any]]:
        """Process a camera frame through SightModeHandler.

        Returns a dict with location context or None on failure.
        """
        if not self._sight_mode:
            logger.warning("SightModeHandler not configured — skipping recognition")
            return None

        if not request.camera_frame:
            logger.warning("No camera frame in request — skipping recognition")
            return None

        try:
            gps = None
            if request.gps_location:
                gps = request.gps_location

            response = await self._sight_mode.process_frame(
                frame_base64=request.camera_frame,
                gps_location=gps,
                timestamp=request.timestamp,
            )

            # Check if a documentary was triggered
            from ..sight_mode.models import SightModeEvent

            if response.event == SightModeEvent.DOCUMENTARY_TRIGGER:
                payload = response.payload
                return {
                    "place_name": payload.get("place_name", ""),
                    "place_description": payload.get("place_description", ""),
                    "place_types": payload.get("place_types", []),
                    "visual_description": payload.get("visual_description", ""),
                    "latitude": payload.get("latitude", 0.0),
                    "longitude": payload.get("longitude", 0.0),
                    "place_id": payload.get("place_id", ""),
                    "confidence": payload.get("confidence", 0.0),
                }

            logger.info(
                "SightMode event=%s (not a documentary trigger)",
                response.event.value,
            )
            return None

        except Exception:
            logger.exception("Location recognition failed")
            self._failures.append(
                TaskFailure(task_name="location_recognition", error="Exception during recognition")
            )
            return None

    # ── Retry logic ──────────────────────────────────────────────────────────

    async def _retry_task(
        self,
        task_name: str,
        func: Callable[..., Any],
        **kwargs: Any,
    ) -> list[ContentElement]:
        """Execute a task with exponential backoff retries (Req 21.5).

        Retries up to MAX_RETRIES times with exponential backoff starting
        at INITIAL_BACKOFF_S.  On final failure, returns an empty list
        (graceful degradation).
        """
        last_error: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return await func(**kwargs)
            except Exception as exc:
                last_error = exc
                self._failures.append(
                    TaskFailure(
                        task_name=task_name,
                        attempt=attempt,
                        error=str(exc),
                    )
                )
                if attempt < MAX_RETRIES:
                    backoff = INITIAL_BACKOFF_S * (2 ** (attempt - 1))
                    logger.warning(
                        "Task '%s' attempt %d/%d failed: %s — retrying in %.1fs",
                        task_name,
                        attempt,
                        MAX_RETRIES,
                        exc,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                else:
                    logger.error(
                        "Task '%s' failed after %d attempts: %s",
                        task_name,
                        MAX_RETRIES,
                        exc,
                    )
        return []

    # ── Helper methods ───────────────────────────────────────────────────────

    def _extract_result(
        self, result: Any, task_name: str
    ) -> list[ContentElement]:
        """Safely extract a list of ContentElement from a gather result."""
        if isinstance(result, BaseException):
            logger.error("Task '%s' raised: %s", task_name, result)
            self._failures.append(
                TaskFailure(task_name=task_name, error=str(result))
            )
            return []
        if isinstance(result, list):
            return result
        return []

    def _fuse_topic(
        self, voice_topic: str, place_name: str, visual_description: str
    ) -> str:
        """Combine voice topic with location for LoreMode."""
        parts = []
        if voice_topic:
            parts.append(voice_topic)
        if place_name:
            parts.append(f"at {place_name}")
        if visual_description and not place_name:
            parts.append(f"(visual: {visual_description})")
        return " ".join(parts) if parts else "Unknown topic"

    def _build_illustration_prompts(
        self, topic: str, place_name: str
    ) -> list[str]:
        """Build illustration prompts from the documentary context.

        Generates 2 prompts: one establishing shot and one detail shot.
        """
        prompts: list[str] = []
        subject = place_name or topic

        if subject:
            prompts.append(
                f"A cinematic wide-angle establishing view of {subject}, "
                f"documentary style, rich detail, dramatic lighting"
            )
            prompts.append(
                f"A detailed close-up illustration of a notable feature of "
                f"{subject}, documentary style, educational"
            )
        else:
            prompts.append("A documentary-style illustration of an interesting scene")

        return prompts

    def _extract_claims(self, topic: str, place_name: str) -> list[str]:
        """Extract factual claims from the topic for verification.

        Returns simple claims derived from the topic and place name.
        In production, claims would be extracted from the narration script.
        """
        claims: list[str] = []
        if place_name:
            claims.append(f"{place_name} is a notable landmark or location")
        if topic and topic != place_name:
            claims.append(topic)
        return claims

    def _handle_voice_command(self, command_text: str) -> str:
        """Map a voice command to a human-readable acknowledgement.

        Recognised commands include stop, pause, resume, mode/language/depth
        changes, branch exit, and export.  Returns a user-facing string.
        """
        lower = command_text.lower().strip()

        if "stop" in lower or "pause" in lower:
            return "Documentary paused. Say 'resume' to continue."
        if "resume" in lower:
            return "Resuming documentary."
        if "switch mode" in lower or "change mode" in lower:
            return "Mode switch requested. Please select a new mode."
        if "change language" in lower or "switch language" in lower:
            return "Language change requested. Please specify the desired language."
        if "change depth" in lower or "set depth" in lower:
            return "Depth level change requested. Choose Explorer, Scholar, or Expert."
        if "go back" in lower or "exit branch" in lower or "close branch" in lower or "return to" in lower:
            return "Returning to the previous topic."
        if "export" in lower or "save" in lower:
            return "Exporting your documentary chronicle."

        return f"Command acknowledged: {command_text}"

    def _record_assistant_turn(
        self, stream: DocumentaryStream, topic: str
    ) -> None:
        """Record the generated stream as an assistant turn in ConversationManager."""
        if not self._conversation_manager:
            return
        try:
            # Build a summary from narration elements
            narration_texts = [
                e.narration_text
                for e in stream.elements
                if e.type == ContentElementType.NARRATION and e.narration_text
            ]
            summary = " ".join(narration_texts) if narration_texts else f"Documentary about {topic}"
            self._conversation_manager.add_assistant_turn(summary, topic=topic)
        except Exception:
            logger.warning("Failed to record assistant turn in ConversationManager")

    def _empty_stream(
        self, request: DocumentaryRequest, error: str = ""
    ) -> DocumentaryStream:
        """Return an empty stream with an optional error message."""
        return DocumentaryStream(
            request_id=request.request_id,
            session_id=request.session_id,
            mode=request.mode,
            error=error,
            completed_at=time.time(),
        )

    @property
    def failures(self) -> list[TaskFailure]:
        """Return the list of task failures from the last request."""
        return list(self._failures)
