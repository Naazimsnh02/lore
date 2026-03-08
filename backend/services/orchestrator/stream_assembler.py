"""Stream Assembler — interleaves content elements into a documentary stream.

Design reference: LORE design.md, Section 2 – Orchestrator (stream assembly).
Requirements:
  5.1 — Generate interleaved documentary content
  5.3 — No gaps > 1 second between elements
  5.4 — Narration as continuous backbone
  5.5 — Insert illustrations at natural break points
  5.6 — Insert video clips during thematic transitions

Architecture notes
------------------
The StreamAssembler takes raw generation results (narration segments,
illustrations, verified facts) and weaves them into a correctly sequenced
DocumentaryStream.  The ordering strategy:

  1. Narration segments form the backbone (Req 5.4).
  2. Illustrations are inserted after relevant narration segments (Req 5.5).
  3. Verified facts are interleaved near the narration they support (Req 5.1).
  4. Video placeholders are inserted at thematic transitions (Req 5.6).
  5. Transition elements bridge topic changes.

Gap prevention: every element carries a ``sequence_id`` so the client can
reassemble correctly even if WebSocket messages arrive out of order.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from .models import ContentElement, ContentElementType, DocumentaryStream, Mode

logger = logging.getLogger(__name__)


class StreamAssembler:
    """Assembles content elements into an interleaved documentary stream.

    The assembler is stateless — call ``assemble()`` with the raw generation
    results and receive a fully sequenced ``DocumentaryStream``.
    """

    def assemble(
        self,
        *,
        request_id: str,
        session_id: str,
        mode: Mode,
        narration_elements: list[ContentElement] | None = None,
        illustration_elements: list[ContentElement] | None = None,
        fact_elements: list[ContentElement] | None = None,
        video_elements: list[ContentElement] | None = None,
        transition_elements: list[ContentElement] | None = None,
    ) -> DocumentaryStream:
        """Interleave content into a documentary stream.

        The assembly strategy places narration as the backbone, then
        intersperses illustrations and facts between narration segments.
        """
        narrations = narration_elements or []
        illustrations = illustration_elements or []
        facts = fact_elements or []
        videos = video_elements or []
        transitions = transition_elements or []

        elements: list[ContentElement] = []
        ill_idx = 0
        fact_idx = 0

        for i, narration in enumerate(narrations):
            elements.append(narration)

            # Insert an illustration after every narration segment if available
            if ill_idx < len(illustrations):
                elements.append(illustrations[ill_idx])
                ill_idx += 1

            # Insert a fact after every other narration segment if available
            if i % 2 == 1 and fact_idx < len(facts):
                elements.append(facts[fact_idx])
                fact_idx += 1

        # Append remaining illustrations
        while ill_idx < len(illustrations):
            elements.append(illustrations[ill_idx])
            ill_idx += 1

        # Append remaining facts
        while fact_idx < len(facts):
            elements.append(facts[fact_idx])
            fact_idx += 1

        # Video clips go at the end (they arrive asynchronously from Veo)
        elements.extend(videos)

        # Transitions at the very end
        elements.extend(transitions)

        # Assign sequential IDs
        for seq, elem in enumerate(elements):
            elem.sequence_id = seq

        stream = DocumentaryStream(
            request_id=request_id,
            session_id=session_id,
            mode=mode,
            elements=elements,
            completed_at=time.time(),
        )

        logger.info(
            "Assembled stream %s: %d elements (narration=%d ill=%d facts=%d video=%d)",
            stream.stream_id,
            len(elements),
            len(narrations),
            len(illustrations),
            len(facts),
            len(videos),
        )

        return stream

    def create_narration_element(
        self,
        text: str,
        *,
        audio_data: str | None = None,
        audio_duration: float = 0.0,
        emotional_tone: str | None = None,
    ) -> ContentElement:
        """Create a narration content element."""
        return ContentElement(
            type=ContentElementType.NARRATION,
            narration_text=text,
            audio_data=audio_data,
            audio_duration=audio_duration,
            emotional_tone=emotional_tone,
        )

    def create_illustration_element(
        self,
        *,
        image_url: str | None = None,
        image_data: str | None = None,
        caption: str = "",
        visual_style: str | None = None,
    ) -> ContentElement:
        """Create an illustration content element."""
        return ContentElement(
            type=ContentElementType.ILLUSTRATION,
            image_url=image_url,
            image_data=image_data,
            caption=caption,
            visual_style=visual_style,
        )

    def create_fact_element(
        self,
        claim_text: str,
        *,
        verified: bool = False,
        confidence: float = 0.0,
        sources: list[dict] | None = None,
    ) -> ContentElement:
        """Create a fact verification content element."""
        return ContentElement(
            type=ContentElementType.FACT,
            claim_text=claim_text,
            verified=verified,
            confidence=confidence,
            sources=sources or [],
        )

    def create_transition_element(self, text: str) -> ContentElement:
        """Create a transition content element."""
        return ContentElement(
            type=ContentElementType.TRANSITION,
            transition_text=text,
        )
