"""Stream Assembler — interleaves content elements into a documentary stream.

Design reference: LORE design.md, Section "Real-Time Streaming and Content Assembly".
Requirements:
  5.1 — Generate interleaved documentary content (narration, video, illustrations, facts)
  5.3 — No gaps > 1 second between consecutive elements
  5.4 — Narration as continuous backbone
  5.5 — Insert illustrations synchronized with relevant narration
  5.6 — Insert video clips at natural break points; buffer min 5 seconds

Architecture notes
------------------
The StreamAssembler takes raw generation results (narration segments,
illustrations, verified facts, videos) and weaves them into a correctly
sequenced DocumentaryStream.  The ordering strategy:

  1. Narration segments form the backbone (Req 5.4).
  2. Illustrations are inserted after relevant narration segments (Req 5.5).
  3. Verified facts are interleaved near the narration they support (Req 5.1).
  4. Videos are inserted at natural breaks (sentence-ending, longer segments) (Req 5.4).
  5. Transition elements bridge topic changes.

Gap prevention: every element carries a ``sequence_id`` and a
``timestamp`` so the client can reassemble correctly.  The
``ContentSynchronizer`` adjusts element timing to ensure no gap
exceeds 1 second (Req 5.3 / Property 4).

The ``StreamBuffer`` maintains a 5-second lookahead buffer so the
client always has content ready for playback (Req 5.6).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import AsyncIterator, Optional

from .models import ContentElement, ContentElementType, DocumentaryStream, Mode

logger = logging.getLogger(__name__)

# ── Default durations for non-timed elements ─────────────────────────────────

DEFAULT_ILLUSTRATION_DURATION = 3.0  # seconds to display an illustration
DEFAULT_FACT_DURATION = 2.0          # seconds to display a fact overlay
DEFAULT_TRANSITION_DURATION = 0.5    # seconds for a transition
MAX_GAP_SECONDS = 1.0                # Req 5.3 — max inter-element gap
BUFFER_CAPACITY_SECONDS = 5.0        # Req 5.6 — minimum buffer


# ── StreamBuffer ─────────────────────────────────────────────────────────────


class StreamBuffer:
    """Buffered queue for smooth documentary playback (Req 5.6).

    Maintains a 5-second content buffer so the client always has
    content ready.  Elements are added via ``add()`` and consumed
    via ``pop()``.  The ``ready()`` check tells whether enough
    content has been buffered to begin streaming.

    Parameters
    ----------
    capacity_seconds:
        Minimum buffered duration before streaming starts.
    """

    def __init__(self, capacity_seconds: float = BUFFER_CAPACITY_SECONDS) -> None:
        self.capacity_seconds = capacity_seconds
        self._buffer: deque[ContentElement] = deque()
        self._total_duration: float = 0.0

    # ── Public API ────────────────────────────────────────────────────────

    async def add(self, element: ContentElement) -> None:
        """Append an element to the buffer."""
        self._buffer.append(element)
        self._total_duration += get_element_duration(element)

    def ready(self) -> bool:
        """Return True when enough content has been buffered to stream."""
        return self._total_duration >= self.capacity_seconds

    async def pop(self) -> Optional[ContentElement]:
        """Remove and return the next element, or None if empty."""
        if not self._buffer:
            return None
        element = self._buffer.popleft()
        self._total_duration -= get_element_duration(element)
        return element

    def flush(self) -> list[ContentElement]:
        """Drain all remaining elements and return them as a list."""
        elements = list(self._buffer)
        self._buffer.clear()
        self._total_duration = 0.0
        return elements

    @property
    def size(self) -> int:
        return len(self._buffer)

    @property
    def total_duration(self) -> float:
        return self._total_duration

    @property
    def is_empty(self) -> bool:
        return len(self._buffer) == 0


# ── ContentSynchronizer ──────────────────────────────────────────────────────


class ContentSynchronizer:
    """Adjusts element timestamps to guarantee gap ≤ 1 second (Req 5.3).

    After the timeline is assembled, the synchronizer walks through
    elements and closes any gap that exceeds ``MAX_GAP_SECONDS`` by
    pulling later elements forward in time.

    It also verifies narration/illustration/video timing alignment
    so that illustrations appear during (or right after) the narration
    segment they relate to, and videos appear at natural breaks.
    """

    def synchronize(
        self, elements: list[ContentElement]
    ) -> list[ContentElement]:
        """Close gaps and re-stamp elements so no two consecutive
        elements are separated by more than MAX_GAP_SECONDS.

        The algorithm walks forward, computing each element's expected
        start time as the previous element's start + its duration.
        If the recorded timestamp drifts more than MAX_GAP_SECONDS
        ahead of expected start, the element is pulled forward.

        Returns the same list (mutated in-place for efficiency).
        """
        if len(elements) < 2:
            return elements

        # First element anchors the timeline
        cursor = elements[0].timestamp + get_element_duration(elements[0])

        for elem in elements[1:]:
            gap = elem.timestamp - cursor
            if gap > MAX_GAP_SECONDS:
                # Pull this element forward so gap == 0
                elem.timestamp = cursor
            elif gap < 0:
                # Element was stamped in the past — push to cursor
                elem.timestamp = cursor
            # Advance cursor
            cursor = elem.timestamp + get_element_duration(elem)

        return elements

    def verify_sync(self, elements: list[ContentElement]) -> list[float]:
        """Return a list of inter-element gaps for diagnostics.

        Gaps > MAX_GAP_SECONDS indicate a synchronization failure.
        """
        gaps: list[float] = []
        for i in range(1, len(elements)):
            prev_end = elements[i - 1].timestamp + get_element_duration(
                elements[i - 1]
            )
            gap = elements[i].timestamp - prev_end
            gaps.append(gap)
        return gaps

    def max_gap(self, elements: list[ContentElement]) -> float:
        """Return the largest inter-element gap, or 0.0 for ≤1 element."""
        gaps = self.verify_sync(elements)
        return max(gaps) if gaps else 0.0


# ── StreamAssembler ──────────────────────────────────────────────────────────


class StreamAssembler:
    """Assembles content elements into an interleaved documentary stream.

    Implements the full assembly pipeline from design.md:
      1. Build interleaved timeline (narration backbone + media inserts)
      2. Synchronize timestamps (gap prevention via ContentSynchronizer)
      3. Optionally buffer for smooth playback (StreamBuffer)

    The ``assemble()`` method is the synchronous batch interface used by
    the Orchestrator.  ``assemble_stream()`` is the async iterator
    interface for progressive real-time delivery.
    """

    def __init__(self) -> None:
        self._synchronizer = ContentSynchronizer()
        self._sequence_counter: int = 0

    # ── Batch assembly (used by Orchestrator) ─────────────────────────────

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
        """Interleave content into a documentary stream (batch mode).

        The assembly strategy places narration as the backbone, then
        intersperses illustrations, facts, and videos using relevance
        and natural-break heuristics.
        """
        narrations = narration_elements or []
        illustrations = illustration_elements or []
        facts = fact_elements or []
        videos = video_elements or []
        transitions = transition_elements or []

        # Step 1: Build interleaved timeline
        elements = self._build_timeline(
            narrations, illustrations, facts, videos, transitions
        )

        # Step 2: Assign sequential IDs
        self._sequence_counter = 0
        for elem in elements:
            elem.sequence_id = self._sequence_counter
            self._sequence_counter += 1

        # Step 3: Synchronize timestamps (gap prevention — Req 5.3)
        elements = self._synchronizer.synchronize(elements)

        stream = DocumentaryStream(
            request_id=request_id,
            session_id=session_id,
            mode=mode,
            elements=elements,
            completed_at=time.time(),
        )

        logger.info(
            "Assembled stream %s: %d elements "
            "(narration=%d ill=%d facts=%d video=%d trans=%d) "
            "max_gap=%.3fs",
            stream.stream_id,
            len(elements),
            len(narrations),
            len(illustrations),
            len(facts),
            len(videos),
            len(transitions),
            self._synchronizer.max_gap(elements),
        )

        return stream

    # ── Async streaming assembly ──────────────────────────────────────────

    async def assemble_stream(
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
    ) -> AsyncIterator[ContentElement]:
        """Assemble and yield elements progressively via StreamBuffer.

        This is the async interface for real-time WebSocket delivery.
        Elements are added to a StreamBuffer; once the buffer holds
        ≥ 5 seconds of content, elements are yielded one at a time.
        """
        stream = self.assemble(
            request_id=request_id,
            session_id=session_id,
            mode=mode,
            narration_elements=narration_elements,
            illustration_elements=illustration_elements,
            fact_elements=fact_elements,
            video_elements=video_elements,
            transition_elements=transition_elements,
        )

        buf = StreamBuffer()
        yielded = 0

        for element in stream.elements:
            await buf.add(element)

            # Once the buffer is ready, start yielding
            while buf.ready() or (
                element is stream.elements[-1] and not buf.is_empty
            ):
                popped = await buf.pop()
                if popped is None:
                    break
                yielded += 1
                yield popped

        # Flush any remaining buffered content
        for remaining in buf.flush():
            yielded += 1
            yield remaining

        logger.info(
            "Streamed %d elements for request %s", yielded, request_id
        )

    # ── Timeline construction ─────────────────────────────────────────────

    def _build_timeline(
        self,
        narrations: list[ContentElement],
        illustrations: list[ContentElement],
        facts: list[ContentElement],
        videos: list[ContentElement],
        transitions: list[ContentElement],
    ) -> list[ContentElement]:
        """Build an interleaved timeline with narration as backbone.

        Strategy (from design.md):
          1. Walk through narration segments sequentially
          2. After each narration, insert a relevant illustration (if any)
          3. After every other narration, insert a fact (if any)
          4. At natural breaks, insert a video clip (if any)
          5. Append remaining media and transitions
        """
        elements: list[ContentElement] = []
        ill_idx = 0
        fact_idx = 0
        video_idx = 0

        for i, narration in enumerate(narrations):
            elements.append(narration)

            # Insert an illustration if one is available and relevant
            if ill_idx < len(illustrations):
                if self._is_relevant(illustrations[ill_idx], narration):
                    elements.append(illustrations[ill_idx])
                    ill_idx += 1

            # Insert a fact after every other narration segment
            if i % 2 == 1 and fact_idx < len(facts):
                elements.append(facts[fact_idx])
                fact_idx += 1

            # Insert a video at natural breaks
            if video_idx < len(videos) and self._is_natural_break(narration):
                elements.append(videos[video_idx])
                video_idx += 1

        # Append remaining illustrations
        while ill_idx < len(illustrations):
            elements.append(illustrations[ill_idx])
            ill_idx += 1

        # Append remaining facts
        while fact_idx < len(facts):
            elements.append(facts[fact_idx])
            fact_idx += 1

        # Append remaining videos
        while video_idx < len(videos):
            elements.append(videos[video_idx])
            video_idx += 1

        # Transitions at the end
        elements.extend(transitions)

        return elements

    # ── Natural break and relevance heuristics ────────────────────────────

    def _is_natural_break(self, narration: ContentElement) -> bool:
        """Detect natural breaks for video insertion.

        A natural break occurs when a narration segment ends with a
        sentence-ending punctuation and is longer than 3 seconds,
        indicating a pause or topic shift (design.md heuristic).
        """
        text = narration.narration_text or ""
        duration = narration.audio_duration
        return (
            text.rstrip().endswith((".", "!", "?"))
            and duration > 3.0
        )

    def _is_relevant(
        self, illustration: ContentElement, narration: ContentElement
    ) -> bool:
        """Check if an illustration is relevant to the current narration.

        Uses keyword overlap as a lightweight relevance signal.  A more
        sophisticated implementation could use embedding similarity.
        """
        narr_text = (narration.narration_text or "").lower()
        ill_text = (illustration.caption or "").lower()

        if not narr_text or not ill_text:
            # If we can't determine relevance, default to inserting
            return True

        # Simple keyword overlap heuristic
        narr_words = set(narr_text.split())
        ill_words = set(ill_text.split())
        overlap = narr_words & ill_words
        # Consider relevant if any content word overlaps (skip short words)
        content_overlap = {w for w in overlap if len(w) > 3}
        return len(content_overlap) > 0 or len(narr_words) < 5

    def _fact_relates_to_segment(
        self, fact: ContentElement, narration: ContentElement
    ) -> bool:
        """Check if a fact citation relates to the current narration."""
        claim = (fact.claim_text or "").lower()
        narr_text = (narration.narration_text or "").lower()
        if not claim or not narr_text:
            return False
        return claim in narr_text or any(
            word in narr_text for word in claim.split() if len(word) > 4
        )

    # ── Element factory methods ───────────────────────────────────────────

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

    def create_video_element(
        self,
        *,
        video_url: str | None = None,
        video_duration: float = 0.0,
        caption: str = "",
    ) -> ContentElement:
        """Create a video content element."""
        return ContentElement(
            type=ContentElementType.VIDEO,
            video_url=video_url,
            video_duration=video_duration,
            caption=caption,
        )

    def create_transition_element(self, text: str) -> ContentElement:
        """Create a transition content element."""
        return ContentElement(
            type=ContentElementType.TRANSITION,
            transition_text=text,
        )


# ── Module-level helpers ─────────────────────────────────────────────────────


def get_element_duration(element: ContentElement) -> float:
    """Calculate the effective duration of a content element in seconds.

    Narration and video use their explicit duration fields.
    Illustrations, facts, and transitions use fixed defaults.
    """
    if element.type == ContentElementType.NARRATION:
        return max(element.audio_duration, 0.5)
    elif element.type == ContentElementType.VIDEO:
        return max(element.video_duration, 1.0)
    elif element.type == ContentElementType.ILLUSTRATION:
        return DEFAULT_ILLUSTRATION_DURATION
    elif element.type == ContentElementType.FACT:
        return DEFAULT_FACT_DURATION
    elif element.type == ContentElementType.TRANSITION:
        return DEFAULT_TRANSITION_DURATION
    return 1.0
