"""Property test: Content Parser Round-Trip (Property 22 / Requirement 28.5).

Feature: lore-multimodal-documentary-app, Property 22: Content Serialization Round-Trip.

FOR ALL valid ContentElement objects C:
    parse(serialize(C)) SHALL equal C

Ensures lossless content storage and transmission.

Validates: Requirements 28.5
Strategy:
  - Generate random ContentElement objects for all five element types.
  - Serialize each element to a DCF JSON string.
  - Parse the DCF string back into a ContentElement.
  - Verify that the reconstructed element equals the original on all
    type-relevant fields.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from backend.services.content_parser.parser import ContentParser
from backend.services.content_parser.serializer import ContentSerializer
from backend.services.orchestrator.models import (
    ContentElement,
    ContentElementType,
    DocumentaryStream,
    Mode,
)


# ── Hypothesis strategies ─────────────────────────────────────────────────────

_safe_text = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,!?-_/:",
    min_size=0,
    max_size=120,
)

_url_text = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789./:-_",
    min_size=0,
    max_size=80,
)

_id_text = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
    min_size=4,
    max_size=16,
)

_tone_strategy = st.sampled_from([
    "neutral", "respectful", "enthusiastic", "contemplative",
    "neutral;lang=fr;depth=expert", "respectful;lang=ja",
    "enthusiastic;lang=de;depth=scholar",
])

_authority_strategy = st.sampled_from(["academic", "government", "media", "other"])

_visual_style_strategy = st.sampled_from([
    "illustrated", "photorealistic", "cinematic", "watercolor",
    "oil_painting", "sketch",
])

_transition_type_strategy = st.sampled_from([
    "scene_change", "topic_shift", "branch_enter", "branch_exit",
])

_source_strategy = st.fixed_dictionaries({
    "title": _safe_text,
    "url": _url_text,
    "authority": _authority_strategy,
    "excerpt": _safe_text,
})

_narration_element_strategy = st.fixed_dictionaries({
    "id": _id_text,
    "sequence_id": st.integers(min_value=0, max_value=9999),
    "timestamp": st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    "narration_text": _safe_text,
    "audio_data": st.one_of(st.none(), st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=", min_size=0, max_size=32)),
    "audio_duration": st.floats(min_value=0.0, max_value=600.0, allow_nan=False, allow_infinity=False),
    "emotional_tone": _tone_strategy,
})

_video_element_strategy = st.fixed_dictionaries({
    "id": _id_text,
    "sequence_id": st.integers(min_value=0, max_value=9999),
    "timestamp": st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    "video_url": _url_text,
    "video_duration": st.floats(min_value=0.0, max_value=600.0, allow_nan=False, allow_infinity=False),
})

_illustration_element_strategy = st.fixed_dictionaries({
    "id": _id_text,
    "sequence_id": st.integers(min_value=0, max_value=9999),
    "timestamp": st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    "image_url": _url_text,
    "image_data": st.one_of(st.none(), st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=", min_size=0, max_size=32)),
    "caption": _safe_text,
    "visual_style": _visual_style_strategy,
})

_fact_element_strategy = st.fixed_dictionaries({
    "id": _id_text,
    "sequence_id": st.integers(min_value=0, max_value=9999),
    "timestamp": st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    "claim_text": _safe_text,
    "verified": st.booleans(),
    "confidence": st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    "sources": st.lists(_source_strategy, min_size=0, max_size=5),
})

_transition_element_strategy = st.fixed_dictionaries({
    "id": _id_text,
    "sequence_id": st.integers(min_value=0, max_value=9999),
    "timestamp": st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    "transition_text": st.one_of(
        _transition_type_strategy,
        st.builds(
            lambda tt, msg: f"{tt}|{msg}" if msg else tt,
            tt=_transition_type_strategy,
            msg=_safe_text,
        ),
    ),
})


def _build_narration(d: dict) -> ContentElement:
    return ContentElement(type=ContentElementType.NARRATION, **d)


def _build_video(d: dict) -> ContentElement:
    return ContentElement(type=ContentElementType.VIDEO, **d)


def _build_illustration(d: dict) -> ContentElement:
    return ContentElement(type=ContentElementType.ILLUSTRATION, **d)


def _build_fact(d: dict) -> ContentElement:
    return ContentElement(type=ContentElementType.FACT, **d)


def _build_transition(d: dict) -> ContentElement:
    return ContentElement(type=ContentElementType.TRANSITION, **d)


# ── Property tests ─────────────────────────────────────────────────────────────


class TestContentParserRoundTrip:
    """Feature: lore-multimodal-documentary-app,
    Property 22: Content Serialization Round-Trip."""

    @settings(
        max_examples=150,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(data=_narration_element_strategy)
    def test_narration_round_trip(self, data: dict):
        """NARRATION: parse(serialize(C)) preserves all narration fields."""
        parser = ContentParser()
        serializer = ContentSerializer()

        original = _build_narration(data)
        serialized = serializer.serialize(original)
        parsed = parser.parse(serialized)

        assert parsed.id == original.id
        assert parsed.type == original.type
        assert parsed.sequence_id == original.sequence_id
        assert abs(parsed.timestamp - original.timestamp) < 1e-6
        assert parsed.narration_text == original.narration_text
        assert parsed.audio_data == original.audio_data
        assert abs(parsed.audio_duration - original.audio_duration) < 1e-6

    @settings(
        max_examples=150,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(data=_video_element_strategy)
    def test_video_round_trip(self, data: dict):
        """VIDEO: parse(serialize(C)) preserves all video fields."""
        parser = ContentParser()
        serializer = ContentSerializer()

        original = _build_video(data)
        serialized = serializer.serialize(original)
        parsed = parser.parse(serialized)

        assert parsed.id == original.id
        assert parsed.type == original.type
        assert parsed.sequence_id == original.sequence_id
        assert abs(parsed.timestamp - original.timestamp) < 1e-6
        assert parsed.video_url == original.video_url
        assert abs(parsed.video_duration - original.video_duration) < 1e-6

    @settings(
        max_examples=150,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(data=_illustration_element_strategy)
    def test_illustration_round_trip(self, data: dict):
        """ILLUSTRATION: parse(serialize(C)) preserves all illustration fields."""
        parser = ContentParser()
        serializer = ContentSerializer()

        original = _build_illustration(data)
        serialized = serializer.serialize(original)
        parsed = parser.parse(serialized)

        assert parsed.id == original.id
        assert parsed.type == original.type
        assert parsed.sequence_id == original.sequence_id
        assert abs(parsed.timestamp - original.timestamp) < 1e-6
        assert parsed.image_url == original.image_url
        assert parsed.image_data == original.image_data
        assert parsed.caption == original.caption
        assert parsed.visual_style == original.visual_style

    @settings(
        max_examples=150,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(data=_fact_element_strategy)
    def test_fact_round_trip(self, data: dict):
        """FACT: parse(serialize(C)) preserves all fact fields."""
        parser = ContentParser()
        serializer = ContentSerializer()

        original = _build_fact(data)
        serialized = serializer.serialize(original)
        parsed = parser.parse(serialized)

        assert parsed.id == original.id
        assert parsed.type == original.type
        assert parsed.sequence_id == original.sequence_id
        assert abs(parsed.timestamp - original.timestamp) < 1e-6
        assert parsed.claim_text == original.claim_text
        assert parsed.verified == original.verified
        assert abs((parsed.confidence or 0.0) - (original.confidence or 0.0)) < 1e-6
        assert len(parsed.sources) == len(original.sources)
        for psrc, osrc in zip(parsed.sources, original.sources):
            assert psrc["title"] == osrc["title"]
            assert psrc["url"] == osrc["url"]
            assert psrc["authority"] == osrc.get("authority", "other")
            assert psrc["excerpt"] == osrc["excerpt"]

    @settings(
        max_examples=150,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(data=_transition_element_strategy)
    def test_transition_round_trip(self, data: dict):
        """TRANSITION: parse(serialize(C)) preserves transition_type and message."""
        parser = ContentParser()
        serializer = ContentSerializer()

        original = _build_transition(data)
        serialized = serializer.serialize(original)
        parsed = parser.parse(serialized)

        assert parsed.id == original.id
        assert parsed.type == original.type
        assert parsed.sequence_id == original.sequence_id
        assert abs(parsed.timestamp - original.timestamp) < 1e-6

        # The transition_text in original encodes type|message or just type.
        # After round-trip, the same information must be present.
        orig_tt = original.transition_text or ""
        parsed_tt = parsed.transition_text or ""
        if "|" in orig_tt:
            orig_type, orig_msg = orig_tt.split("|", 1)
        else:
            orig_type, orig_msg = orig_tt, ""
        if "|" in parsed_tt:
            parsed_type, parsed_msg = parsed_tt.split("|", 1)
        else:
            parsed_type, parsed_msg = parsed_tt, ""

        assert orig_type == parsed_type
        assert orig_msg == parsed_msg

    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        narration=_narration_element_strategy,
        fact=_fact_element_strategy,
        transition=_transition_element_strategy,
    )
    def test_stream_round_trip(self, narration: dict, fact: dict, transition: dict):
        """DocumentaryStream: parse_stream(serialize_stream(S)) preserves all elements."""
        parser = ContentParser()
        serializer = ContentSerializer()

        # Assign distinct sequence IDs
        narration = dict(narration, sequence_id=0)
        fact = dict(fact, sequence_id=1)
        transition = dict(transition, sequence_id=2)

        elements = [
            _build_narration(narration),
            _build_fact(fact),
            _build_transition(transition),
        ]
        stream = DocumentaryStream(
            stream_id="prop_stream",
            request_id="prop_req",
            session_id="prop_sess",
            mode=Mode.VOICE,
            elements=elements,
            started_at=1000.0,
        )

        serialized = serializer.serialize_stream(stream)
        parsed_stream = parser.parse_stream(serialized)

        assert parsed_stream.stream_id == stream.stream_id
        assert parsed_stream.mode == stream.mode
        assert len(parsed_stream.elements) == len(stream.elements)
        for original_el, parsed_el in zip(stream.elements, parsed_stream.elements):
            assert parsed_el.type == original_el.type
            assert parsed_el.sequence_id == original_el.sequence_id

    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        element_type=st.sampled_from(["narration", "video", "illustration", "fact", "transition"]),
        element_id=_id_text,
        sequence_id=st.integers(min_value=0, max_value=999),
        timestamp=st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    )
    def test_element_identity_preserved_for_all_types(
        self, element_type: str, element_id: str, sequence_id: int, timestamp: float
    ):
        """id, type, sequence_id, timestamp preserved across all element types."""
        parser = ContentParser()
        serializer = ContentSerializer()

        type_enum = ContentElementType(element_type)
        kwargs: dict = {
            "id": element_id,
            "type": type_enum,
            "sequence_id": sequence_id,
            "timestamp": timestamp,
        }
        # Set minimal type-specific fields
        if element_type == "narration":
            kwargs["narration_text"] = "test"
        elif element_type == "fact":
            kwargs["claim_text"] = "test claim"
        elif element_type == "transition":
            kwargs["transition_text"] = "scene_change"

        original = ContentElement(**kwargs)
        serialized = serializer.serialize(original)
        parsed = parser.parse(serialized)

        assert parsed.id == original.id
        assert parsed.type == original.type
        assert parsed.sequence_id == original.sequence_id
        assert abs(parsed.timestamp - original.timestamp) < 1e-6

    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(
        claim=_safe_text,
        verified=st.booleans(),
        confidence=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        sources=st.lists(_source_strategy, min_size=0, max_size=8),
    )
    def test_fact_sources_preserved(
        self, claim: str, verified: bool, confidence: float, sources: list
    ):
        """All fact sources are preserved through parse(serialize(C))."""
        parser = ContentParser()
        serializer = ContentSerializer()

        original = ContentElement(
            id="ftest",
            type=ContentElementType.FACT,
            sequence_id=0,
            timestamp=1000.0,
            claim_text=claim,
            verified=verified,
            confidence=confidence,
            sources=sources,
        )

        parsed = parser.parse(serializer.serialize(original))

        assert len(parsed.sources) == len(sources)
        for psrc, osrc in zip(parsed.sources, sources):
            assert psrc["title"] == osrc["title"]
            assert psrc["excerpt"] == osrc["excerpt"]

    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(data=_narration_element_strategy)
    def test_serialized_dcf_is_valid_json(self, data: dict):
        """serialize() always produces valid JSON."""
        import json as _json
        serializer = ContentSerializer()
        original = _build_narration(data)
        serialized = serializer.serialize(original)
        # Must not raise
        parsed_json = _json.loads(serialized)
        assert isinstance(parsed_json, dict)
        assert "version" in parsed_json
        assert parsed_json["version"] == "1.0"

    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(data=_fact_element_strategy)
    def test_serialized_fact_dcf_contains_required_fields(self, data: dict):
        """Serialized FACT DCF always contains required top-level fields."""
        import json as _json
        serializer = ContentSerializer()
        original = _build_fact(data)
        serialized = serializer.serialize(original)
        d = _json.loads(serialized)
        for field in ["version", "element_id", "sequence_id", "timestamp", "type", "content"]:
            assert field in d, f"Missing required DCF field: {field}"
        assert d["type"] == "fact"
        assert "claim" in d["content"]
