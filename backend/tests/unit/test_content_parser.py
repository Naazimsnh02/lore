"""Unit tests for Content Parser and Serializer (Task 27.1).

Requirements: 28.1 – 28.7
"""

from __future__ import annotations

import json

import pytest

from backend.services.content_parser.models import (
    DCFElement,
    DCFStream,
    FactContent,
    IllustrationContent,
    NarrationContent,
    SourceCitation,
    TransitionContent,
    VideoContent,
)
from backend.services.content_parser.parser import ContentParser, ParseError
from backend.services.content_parser.serializer import ContentSerializer
from backend.services.orchestrator.models import (
    ContentElement,
    ContentElementType,
    DocumentaryStream,
    Mode,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def parser() -> ContentParser:
    return ContentParser()


@pytest.fixture
def serializer() -> ContentSerializer:
    return ContentSerializer()


def _narration_element(**kwargs) -> ContentElement:
    defaults = dict(
        id="abc123",
        type=ContentElementType.NARRATION,
        sequence_id=0,
        timestamp=1000.0,
        narration_text="The Roman Colosseum was built in 70 AD.",
        audio_duration=5.0,
        emotional_tone="respectful",
    )
    defaults.update(kwargs)
    return ContentElement(**defaults)


def _video_element(**kwargs) -> ContentElement:
    defaults = dict(
        id="vid001",
        type=ContentElementType.VIDEO,
        sequence_id=1,
        timestamp=1005.0,
        video_url="gs://bucket/video.mp4",
        video_duration=30.0,
    )
    defaults.update(kwargs)
    return ContentElement(**defaults)


def _illustration_element(**kwargs) -> ContentElement:
    defaults = dict(
        id="ill001",
        type=ContentElementType.ILLUSTRATION,
        sequence_id=2,
        timestamp=1010.0,
        image_url="gs://bucket/image.png",
        caption="Ancient Roman architecture",
        visual_style="illustrated",
    )
    defaults.update(kwargs)
    return ContentElement(**defaults)


def _fact_element(**kwargs) -> ContentElement:
    defaults = dict(
        id="fct001",
        type=ContentElementType.FACT,
        sequence_id=3,
        timestamp=1015.0,
        claim_text="The Colosseum held 50,000 to 80,000 spectators.",
        verified=True,
        confidence=0.95,
        sources=[{"title": "Wikipedia", "url": "https://en.wikipedia.org", "authority": "media", "excerpt": "..."}],
    )
    defaults.update(kwargs)
    return ContentElement(**defaults)


def _transition_element(**kwargs) -> ContentElement:
    defaults = dict(
        id="trn001",
        type=ContentElementType.TRANSITION,
        sequence_id=4,
        timestamp=1020.0,
        transition_text="scene_change|Moving to next scene",
    )
    defaults.update(kwargs)
    return ContentElement(**defaults)


# ── Tests: DCF Grammar (Req 28.1) ─────────────────────────────────────────────


class TestDCFGrammar:
    def test_narration_content_model(self):
        c = NarrationContent(transcript="Hello world", duration=3.0)
        assert c.transcript == "Hello world"
        assert c.duration == 3.0
        assert c.language == "en"
        assert c.depth_level == "explorer"

    def test_video_content_model(self):
        c = VideoContent(video_url="gs://bucket/v.mp4", duration=30.0)
        assert c.video_url == "gs://bucket/v.mp4"
        assert c.duration == 30.0
        assert c.resolution == "1080p"

    def test_illustration_content_model(self):
        c = IllustrationContent(caption="test", visual_style="cinematic")
        assert c.caption == "test"
        assert c.visual_style == "cinematic"

    def test_fact_content_model(self):
        src = SourceCitation(title="A", url="http://a.com", authority="academic", excerpt="x")
        c = FactContent(claim="The earth is round", sources=[src], confidence=0.99)
        assert c.claim == "The earth is round"
        assert len(c.sources) == 1
        assert c.sources[0].authority == "academic"

    def test_transition_content_model(self):
        c = TransitionContent(transition_type="branch_enter", message="Entering branch")
        assert c.transition_type == "branch_enter"
        assert c.message == "Entering branch"

    def test_source_citation_invalid_authority_raises(self):
        with pytest.raises(Exception):
            SourceCitation(title="X", url="x", authority="unknown", excerpt="")

    def test_transition_content_invalid_type_raises(self):
        with pytest.raises(Exception):
            TransitionContent(transition_type="invalid_type")

    def test_dcf_element_requires_valid_type(self):
        with pytest.raises(Exception):
            DCFElement(
                element_id="x",
                sequence_id=0,
                timestamp=0.0,
                type="invalid",
                content=NarrationContent(transcript=""),
            )

    def test_dcf_stream_mode_validation(self):
        with pytest.raises(Exception):
            DCFStream(stream_id="x", mode="invalid_mode", started_at=0.0)


# ── Tests: Content Serializer (Req 28.3, 28.4) ───────────────────────────────


class TestContentSerializer:
    def test_serialize_narration_produces_valid_json(self, serializer):
        el = _narration_element()
        result = serializer.serialize(el)
        data = json.loads(result)
        assert data["type"] == "narration"
        assert data["element_id"] == "abc123"
        assert data["content"]["transcript"] == "The Roman Colosseum was built in 70 AD."
        assert data["version"] == "1.0"

    def test_serialize_video_produces_valid_json(self, serializer):
        el = _video_element()
        result = serializer.serialize(el)
        data = json.loads(result)
        assert data["type"] == "video"
        assert data["content"]["video_url"] == "gs://bucket/video.mp4"
        assert data["content"]["duration"] == 30.0

    def test_serialize_illustration_produces_valid_json(self, serializer):
        el = _illustration_element()
        result = serializer.serialize(el)
        data = json.loads(result)
        assert data["type"] == "illustration"
        assert data["content"]["caption"] == "Ancient Roman architecture"
        assert data["content"]["visual_style"] == "illustrated"

    def test_serialize_fact_produces_valid_json(self, serializer):
        el = _fact_element()
        result = serializer.serialize(el)
        data = json.loads(result)
        assert data["type"] == "fact"
        assert data["content"]["claim"] == "The Colosseum held 50,000 to 80,000 spectators."
        assert data["content"]["verified"] is True
        assert data["content"]["confidence"] == pytest.approx(0.95)

    def test_serialize_transition_produces_valid_json(self, serializer):
        el = _transition_element()
        result = serializer.serialize(el)
        data = json.loads(result)
        assert data["type"] == "transition"
        assert data["content"]["transition_type"] == "scene_change"
        assert data["content"]["message"] == "Moving to next scene"

    def test_serialize_preserves_sequence_id_and_timestamp(self, serializer):
        el = _narration_element(sequence_id=42, timestamp=9999.5)
        data = json.loads(serializer.serialize(el))
        assert data["sequence_id"] == 42
        assert data["timestamp"] == pytest.approx(9999.5)

    def test_serialize_stream(self, serializer):
        stream = DocumentaryStream(
            stream_id="s1",
            request_id="r1",
            session_id="sess1",
            mode=Mode.VOICE,
            elements=[_narration_element(), _fact_element()],
            started_at=1000.0,
        )
        result = serializer.serialize_stream(stream)
        data = json.loads(result)
        assert data["stream_id"] == "s1"
        assert data["mode"] == "voice"
        assert len(data["elements"]) == 2
        assert data["elements"][0]["type"] == "narration"
        assert data["elements"][1]["type"] == "fact"

    def test_serialize_narration_with_metadata(self, serializer):
        """Metadata (language, depth_level) encoded in emotional_tone."""
        el = _narration_element()
        # Simulate a narration with non-default language and depth
        el.emotional_tone = "enthusiastic;lang=fr;depth=expert"
        result = serializer.serialize(el)
        data = json.loads(result)
        assert data["content"]["tone"] == "enthusiastic"
        assert data["content"]["language"] == "fr"
        assert data["content"]["depth_level"] == "expert"

    def test_serialize_video_with_all_metadata(self, serializer):
        """Video thumbnail/resolution metadata round-trips through transition_text."""
        el = _video_element()
        el.transition_text = "thumb=https://t.co/img.jpg;res=4K;audio=1;desc=Aerial shot"
        result = serializer.serialize(el)
        data = json.loads(result)
        assert data["content"]["thumbnail_url"] == "https://t.co/img.jpg"
        assert data["content"]["resolution"] == "4K"
        assert data["content"]["has_native_audio"] is True
        assert data["content"]["scene_description"] == "Aerial shot"


# ── Tests: Content Parser (Req 28.2, 28.6, 28.7) ─────────────────────────────


class TestContentParser:
    def test_parse_narration_dcf(self, parser):
        dcf = json.dumps({
            "version": "1.0",
            "element_id": "abc123",
            "sequence_id": 0,
            "timestamp": 1000.0,
            "type": "narration",
            "content": {
                "transcript": "Hello world",
                "duration": 3.0,
                "tone": "neutral",
                "language": "en",
                "depth_level": "explorer",
            },
        })
        el = parser.parse(dcf)
        assert el.type == ContentElementType.NARRATION
        assert el.narration_text == "Hello world"
        assert el.audio_duration == 3.0
        assert el.id == "abc123"

    def test_parse_video_dcf(self, parser):
        dcf = json.dumps({
            "version": "1.0",
            "element_id": "vid001",
            "sequence_id": 1,
            "timestamp": 1005.0,
            "type": "video",
            "content": {
                "video_url": "gs://bucket/v.mp4",
                "duration": 30.0,
            },
        })
        el = parser.parse(dcf)
        assert el.type == ContentElementType.VIDEO
        assert el.video_url == "gs://bucket/v.mp4"
        assert el.video_duration == 30.0

    def test_parse_illustration_dcf(self, parser):
        dcf = json.dumps({
            "version": "1.0",
            "element_id": "ill001",
            "sequence_id": 2,
            "timestamp": 1010.0,
            "type": "illustration",
            "content": {
                "image_url": "gs://bucket/image.png",
                "caption": "Ancient Rome",
                "visual_style": "watercolor",
            },
        })
        el = parser.parse(dcf)
        assert el.type == ContentElementType.ILLUSTRATION
        assert el.image_url == "gs://bucket/image.png"
        assert el.caption == "Ancient Rome"
        assert el.visual_style == "watercolor"

    def test_parse_fact_dcf(self, parser):
        dcf = json.dumps({
            "version": "1.0",
            "element_id": "fct001",
            "sequence_id": 3,
            "timestamp": 1015.0,
            "type": "fact",
            "content": {
                "claim": "The Earth is round.",
                "verified": True,
                "confidence": 0.99,
                "sources": [
                    {"title": "NASA", "url": "https://nasa.gov", "authority": "government", "excerpt": "..."}
                ],
            },
        })
        el = parser.parse(dcf)
        assert el.type == ContentElementType.FACT
        assert el.claim_text == "The Earth is round."
        assert el.verified is True
        assert el.confidence == pytest.approx(0.99)
        assert len(el.sources) == 1
        assert el.sources[0]["authority"] == "government"

    def test_parse_transition_dcf(self, parser):
        dcf = json.dumps({
            "version": "1.0",
            "element_id": "trn001",
            "sequence_id": 4,
            "timestamp": 1020.0,
            "type": "transition",
            "content": {
                "transition_type": "branch_enter",
                "message": "Entering branch topic",
            },
        })
        el = parser.parse(dcf)
        assert el.type == ContentElementType.TRANSITION
        assert "branch_enter" in el.transition_text

    def test_parse_invalid_json_raises_parse_error(self, parser):
        with pytest.raises(ParseError, match="Invalid JSON"):
            parser.parse("{not valid json}")

    def test_parse_non_object_json_raises_parse_error(self, parser):
        with pytest.raises(ParseError, match="JSON object"):
            parser.parse("[1, 2, 3]")

    def test_parse_missing_type_raises_parse_error(self, parser):
        dcf = json.dumps({
            "element_id": "x",
            "sequence_id": 0,
            "timestamp": 0.0,
            "content": {},
        })
        with pytest.raises(ParseError, match="type"):
            parser.parse(dcf)

    def test_parse_missing_element_id_raises_parse_error(self, parser):
        dcf = json.dumps({
            "sequence_id": 0,
            "timestamp": 0.0,
            "type": "narration",
            "content": {"transcript": ""},
        })
        with pytest.raises(ParseError, match="element_id"):
            parser.parse(dcf)

    def test_parse_missing_content_raises_parse_error(self, parser):
        dcf = json.dumps({
            "element_id": "x",
            "sequence_id": 0,
            "timestamp": 0.0,
            "type": "narration",
        })
        with pytest.raises(ParseError, match="content"):
            parser.parse(dcf)

    def test_parse_invalid_type_raises_parse_error(self, parser):
        dcf = json.dumps({
            "element_id": "x",
            "sequence_id": 0,
            "timestamp": 0.0,
            "type": "unknown_type",
            "content": {},
        })
        with pytest.raises(ParseError):
            parser.parse(dcf)

    def test_parse_negative_sequence_id_raises_parse_error(self, parser):
        dcf = json.dumps({
            "element_id": "x",
            "sequence_id": -1,
            "timestamp": 0.0,
            "type": "narration",
            "content": {"transcript": ""},
        })
        with pytest.raises(ParseError):
            parser.parse(dcf)

    def test_parse_non_string_raises_parse_error(self, parser):
        with pytest.raises(ParseError):
            parser.parse(42)  # type: ignore[arg-type]

    def test_parse_stream(self, parser, serializer):
        """Parse a full DCF stream string."""
        stream = DocumentaryStream(
            stream_id="s1",
            request_id="r1",
            session_id="sess1",
            mode=Mode.SIGHT,
            elements=[_narration_element(), _video_element()],
            started_at=1000.0,
        )
        dcf_str = serializer.serialize_stream(stream)
        parsed = parser.parse_stream(dcf_str)
        assert parsed.stream_id == "s1"
        assert parsed.mode == Mode.SIGHT
        assert len(parsed.elements) == 2

    def test_validate_dict_returns_errors_for_missing_fields(self, parser):
        is_valid, errors = parser.validate_dict({"element_id": "x"})
        assert not is_valid
        assert any("sequence_id" in e for e in errors)

    def test_validate_dict_returns_true_for_valid_dict(self, parser):
        data = {
            "element_id": "x",
            "sequence_id": 0,
            "timestamp": 0.0,
            "type": "narration",
            "content": {"transcript": "hello"},
        }
        is_valid, errors = parser.validate_dict(data)
        assert is_valid
        assert errors == []


# ── Tests: Round-Trip (Req 28.5) ──────────────────────────────────────────────


class TestRoundTrip:
    def test_narration_round_trip(self, parser, serializer):
        el = _narration_element()
        serialized = serializer.serialize(el)
        parsed = parser.parse(serialized)
        assert parsed.id == el.id
        assert parsed.type == el.type
        assert parsed.sequence_id == el.sequence_id
        assert parsed.timestamp == pytest.approx(el.timestamp)
        assert parsed.narration_text == el.narration_text
        assert parsed.audio_duration == pytest.approx(el.audio_duration)

    def test_video_round_trip(self, parser, serializer):
        el = _video_element()
        parsed = parser.parse(serializer.serialize(el))
        assert parsed.id == el.id
        assert parsed.type == el.type
        assert parsed.video_url == el.video_url
        assert parsed.video_duration == pytest.approx(el.video_duration)

    def test_illustration_round_trip(self, parser, serializer):
        el = _illustration_element()
        parsed = parser.parse(serializer.serialize(el))
        assert parsed.id == el.id
        assert parsed.image_url == el.image_url
        assert parsed.caption == el.caption
        assert parsed.visual_style == el.visual_style

    def test_fact_round_trip(self, parser, serializer):
        el = _fact_element()
        parsed = parser.parse(serializer.serialize(el))
        assert parsed.id == el.id
        assert parsed.claim_text == el.claim_text
        assert parsed.verified == el.verified
        assert parsed.confidence == pytest.approx(el.confidence)
        assert len(parsed.sources) == len(el.sources)

    def test_transition_round_trip(self, parser, serializer):
        el = _transition_element()
        parsed = parser.parse(serializer.serialize(el))
        assert parsed.id == el.id
        assert parsed.type == el.type
        # Both contain "scene_change" and the message
        assert "scene_change" in parsed.transition_text
        assert "Moving to next scene" in parsed.transition_text

    def test_narration_with_audio_data_round_trip(self, parser, serializer):
        el = _narration_element(audio_data="base64encodedaudio==")
        parsed = parser.parse(serializer.serialize(el))
        assert parsed.audio_data == "base64encodedaudio=="

    def test_illustration_with_image_data_round_trip(self, parser, serializer):
        el = _illustration_element(image_data="base64encodedimage==")
        parsed = parser.parse(serializer.serialize(el))
        assert parsed.image_data == "base64encodedimage=="

    def test_fact_with_multiple_sources_round_trip(self, parser, serializer):
        el = _fact_element(
            sources=[
                {"title": "A", "url": "http://a.com", "authority": "academic", "excerpt": "x"},
                {"title": "B", "url": "http://b.com", "authority": "government", "excerpt": "y"},
            ]
        )
        parsed = parser.parse(serializer.serialize(el))
        assert len(parsed.sources) == 2
        assert parsed.sources[0]["authority"] == "academic"
        assert parsed.sources[1]["authority"] == "government"

    def test_stream_round_trip(self, parser, serializer):
        elements = [
            _narration_element(),
            _illustration_element(),
            _fact_element(),
            _transition_element(),
        ]
        stream = DocumentaryStream(
            stream_id="s1",
            request_id="r1",
            session_id="sess1",
            mode=Mode.LORE,
            elements=elements,
            started_at=1000.0,
            completed_at=1050.0,
        )
        serialized = serializer.serialize_stream(stream)
        parsed = parser.parse_stream(serialized)
        assert parsed.stream_id == stream.stream_id
        assert parsed.mode == stream.mode
        assert len(parsed.elements) == len(stream.elements)
        assert parsed.started_at == pytest.approx(stream.started_at)
        assert parsed.completed_at == pytest.approx(stream.completed_at)

    def test_all_content_types_round_trip_id_and_sequence(self, parser, serializer):
        """All five element types preserve id, type, sequence_id, timestamp."""
        elements = [
            _narration_element(id="n1", sequence_id=0, timestamp=100.0),
            _video_element(id="v1", sequence_id=1, timestamp=200.0),
            _illustration_element(id="i1", sequence_id=2, timestamp=300.0),
            _fact_element(id="f1", sequence_id=3, timestamp=400.0),
            _transition_element(id="t1", sequence_id=4, timestamp=500.0),
        ]
        for el in elements:
            parsed = parser.parse(serializer.serialize(el))
            assert parsed.id == el.id
            assert parsed.type == el.type
            assert parsed.sequence_id == el.sequence_id
            assert parsed.timestamp == pytest.approx(el.timestamp)

    def test_fact_with_alternative_perspectives_round_trip(self, parser, serializer):
        el = _fact_element(
            transition_text='perspectives=["View A", "View B"]',
        )
        serialized = serializer.serialize(el)
        parsed = parser.parse(serialized)
        # Re-serialize to check perspectives survived the round-trip
        data = json.loads(serialized)
        assert "View A" in data["content"]["alternative_perspectives"]
        assert "View B" in data["content"]["alternative_perspectives"]

    def test_video_with_all_metadata_round_trip(self, parser, serializer):
        el = _video_element(
            transition_text="thumb=https://cdn.example.com/thumb.jpg;res=4K;audio=1;desc=Aerial view"
        )
        parsed = parser.parse(serializer.serialize(el))
        # Video metadata is stored in transition_text; verify video fields round-trip
        assert parsed.video_url == el.video_url
        assert parsed.video_duration == pytest.approx(el.video_duration)

    def test_narration_with_non_default_language_round_trip(self, parser, serializer):
        """Non-default language and depth survive round-trip via emotional_tone encoding."""
        el = _narration_element(emotional_tone="enthusiastic;lang=ja;depth=expert")
        serialized = serializer.serialize(el)
        data = json.loads(serialized)
        assert data["content"]["language"] == "ja"
        assert data["content"]["depth_level"] == "expert"
        assert data["content"]["tone"] == "enthusiastic"
        # Parse back
        parsed = parser.parse(serialized)
        assert parsed.emotional_tone == "enthusiastic;lang=ja;depth=expert"
