"""Property-based tests for documentary stream continuity.

Property 4: Documentary Stream Continuity
  For any documentary stream session, the time gap between consecutive
  content elements (narration, video, illustration, fact) shall not
  exceed 1 second, ensuring seamless user experience.

  Validates: Requirements 5.3

Feature: lore-multimodal-documentary-app, Property 4: Documentary Stream Continuity
"""

from __future__ import annotations

import time

import pytest
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from backend.services.orchestrator.models import (
    ContentElement,
    ContentElementType,
    DocumentaryStream,
    Mode,
)
from backend.services.orchestrator.stream_assembler import (
    MAX_GAP_SECONDS,
    ContentSynchronizer,
    StreamAssembler,
    StreamBuffer,
    get_element_duration,
)


# ── Strategies ────────────────────────────────────────────────────────────────

modes = st.sampled_from(list(Mode))

narration_texts = st.text(
    min_size=3,
    max_size=80,
    alphabet=st.characters(whitelist_categories=("L", "N", "Z", "P")),
)

# Narration durations between 0.5 and 30 seconds
narration_durations = st.floats(min_value=0.5, max_value=30.0)

# Video durations between 8 and 60 seconds (Veo range)
video_durations = st.floats(min_value=8.0, max_value=60.0)


def narration_element_strategy():
    """Strategy that produces narration ContentElements."""
    return st.builds(
        lambda text, dur: ContentElement(
            type=ContentElementType.NARRATION,
            narration_text=text,
            audio_duration=dur,
            timestamp=time.time(),
        ),
        text=narration_texts,
        dur=narration_durations,
    )


def illustration_element_strategy():
    """Strategy that produces illustration ContentElements."""
    return st.builds(
        lambda caption: ContentElement(
            type=ContentElementType.ILLUSTRATION,
            caption=caption,
            timestamp=time.time(),
        ),
        caption=narration_texts,
    )


def fact_element_strategy():
    """Strategy that produces fact ContentElements."""
    return st.builds(
        lambda claim: ContentElement(
            type=ContentElementType.FACT,
            claim_text=claim,
            verified=True,
            confidence=0.9,
            timestamp=time.time(),
        ),
        claim=narration_texts,
    )


def video_element_strategy():
    """Strategy that produces video ContentElements."""
    return st.builds(
        lambda dur: ContentElement(
            type=ContentElementType.VIDEO,
            video_url="https://example.com/video.mp4",
            video_duration=dur,
            timestamp=time.time(),
        ),
        dur=video_durations,
    )


# ── Property tests ────────────────────────────────────────────────────────────


class TestDocumentaryStreamContinuity:
    """Property 4: No gap between consecutive stream elements exceeds 1 second.

    Feature: lore-multimodal-documentary-app
    Property 4: Documentary Stream Continuity
    """

    @given(
        narrations=st.lists(narration_element_strategy(), min_size=1, max_size=20),
        illustrations=st.lists(illustration_element_strategy(), min_size=0, max_size=10),
        facts=st.lists(fact_element_strategy(), min_size=0, max_size=10),
        mode=modes,
    )
    @settings(
        max_examples=150,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_assembled_stream_gaps_within_limit(
        self,
        narrations: list[ContentElement],
        illustrations: list[ContentElement],
        facts: list[ContentElement],
        mode: Mode,
    ):
        """After assembly and synchronization, no inter-element gap exceeds 1s."""
        assembler = StreamAssembler()
        sync = ContentSynchronizer()

        stream = assembler.assemble(
            request_id="prop-test",
            session_id="prop-session",
            mode=mode,
            narration_elements=narrations,
            illustration_elements=illustrations,
            fact_elements=facts,
        )

        if len(stream.elements) < 2:
            return  # Trivially passes

        max_gap = sync.max_gap(stream.elements)
        assert max_gap <= MAX_GAP_SECONDS, (
            f"Gap {max_gap:.3f}s exceeds limit {MAX_GAP_SECONDS}s "
            f"with {len(stream.elements)} elements"
        )

    @given(
        narrations=st.lists(narration_element_strategy(), min_size=1, max_size=15),
        videos=st.lists(video_element_strategy(), min_size=0, max_size=5),
        illustrations=st.lists(illustration_element_strategy(), min_size=0, max_size=8),
        facts=st.lists(fact_element_strategy(), min_size=0, max_size=8),
        mode=modes,
    )
    @settings(
        max_examples=150,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_full_content_stream_gaps_within_limit(
        self,
        narrations: list[ContentElement],
        videos: list[ContentElement],
        illustrations: list[ContentElement],
        facts: list[ContentElement],
        mode: Mode,
    ):
        """With all content types (including video), gaps still respect the limit."""
        assembler = StreamAssembler()
        sync = ContentSynchronizer()

        stream = assembler.assemble(
            request_id="prop-test-full",
            session_id="prop-session",
            mode=mode,
            narration_elements=narrations,
            illustration_elements=illustrations,
            fact_elements=facts,
            video_elements=videos,
        )

        if len(stream.elements) < 2:
            return

        max_gap = sync.max_gap(stream.elements)
        assert max_gap <= MAX_GAP_SECONDS, (
            f"Gap {max_gap:.3f}s exceeds limit {MAX_GAP_SECONDS}s "
            f"with {len(stream.elements)} elements (incl. video)"
        )

    @given(
        elements=st.lists(
            st.one_of(
                narration_element_strategy(),
                illustration_element_strategy(),
                fact_element_strategy(),
                video_element_strategy(),
            ),
            min_size=2,
            max_size=30,
        ),
    )
    @settings(
        max_examples=150,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_synchronizer_closes_all_gaps(
        self, elements: list[ContentElement]
    ):
        """The ContentSynchronizer guarantees max gap ≤ 1s for any element sequence."""
        sync = ContentSynchronizer()
        sync.synchronize(elements)
        max_gap = sync.max_gap(elements)
        assert max_gap <= MAX_GAP_SECONDS, (
            f"Synchronizer failed: max gap {max_gap:.3f}s > {MAX_GAP_SECONDS}s"
        )

    @given(
        narrations=st.lists(narration_element_strategy(), min_size=1, max_size=10),
        mode=modes,
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_sequence_ids_are_sequential(
        self, narrations: list[ContentElement], mode: Mode
    ):
        """All elements in an assembled stream have monotonically increasing sequence IDs."""
        assembler = StreamAssembler()
        stream = assembler.assemble(
            request_id="seq-test",
            session_id="seq-session",
            mode=mode,
            narration_elements=narrations,
        )
        ids = [e.sequence_id for e in stream.elements]
        assert ids == list(range(len(ids)))

    @given(
        narrations=st.lists(narration_element_strategy(), min_size=1, max_size=15),
        illustrations=st.lists(illustration_element_strategy(), min_size=0, max_size=8),
        facts=st.lists(fact_element_strategy(), min_size=0, max_size=8),
        videos=st.lists(video_element_strategy(), min_size=0, max_size=3),
    )
    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    def test_all_input_elements_appear_in_output(
        self,
        narrations: list[ContentElement],
        illustrations: list[ContentElement],
        facts: list[ContentElement],
        videos: list[ContentElement],
    ):
        """No input elements are dropped during assembly."""
        assembler = StreamAssembler()
        stream = assembler.assemble(
            request_id="drop-test",
            session_id="drop-session",
            mode=Mode.SIGHT,
            narration_elements=narrations,
            illustration_elements=illustrations,
            fact_elements=facts,
            video_elements=videos,
        )

        total_input = len(narrations) + len(illustrations) + len(facts) + len(videos)
        assert len(stream.elements) == total_input, (
            f"Expected {total_input} elements, got {len(stream.elements)}"
        )
