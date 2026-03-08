"""Documentary Orchestrator — ADK-based multi-agent coordinator for LORE.

Design reference: LORE design.md, Section 2 – Orchestrator.
Requirements:
  1.1  — Three operating modes (SightMode, VoiceMode, LoreMode)
  2.1  — SightMode camera → documentary
  3.1  — VoiceMode voice → documentary
  4.1  — LoreMode camera + voice fusion
  5.1  — Generate interleaved documentary content
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
  - SightModeHandler  → camera frame processing (Task 8)
  - SessionMemoryManager → persistence (Task 3)

Parallel execution: narration, illustration, and search verification run
concurrently via ``asyncio.gather`` (Req 21.2).  Each task is wrapped in
``_retry_task`` which implements exponential backoff (Req 21.5).

Mode workflows:
  - sight_mode_workflow: frame → location → parallel(narration, illustration, search)
  - voice_mode_workflow: topic → parallel(narration, illustration, search)
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
    session_memory:
        SessionMemoryManager instance (Task 3).
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
        session_memory: Any = None,
        on_stream_element: Optional[
            Callable[[str, ContentElement], Any]
        ] = None,
    ) -> None:
        self._narration = narration_engine
        self._illustrator = nano_illustrator
        self._grounder = search_grounder
        self._sight_mode = sight_mode_handler
        self._session_memory = session_memory
        self._on_stream_element = on_stream_element
        self._assembler = StreamAssembler()
        self._failures: list[TaskFailure] = []

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

        # Step 4: Assemble stream
        return self._assembler.assemble(
            request_id=request.request_id,
            session_id=request.session_id,
            mode=Mode.SIGHT,
            narration_elements=narration_elements,
            illustration_elements=illustration_elements,
            fact_elements=fact_elements,
        )

    async def voice_mode_workflow(
        self, request: DocumentaryRequest
    ) -> DocumentaryStream:
        """VoiceMode: voice topic → parallel content generation.

        Steps:
          1. Use the transcribed topic from the request
          2. Run narration, illustration, and search in parallel
          3. Assemble interleaved stream
        """
        topic = request.voice_topic or "Unknown Topic"

        narration_elements, illustration_elements, fact_elements = (
            await self._parallel_generate(
                mode="voice",
                topic=topic,
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
            mode=Mode.VOICE,
            narration_elements=narration_elements,
            illustration_elements=illustration_elements,
            fact_elements=fact_elements,
        )

    async def lore_mode_workflow(
        self, request: DocumentaryRequest
    ) -> DocumentaryStream:
        """LoreMode: camera + voice fusion → parallel content generation.

        Combines location recognition from the camera with the voice topic
        to create a fused documentary context.
        """
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
        )

    async def branch_documentary_workflow(
        self, request: DocumentaryRequest
    ) -> DocumentaryStream:
        """Branch documentary: sub-topic exploration up to 3 levels deep.

        Requirement 13.1 — nested sub-topics, max depth 3.
        """
        branch_topic = request.branch_topic or request.voice_topic or "Unknown"
        # Depth tracking via previous_topics length (simple heuristic)
        current_depth = len(request.previous_topics)
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
        )

    async def alternate_history_workflow(
        self, request: DocumentaryRequest
    ) -> DocumentaryStream:
        """Alternate history: 'what if' scenarios grounded in real facts.

        Requirement 15.1 — speculative narration based on verified history.
        """
        topic = request.voice_topic or request.branch_topic or "Unknown"
        alt_topic = f"Alternate history: What if {topic}?"

        narration_elements, illustration_elements, fact_elements = (
            await self._parallel_generate(
                mode="lore",
                topic=alt_topic,
                depth_dial=request.depth_dial,
                language=request.language,
                session_id=request.session_id,
                user_id=request.user_id,
                previous_topics=request.previous_topics,
                custom_instructions=(
                    "Generate a speculative 'what if' alternate history scenario. "
                    "Ground the speculation in verified historical facts, then "
                    "explore a plausible alternative timeline. Clearly distinguish "
                    "between established facts and speculative elements."
                ),
            )
        )

        return self._assembler.assemble(
            request_id=request.request_id,
            session_id=request.session_id,
            mode=Mode.LORE,
            narration_elements=narration_elements,
            illustration_elements=illustration_elements,
            fact_elements=fact_elements,
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
        """Run narration, illustration, and search tasks in parallel.

        Returns (narration_elements, illustration_elements, fact_elements).
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

        results = await asyncio.gather(
            narration_task,
            illustration_task,
            search_task,
            return_exceptions=True,
        )

        narration_elements = self._extract_result(results[0], "narration")
        illustration_elements = self._extract_result(results[1], "illustration")
        fact_elements = self._extract_result(results[2], "search")

        # Push elements to client in real-time if callback is set
        if self._on_stream_element:
            all_elements = narration_elements + illustration_elements + fact_elements
            for elem in all_elements:
                try:
                    await self._on_stream_element(session_id, elem)
                except Exception:
                    logger.warning("Failed to push stream element to client")

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
            depth_level=depth_map.get(kwargs.get("depth_dial", "explorer"), DepthLevel.EXPLORER),
            session_id=kwargs.get("session_id"),
            user_id=kwargs.get("user_id"),
            previous_topics=kwargs.get("previous_topics", []),
            custom_instructions=kwargs.get("custom_instructions"),
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
