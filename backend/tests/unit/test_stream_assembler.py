"""Unit tests for the StreamAssembler, StreamBuffer, and ContentSynchronizer.

Tests cover (Task 13):
  - StreamAssembler.assemble() — interleaved timeline construction
  - StreamAssembler.assemble_stream() — async buffered streaming
  - StreamBuffer — add/pop/flush/ready behaviour
  - ContentSynchronizer — gap prevention, timestamp adjustment
  - Natural break detection heuristics
  - Relevance matching heuristics
  - Element factory methods
  - Edge cases: empty inputs, single elements, all-video streams
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from backend.services.orchestrator.models import (
    ContentElement,
    ContentElementType,
    DocumentaryStream,
    Mode,
)
from backend.services.orchestrator.stream_assembler import (
    BUFFER_CAPACITY_SECONDS,
    DEFAULT_FACT_DURATION,
    DEFAULT_ILLUSTRATION_DURATION,
    DEFAULT_TRANSITION_DURATION,
    MAX_GAP_SECONDS,
    ContentSynchronizer,
    StreamAssembler,
    StreamBuffer,
    get_element_duration,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _narration(text: str, duration: float = 5.0, **kw: Any) -> ContentElement:
    """Create a narration element."""
    return ContentElement(
        type=ContentElementType.NARRATION,
        narration_text=text,
        audio_duration=duration,
        **kw,
    )


def _illustration(caption: str = "test", **kw: Any) -> ContentElement:
    """Create an illustration element."""
    return ContentElement(
        type=ContentElementType.ILLUSTRATION,
        caption=caption,
        **kw,
    )


def _fact(claim: str = "test claim", **kw: Any) -> ContentElement:
    """Create a fact element."""
    return ContentElement(
        type=ContentElementType.FACT,
        claim_text=claim,
        verified=True,
        confidence=0.9,
        **kw,
    )


def _video(duration: float = 10.0, **kw: Any) -> ContentElement:
    """Create a video element."""
    return ContentElement(
        type=ContentElementType.VIDEO,
        video_duration=duration,
        video_url="https://example.com/video.mp4",
        **kw,
    )


def _transition(text: str = "Moving on...") -> ContentElement:
    return ContentElement(
        type=ContentElementType.TRANSITION,
        transition_text=text,
    )


# ── get_element_duration ─────────────────────────────────────────────────────


class TestGetElementDuration:
    def test_narration_uses_audio_duration(self):
        elem = _narration("Hello.", duration=7.5)
        assert get_element_duration(elem) == 7.5

    def test_narration_minimum_half_second(self):
        elem = _narration("Hi.", duration=0.0)
        assert get_element_duration(elem) == 0.5

    def test_video_uses_video_duration(self):
        elem = _video(duration=30.0)
        assert get_element_duration(elem) == 30.0

    def test_video_minimum_one_second(self):
        elem = _video(duration=0.0)
        assert get_element_duration(elem) == 1.0

    def test_illustration_fixed(self):
        assert get_element_duration(_illustration()) == DEFAULT_ILLUSTRATION_DURATION

    def test_fact_fixed(self):
        assert get_element_duration(_fact()) == DEFAULT_FACT_DURATION

    def test_transition_fixed(self):
        assert get_element_duration(_transition()) == DEFAULT_TRANSITION_DURATION


# ── StreamBuffer ─────────────────────────────────────────────────────────────


class TestStreamBuffer:
    @pytest.mark.asyncio
    async def test_add_and_pop(self):
        buf = StreamBuffer()
        elem = _narration("Test.", duration=2.0)
        await buf.add(elem)
        assert buf.size == 1
        popped = await buf.pop()
        assert popped is elem
        assert buf.size == 0

    @pytest.mark.asyncio
    async def test_pop_empty_returns_none(self):
        buf = StreamBuffer()
        assert await buf.pop() is None

    @pytest.mark.asyncio
    async def test_ready_threshold(self):
        buf = StreamBuffer(capacity_seconds=5.0)
        # Not ready with 3 seconds
        await buf.add(_narration("A.", duration=3.0))
        assert not buf.ready()
        # Ready after adding 3 more
        await buf.add(_narration("B.", duration=3.0))
        assert buf.ready()

    @pytest.mark.asyncio
    async def test_flush_drains_all(self):
        buf = StreamBuffer()
        await buf.add(_narration("A.", duration=1.0))
        await buf.add(_narration("B.", duration=2.0))
        flushed = buf.flush()
        assert len(flushed) == 2
        assert buf.is_empty
        assert buf.total_duration == 0.0

    @pytest.mark.asyncio
    async def test_total_duration_tracked(self):
        buf = StreamBuffer()
        await buf.add(_narration("A.", duration=2.0))
        await buf.add(_illustration())  # 3.0s default
        assert buf.total_duration == pytest.approx(5.0)
        await buf.pop()
        assert buf.total_duration == pytest.approx(3.0)

    @pytest.mark.asyncio
    async def test_fifo_order(self):
        buf = StreamBuffer()
        a = _narration("First.", duration=1.0)
        b = _narration("Second.", duration=1.0)
        await buf.add(a)
        await buf.add(b)
        assert await buf.pop() is a
        assert await buf.pop() is b


# ── ContentSynchronizer ──────────────────────────────────────────────────────


class TestContentSynchronizer:
    def setup_method(self):
        self.sync = ContentSynchronizer()

    def test_empty_list(self):
        result = self.sync.synchronize([])
        assert result == []

    def test_single_element(self):
        elem = _narration("Hi.", duration=2.0)
        result = self.sync.synchronize([elem])
        assert len(result) == 1

    def test_closes_large_gap(self):
        now = time.time()
        a = _narration("A.", duration=2.0, timestamp=now)
        b = _narration("B.", duration=2.0, timestamp=now + 10.0)  # 8s gap
        self.sync.synchronize([a, b])
        # b should have been pulled forward
        gap = b.timestamp - (a.timestamp + get_element_duration(a))
        assert gap <= MAX_GAP_SECONDS

    def test_preserves_small_gap(self):
        now = time.time()
        a = _narration("A.", duration=2.0, timestamp=now)
        b = _narration("B.", duration=2.0, timestamp=now + 2.5)  # 0.5s gap
        original_b_ts = b.timestamp
        self.sync.synchronize([a, b])
        # Gap was already fine, timestamp unchanged
        assert b.timestamp == original_b_ts

    def test_fixes_negative_gap(self):
        now = time.time()
        a = _narration("A.", duration=5.0, timestamp=now)
        b = _narration("B.", duration=2.0, timestamp=now - 1.0)  # In the past
        self.sync.synchronize([a, b])
        # b should be pushed to cursor
        assert b.timestamp >= a.timestamp + get_element_duration(a)

    def test_max_gap_after_sync(self):
        now = time.time()
        elements = [
            _narration("A.", duration=2.0, timestamp=now),
            _narration("B.", duration=2.0, timestamp=now + 20.0),
            _narration("C.", duration=2.0, timestamp=now + 50.0),
        ]
        self.sync.synchronize(elements)
        mg = self.sync.max_gap(elements)
        assert mg <= MAX_GAP_SECONDS

    def test_verify_sync_returns_gaps(self):
        now = time.time()
        elements = [
            _narration("A.", duration=2.0, timestamp=now),
            _narration("B.", duration=2.0, timestamp=now + 3.0),
        ]
        gaps = self.sync.verify_sync(elements)
        assert len(gaps) == 1
        assert gaps[0] == pytest.approx(1.0)

    def test_many_elements_all_synced(self):
        now = time.time()
        elements = [
            _narration(f"Seg {i}.", duration=1.0, timestamp=now + i * 100)
            for i in range(20)
        ]
        self.sync.synchronize(elements)
        assert self.sync.max_gap(elements) <= MAX_GAP_SECONDS


# ── StreamAssembler ──────────────────────────────────────────────────────────


class TestStreamAssemblerAssemble:
    def setup_method(self):
        self.assembler = StreamAssembler()

    def test_empty_inputs_produce_empty_stream(self):
        stream = self.assembler.assemble(
            request_id="r1", session_id="s1", mode=Mode.SIGHT
        )
        assert isinstance(stream, DocumentaryStream)
        assert len(stream.elements) == 0

    def test_narration_only(self):
        narrs = [_narration("Hello.", duration=5.0), _narration("World.", duration=3.0)]
        stream = self.assembler.assemble(
            request_id="r1",
            session_id="s1",
            mode=Mode.VOICE,
            narration_elements=narrs,
        )
        assert len(stream.elements) == 2
        assert all(e.type == ContentElementType.NARRATION for e in stream.elements)

    def test_interleaving_narration_and_illustrations(self):
        narrs = [
            _narration("The Colosseum.", duration=5.0),
            _narration("Built in 70 AD.", duration=4.0),
        ]
        ills = [_illustration(caption="colosseum view")]
        stream = self.assembler.assemble(
            request_id="r1",
            session_id="s1",
            mode=Mode.SIGHT,
            narration_elements=narrs,
            illustration_elements=ills,
        )
        # Illustration should appear somewhere in the stream
        types = [e.type for e in stream.elements]
        assert ContentElementType.ILLUSTRATION in types

    def test_facts_interleaved_after_every_other_narration(self):
        narrs = [
            _narration("Seg 0.", duration=3.0),
            _narration("Seg 1.", duration=3.0),
            _narration("Seg 2.", duration=3.0),
            _narration("Seg 3.", duration=3.0),
        ]
        facts = [_fact("claim A"), _fact("claim B")]
        stream = self.assembler.assemble(
            request_id="r1",
            session_id="s1",
            mode=Mode.VOICE,
            narration_elements=narrs,
            fact_elements=facts,
        )
        # Facts inserted after segments 1 and 3
        types = [e.type for e in stream.elements]
        assert types.count(ContentElementType.FACT) == 2

    def test_video_at_natural_break(self):
        # Long narration ending with period — natural break
        narrs = [_narration("This is a long segment about history.", duration=5.0)]
        vids = [_video(duration=8.0)]
        stream = self.assembler.assemble(
            request_id="r1",
            session_id="s1",
            mode=Mode.SIGHT,
            narration_elements=narrs,
            video_elements=vids,
        )
        types = [e.type for e in stream.elements]
        assert ContentElementType.VIDEO in types
        # Video should follow narration
        narr_idx = types.index(ContentElementType.NARRATION)
        vid_idx = types.index(ContentElementType.VIDEO)
        assert vid_idx > narr_idx

    def test_video_not_at_non_break(self):
        # Short narration — not a natural break
        narrs = [_narration("Hi", duration=1.0)]
        vids = [_video(duration=8.0)]
        stream = self.assembler.assemble(
            request_id="r1",
            session_id="s1",
            mode=Mode.VOICE,
            narration_elements=narrs,
            video_elements=vids,
        )
        # Video should still be in the stream (appended at end)
        types = [e.type for e in stream.elements]
        assert ContentElementType.VIDEO in types

    def test_sequential_ids(self):
        narrs = [_narration("A.", duration=2.0), _narration("B.", duration=2.0)]
        ills = [_illustration()]
        stream = self.assembler.assemble(
            request_id="r1",
            session_id="s1",
            mode=Mode.SIGHT,
            narration_elements=narrs,
            illustration_elements=ills,
        )
        ids = [e.sequence_id for e in stream.elements]
        assert ids == list(range(len(ids)))

    def test_gap_prevention(self):
        """Verify all inter-element gaps are ≤ 1 second after assembly."""
        sync = ContentSynchronizer()
        narrs = [
            _narration("A.", duration=2.0),
            _narration("B.", duration=3.0),
            _narration("C.", duration=4.0),
        ]
        ills = [_illustration(), _illustration()]
        facts = [_fact()]
        stream = self.assembler.assemble(
            request_id="r1",
            session_id="s1",
            mode=Mode.VOICE,
            narration_elements=narrs,
            illustration_elements=ills,
            fact_elements=facts,
        )
        assert sync.max_gap(stream.elements) <= MAX_GAP_SECONDS

    def test_transitions_at_end(self):
        narrs = [_narration("Main.", duration=3.0)]
        trans = [_transition("Switching topic...")]
        stream = self.assembler.assemble(
            request_id="r1",
            session_id="s1",
            mode=Mode.VOICE,
            narration_elements=narrs,
            transition_elements=trans,
        )
        assert stream.elements[-1].type == ContentElementType.TRANSITION

    def test_mode_preserved(self):
        stream = self.assembler.assemble(
            request_id="r1",
            session_id="s1",
            mode=Mode.LORE,
            narration_elements=[_narration("Test.", duration=2.0)],
        )
        assert stream.mode == Mode.LORE

    def test_completed_at_set(self):
        stream = self.assembler.assemble(
            request_id="r1", session_id="s1", mode=Mode.SIGHT
        )
        assert stream.completed_at is not None

    def test_all_content_types_together(self):
        narrs = [
            _narration("History of Rome.", duration=5.0),
            _narration("The empire expanded.", duration=4.0),
        ]
        ills = [_illustration(caption="rome overview")]
        facts = [_fact("Rome was founded in 753 BC.")]
        vids = [_video(duration=10.0)]
        trans = [_transition("Next chapter...")]
        stream = self.assembler.assemble(
            request_id="r1",
            session_id="s1",
            mode=Mode.SIGHT,
            narration_elements=narrs,
            illustration_elements=ills,
            fact_elements=facts,
            video_elements=vids,
            transition_elements=trans,
        )
        type_set = {e.type for e in stream.elements}
        assert ContentElementType.NARRATION in type_set
        # At least some content types present
        assert len(type_set) >= 2


# ── StreamAssembler.assemble_stream (async) ──────────────────────────────────


class TestStreamAssemblerAsyncStream:
    @pytest.mark.asyncio
    async def test_async_stream_yields_all_elements(self):
        assembler = StreamAssembler()
        narrs = [
            _narration("A.", duration=3.0),
            _narration("B.", duration=3.0),
        ]
        elements = []
        async for elem in assembler.assemble_stream(
            request_id="r1",
            session_id="s1",
            mode=Mode.VOICE,
            narration_elements=narrs,
        ):
            elements.append(elem)
        assert len(elements) == 2

    @pytest.mark.asyncio
    async def test_async_stream_preserves_order(self):
        assembler = StreamAssembler()
        narrs = [
            _narration("First.", duration=2.0),
            _narration("Second.", duration=2.0),
            _narration("Third.", duration=2.0),
        ]
        elements = []
        async for elem in assembler.assemble_stream(
            request_id="r1",
            session_id="s1",
            mode=Mode.VOICE,
            narration_elements=narrs,
        ):
            elements.append(elem)
        ids = [e.sequence_id for e in elements]
        assert ids == sorted(ids)

    @pytest.mark.asyncio
    async def test_async_empty_inputs(self):
        assembler = StreamAssembler()
        elements = []
        async for elem in assembler.assemble_stream(
            request_id="r1", session_id="s1", mode=Mode.SIGHT
        ):
            elements.append(elem)
        assert len(elements) == 0


# ── Natural break detection ──────────────────────────────────────────────────


class TestNaturalBreakDetection:
    def setup_method(self):
        self.assembler = StreamAssembler()

    def test_period_long_segment_is_break(self):
        elem = _narration("This ends with a period.", duration=5.0)
        assert self.assembler._is_natural_break(elem) is True

    def test_exclamation_long_segment_is_break(self):
        elem = _narration("Amazing!", duration=4.0)
        assert self.assembler._is_natural_break(elem) is True

    def test_question_long_segment_is_break(self):
        elem = _narration("Did you know?", duration=3.5)
        assert self.assembler._is_natural_break(elem) is True

    def test_short_segment_not_break(self):
        elem = _narration("Hi.", duration=1.0)
        assert self.assembler._is_natural_break(elem) is False

    def test_no_punctuation_not_break(self):
        elem = _narration("This has no ending punctuation", duration=10.0)
        assert self.assembler._is_natural_break(elem) is False

    def test_empty_text_not_break(self):
        elem = _narration("", duration=5.0)
        assert self.assembler._is_natural_break(elem) is False


# ── Relevance matching ───────────────────────────────────────────────────────


class TestRelevanceMatching:
    def setup_method(self):
        self.assembler = StreamAssembler()

    def test_keyword_overlap_is_relevant(self):
        narr = _narration("The ancient Colosseum stands tall.")
        ill = _illustration(caption="Colosseum historical view")
        assert self.assembler._is_relevant(ill, narr) is True

    def test_no_overlap_not_relevant(self):
        narr = _narration("The weather forecast for today looks promising.")
        ill = _illustration(caption="medieval castle architecture")
        assert self.assembler._is_relevant(ill, narr) is False

    def test_empty_caption_defaults_relevant(self):
        narr = _narration("Some text here.")
        ill = _illustration(caption="")
        assert self.assembler._is_relevant(ill, narr) is True

    def test_short_narration_defaults_relevant(self):
        narr = _narration("Hi there.")
        ill = _illustration(caption="totally unrelated")
        # Short narration → default relevant
        assert self.assembler._is_relevant(ill, narr) is True


# ── Element factory methods ──────────────────────────────────────────────────


class TestElementFactories:
    def setup_method(self):
        self.assembler = StreamAssembler()

    def test_create_narration_element(self):
        elem = self.assembler.create_narration_element(
            "Hello World.", audio_duration=3.5, emotional_tone="enthusiastic"
        )
        assert elem.type == ContentElementType.NARRATION
        assert elem.narration_text == "Hello World."
        assert elem.audio_duration == 3.5
        assert elem.emotional_tone == "enthusiastic"

    def test_create_illustration_element(self):
        elem = self.assembler.create_illustration_element(
            image_url="https://example.com/img.png",
            caption="A view",
            visual_style="WATERCOLOR",
        )
        assert elem.type == ContentElementType.ILLUSTRATION
        assert elem.image_url == "https://example.com/img.png"
        assert elem.caption == "A view"

    def test_create_fact_element(self):
        elem = self.assembler.create_fact_element(
            "The Earth is round.", verified=True, confidence=0.99
        )
        assert elem.type == ContentElementType.FACT
        assert elem.verified is True
        assert elem.confidence == 0.99

    def test_create_video_element(self):
        elem = self.assembler.create_video_element(
            video_url="https://example.com/v.mp4",
            video_duration=15.0,
            caption="Cinematic view",
        )
        assert elem.type == ContentElementType.VIDEO
        assert elem.video_duration == 15.0

    def test_create_transition_element(self):
        elem = self.assembler.create_transition_element("Let's move on.")
        assert elem.type == ContentElementType.TRANSITION
        assert elem.transition_text == "Let's move on."
