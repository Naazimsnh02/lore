"""Property-based tests for the Alternate History Engine.

Tests validate:
  - What-if detection consistency (Property: detection is deterministic)
  - Scenario extraction preserves original question
  - Speculative labeling always present on output
  - Causal chain link confidence bounds

Requirements: 15.1, 15.2, 15.3.
"""

from __future__ import annotations

import string

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.services.alternate_history.detector import AlternateHistoryDetector
from backend.services.alternate_history.engine import AlternateHistoryEngine
from backend.services.alternate_history.models import (
    CausalLink,
    ContentLabel,
    HistoricalEvent,
    SpeculativeContent,
    WhatIfQuestion,
)


# ── Strategies ────────────────────────────────────────────────────────────────

# Text that always includes a what-if pattern
what_if_prefixes = st.sampled_from([
    "What if ",
    "Imagine if ",
    "Suppose ",
    "What would happen if ",
    "Alternate history: ",
    "Alternative history of ",
])

topic_text = st.text(
    alphabet=string.ascii_letters + string.digits + " .,'-",
    min_size=3,
    max_size=100,
).filter(lambda s: s.strip())

what_if_text = st.builds(lambda p, t: p + t, what_if_prefixes, topic_text)

# Text that should NOT be what-if
normal_text = st.text(
    alphabet=string.ascii_letters + string.digits + " .,",
    min_size=5,
    max_size=100,
).filter(
    lambda s: s.strip()
    and "what if" not in s.lower()
    and "imagine if" not in s.lower()
    and "suppose" not in s.lower()
    and "would happen if" not in s.lower()
    and "alternate history" not in s.lower()
    and "alternative history" not in s.lower()
    and "be different if" not in s.lower()
    and "could have happened if" not in s.lower()
    and "look like if" not in s.lower()
    and "have changed if" not in s.lower()
)


# ── Detection properties ─────────────────────────────────────────────────────


class TestDetectionProperties:
    """Feature: lore-multimodal-documentary-app, Property: what-if detection determinism."""

    @given(text=what_if_text)
    @settings(max_examples=120)
    def test_what_if_always_detected(self, text: str):
        """Any text starting with a known what-if prefix is detected."""
        detector = AlternateHistoryDetector()
        assert detector.is_what_if(text) is True

    @given(text=normal_text)
    @settings(max_examples=120)
    def test_non_what_if_not_detected(self, text: str):
        """Text without what-if patterns is not detected."""
        detector = AlternateHistoryDetector()
        assert detector.is_what_if(text) is False

    @given(text=what_if_text)
    @settings(max_examples=100)
    def test_detection_is_deterministic(self, text: str):
        """Same input always produces same detection result."""
        detector = AlternateHistoryDetector()
        r1 = detector.is_what_if(text)
        r2 = detector.is_what_if(text)
        assert r1 == r2


# ── Extraction properties ────────────────────────────────────────────────────


class TestExtractionProperties:
    """Feature: lore-multimodal-documentary-app, Property: scenario extraction correctness."""

    @given(text=what_if_text)
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_original_question_preserved(self, text: str):
        """Extraction always preserves the original question."""
        detector = AlternateHistoryDetector()
        result = await detector.extract_scenario(text)
        assert result.original_question == text

    @given(text=what_if_text)
    @settings(max_examples=100)
    @pytest.mark.asyncio
    async def test_result_is_what_if_question(self, text: str):
        """Extraction always returns a WhatIfQuestion."""
        detector = AlternateHistoryDetector()
        result = await detector.extract_scenario(text)
        assert isinstance(result, WhatIfQuestion)
        assert isinstance(result.base_event, HistoricalEvent)


# ── Labeling properties ──────────────────────────────────────────────────────


class TestLabelingProperties:
    """Feature: lore-multimodal-documentary-app, Property: speculative labeling (Req 15.4)."""

    @given(text=st.text(min_size=0, max_size=500))
    @settings(max_examples=100)
    def test_speculative_label_always_applied(self, text: str):
        """label_speculative always produces SpeculativeContent with a label."""
        engine = AlternateHistoryEngine()
        content = engine.label_speculative(text)
        assert isinstance(content, SpeculativeContent)
        assert content.label == ContentLabel.SPECULATIVE
        assert content.text == text
        assert content.disclaimer  # Never empty

    @given(
        text=st.text(min_size=1, max_size=200),
        label=st.sampled_from(list(ContentLabel)),
    )
    @settings(max_examples=100)
    def test_custom_label_preserved(self, text: str, label: ContentLabel):
        """Custom labels are correctly applied."""
        engine = AlternateHistoryEngine()
        content = engine.label_speculative(text, label=label)
        assert content.label == label


# ── Causal chain properties ──────────────────────────────────────────────────


class TestCausalChainProperties:
    """Feature: lore-multimodal-documentary-app, Property: causal chain validity (Req 15.5)."""

    @given(
        confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    )
    @settings(max_examples=100)
    def test_confidence_bounds(self, confidence: float):
        """CausalLink confidence is always in [0, 1]."""
        link = CausalLink(
            from_event="A", to_event="B", confidence=confidence
        )
        assert 0.0 <= link.confidence <= 1.0
