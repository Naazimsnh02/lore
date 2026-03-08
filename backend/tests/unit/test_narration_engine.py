"""Unit tests for the Narration Engine service.

Tests cover:
  - NarrationEngine.generate_script (Task 9.1)
  - NarrationEngine.synthesize_speech streaming (Task 9.1)
  - NarrationEngine.translate_script (Task 9.1)
  - AffectiveNarrator tone detection (Task 9.2)
  - AffectiveNarrator voice parameter mapping (Task 9.2)
  - Edge cases: timeouts, parse failures, fallbacks

Requirements validated: 3.1, 3.2, 5.2, 11.1–11.6.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from backend.services.narration_engine.affective_narrator import AffectiveNarrator
from backend.services.narration_engine.engine import (
    LIVE_AUDIO_MODEL,
    SCRIPT_MODEL,
    NarrationEngine,
)
from backend.services.narration_engine.models import (
    AudioChunk,
    DepthLevel,
    EmotionalTone,
    NarrationContext,
    NarrationScript,
    NarrationSegment,
    VoiceParameters,
)


# ── Fixtures ──────────────────────────────────────────────────


def _make_context(**overrides: Any) -> NarrationContext:
    """Create a NarrationContext with sensible defaults."""
    defaults = {
        "mode": "sight",
        "place_name": "Colosseum",
        "place_description": "Ancient Roman amphitheatre",
        "place_types": ["tourist_attraction", "historic_site"],
        "visual_description": "A large stone amphitheatre under blue sky",
        "language": "en",
        "depth_level": DepthLevel.EXPLORER,
    }
    defaults.update(overrides)
    return NarrationContext(**defaults)


def _make_script(
    text: str = "Welcome to the Colosseum.",
    tone: EmotionalTone = EmotionalTone.NEUTRAL,
    segments: int = 1,
) -> NarrationScript:
    """Create a simple NarrationScript."""
    segs = [
        NarrationSegment(text=text, duration=5.0, tone=tone)
        for _ in range(segments)
    ]
    return NarrationScript(
        segments=segs,
        total_duration=5.0 * segments,
        language="en",
        depth_level=DepthLevel.EXPLORER,
        tone=tone,
    )


class _MockInlineData:
    """Simulates the inline_data attribute of a Live API Part."""

    def __init__(self, data: bytes | str) -> None:
        self.data = data
        self.mime_type = "audio/pcm"


class _MockPart:
    def __init__(self, audio_data: bytes | str | None = None, text: str | None = None) -> None:
        self.inline_data = _MockInlineData(audio_data) if audio_data else None
        self.text = text


class _MockModelTurn:
    def __init__(self, parts: list[_MockPart]) -> None:
        self.parts = parts


class _MockServerContent:
    def __init__(
        self,
        model_turn: _MockModelTurn | None = None,
        turn_complete: bool = False,
    ) -> None:
        self.model_turn = model_turn
        self.turn_complete = turn_complete


class _MockMessage:
    def __init__(self, server_content: _MockServerContent | None = None) -> None:
        self.server_content = server_content


def _mock_genai_client(
    generate_text: str = "[]",
    audio_chunks: list[bytes] | None = None,
) -> MagicMock:
    """Build a mock google.genai.Client with text generation and live connect."""
    client = MagicMock()

    # Mock aio.models.generate_content
    mock_response = MagicMock()
    mock_response.text = generate_text
    client.aio.models.generate_content = AsyncMock(return_value=mock_response)

    # Mock aio.live.connect as async context manager
    chunks = audio_chunks or [b"\x00\x01" * 100]
    messages = []
    for chunk_data in chunks:
        msg = _MockMessage(
            _MockServerContent(
                model_turn=_MockModelTurn([_MockPart(audio_data=chunk_data)])
            )
        )
        messages.append(msg)
    # Add turn_complete message
    messages.append(_MockMessage(_MockServerContent(turn_complete=True)))

    async def _mock_receive() -> AsyncIterator[_MockMessage]:
        for m in messages:
            yield m

    mock_session = MagicMock()
    mock_session.send_content = AsyncMock()
    mock_session.receive = _mock_receive

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    client.aio.live.connect = MagicMock(return_value=mock_ctx)

    return client


@pytest.fixture
def affective() -> AffectiveNarrator:
    return AffectiveNarrator()


@pytest.fixture
def client() -> MagicMock:
    script_json = json.dumps([
        {"text": "Welcome to the Colosseum.", "duration": 5.0},
        {"text": "Built in 80 AD, it is a marvel of engineering.", "duration": 8.0},
    ])
    return _mock_genai_client(generate_text=script_json)


@pytest.fixture
def engine(client: MagicMock) -> NarrationEngine:
    return NarrationEngine(client=client)


# ══════════════════════════════════════════════════════════════
# AffectiveNarrator tests (Task 9.2, Req 11.1–11.6)
# ══════════════════════════════════════════════════════════════


class TestAffectiveNarrator:
    """Test suite for AffectiveNarrator tone detection and parameter mapping."""

    # ── Tone detection by location type ───────────────────────

    def test_respectful_for_memorial(self, affective: AffectiveNarrator) -> None:
        """Req 11.2: war memorials → respectful tone."""
        ctx = _make_context(place_types=["war_memorial", "historic_site"])
        assert affective.determine_emotional_tone(ctx) == EmotionalTone.RESPECTFUL

    def test_respectful_for_cemetery(self, affective: AffectiveNarrator) -> None:
        ctx = _make_context(place_types=["cemetery"])
        assert affective.determine_emotional_tone(ctx) == EmotionalTone.RESPECTFUL

    def test_respectful_for_place_of_worship(self, affective: AffectiveNarrator) -> None:
        ctx = _make_context(place_types=["place_of_worship"])
        assert affective.determine_emotional_tone(ctx) == EmotionalTone.RESPECTFUL

    def test_contemplative_for_museum(self, affective: AffectiveNarrator) -> None:
        """Req 11.4: museums → contemplative tone."""
        ctx = _make_context(place_types=["museum"])
        assert affective.determine_emotional_tone(ctx) == EmotionalTone.CONTEMPLATIVE

    def test_contemplative_for_library(self, affective: AffectiveNarrator) -> None:
        ctx = _make_context(place_types=["library", "book_store"])
        assert affective.determine_emotional_tone(ctx) == EmotionalTone.CONTEMPLATIVE

    def test_contemplative_for_university(self, affective: AffectiveNarrator) -> None:
        ctx = _make_context(place_types=["university"])
        assert affective.determine_emotional_tone(ctx) == EmotionalTone.CONTEMPLATIVE

    def test_enthusiastic_for_park(self, affective: AffectiveNarrator) -> None:
        """Req 11.3: parks/festivals → enthusiastic tone."""
        ctx = _make_context(place_types=["park", "tourist_attraction"])
        assert affective.determine_emotional_tone(ctx) == EmotionalTone.ENTHUSIASTIC

    def test_enthusiastic_for_amusement_park(self, affective: AffectiveNarrator) -> None:
        ctx = _make_context(place_types=["amusement_park"])
        assert affective.determine_emotional_tone(ctx) == EmotionalTone.ENTHUSIASTIC

    def test_neutral_for_unknown_type(self, affective: AffectiveNarrator) -> None:
        ctx = _make_context(place_types=["gas_station"], place_description="", topic=None)
        assert affective.determine_emotional_tone(ctx) == EmotionalTone.NEUTRAL

    # ── Tone detection by topic sentiment ─────────────────────

    def test_respectful_for_tragic_topic(self, affective: AffectiveNarrator) -> None:
        ctx = _make_context(
            place_types=[],
            topic="The tragedy of war and destruction that befell the city",
        )
        assert affective.determine_emotional_tone(ctx) == EmotionalTone.RESPECTFUL

    def test_enthusiastic_for_celebration_topic(self, affective: AffectiveNarrator) -> None:
        ctx = _make_context(
            place_types=[],
            topic="A celebration of achievement and innovation in science",
        )
        assert affective.determine_emotional_tone(ctx) == EmotionalTone.ENTHUSIASTIC

    def test_contemplative_for_neutral_long_topic(self, affective: AffectiveNarrator) -> None:
        ctx = _make_context(
            place_types=[],
            topic="The history of this region spans many centuries of change",
        )
        assert affective.determine_emotional_tone(ctx) == EmotionalTone.CONTEMPLATIVE

    # ── Priority: location types over topic sentiment ─────────

    def test_location_type_takes_priority_over_topic(self, affective: AffectiveNarrator) -> None:
        """Location types should win over topic sentiment."""
        ctx = _make_context(
            place_types=["cemetery"],
            topic="A celebration of achievement and innovation",
        )
        assert affective.determine_emotional_tone(ctx) == EmotionalTone.RESPECTFUL

    # ── Sentiment analysis ────────────────────────────────────

    def test_negative_sentiment(self, affective: AffectiveNarrator) -> None:
        assert AffectiveNarrator.analyze_sentiment("war and tragedy") < 0

    def test_positive_sentiment(self, affective: AffectiveNarrator) -> None:
        assert AffectiveNarrator.analyze_sentiment("celebration and victory") > 0

    def test_neutral_sentiment_no_keywords(self, affective: AffectiveNarrator) -> None:
        assert AffectiveNarrator.analyze_sentiment("a sunny day") == 0.0

    def test_mixed_sentiment(self, affective: AffectiveNarrator) -> None:
        score = AffectiveNarrator.analyze_sentiment("war and victory")
        assert -1.0 <= score <= 1.0

    # ── Voice parameter mapping (Req 11.5) ────────────────────

    def test_adapt_tone_returns_voice_parameters(self, affective: AffectiveNarrator) -> None:
        params = affective.adapt_tone(EmotionalTone.RESPECTFUL)
        assert isinstance(params, VoiceParameters)

    def test_respectful_params_slower(self, affective: AffectiveNarrator) -> None:
        params = affective.adapt_tone(EmotionalTone.RESPECTFUL)
        assert params.speaking_rate < 1.0
        assert params.pitch < 0.0

    def test_enthusiastic_params_faster(self, affective: AffectiveNarrator) -> None:
        params = affective.adapt_tone(EmotionalTone.ENTHUSIASTIC)
        assert params.speaking_rate > 1.0
        assert params.pitch > 0.0

    def test_contemplative_params(self, affective: AffectiveNarrator) -> None:
        params = affective.adapt_tone(EmotionalTone.CONTEMPLATIVE)
        assert params.speaking_rate < 1.0

    def test_neutral_params_default(self, affective: AffectiveNarrator) -> None:
        params = affective.adapt_tone(EmotionalTone.NEUTRAL)
        assert params.speaking_rate == 1.0
        assert params.pitch == 0.0

    def test_each_tone_has_different_voice(self, affective: AffectiveNarrator) -> None:
        voices = {
            affective.adapt_tone(t).voice_name for t in EmotionalTone
        }
        assert len(voices) == 4, "Each tone should map to a unique voice"

    # ── Tone instructions ─────────────────────────────────────

    def test_tone_instruction_nonempty(self, affective: AffectiveNarrator) -> None:
        for tone in EmotionalTone:
            instr = affective.get_tone_instruction(tone)
            assert isinstance(instr, str)
            assert len(instr) > 20

    # ── Tonal consistency (Req 11.6) ──────────────────────────

    def test_same_context_gives_same_tone(self, affective: AffectiveNarrator) -> None:
        """Req 11.6: tonal consistency within a session."""
        ctx = _make_context(place_types=["museum"])
        results = [affective.determine_emotional_tone(ctx) for _ in range(10)]
        assert len(set(results)) == 1


# ══════════════════════════════════════════════════════════════
# NarrationEngine tests (Task 9.1, Req 3.1, 3.2, 5.2)
# ══════════════════════════════════════════════════════════════


class TestNarrationEngineScript:
    """Test suite for script generation."""

    @pytest.mark.asyncio
    async def test_generate_script_returns_script(self, engine: NarrationEngine) -> None:
        ctx = _make_context()
        script = await engine.generate_script(ctx)
        assert isinstance(script, NarrationScript)
        assert len(script.segments) > 0

    @pytest.mark.asyncio
    async def test_generate_script_sets_language(self, engine: NarrationEngine) -> None:
        ctx = _make_context(language="fr")
        script = await engine.generate_script(ctx)
        assert script.language == "fr"

    @pytest.mark.asyncio
    async def test_generate_script_sets_depth(self, engine: NarrationEngine) -> None:
        ctx = _make_context()
        script = await engine.generate_script(ctx, depth_level=DepthLevel.EXPERT)
        assert script.depth_level == DepthLevel.EXPERT

    @pytest.mark.asyncio
    async def test_generate_script_sets_tone(self, engine: NarrationEngine) -> None:
        ctx = _make_context(place_types=["museum"])
        script = await engine.generate_script(ctx)
        assert script.tone == EmotionalTone.CONTEMPLATIVE

    @pytest.mark.asyncio
    async def test_generate_script_total_duration(self, engine: NarrationEngine) -> None:
        ctx = _make_context()
        script = await engine.generate_script(ctx)
        expected = sum(s.duration for s in script.segments)
        assert script.total_duration == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_script_segments_have_text(self, engine: NarrationEngine) -> None:
        ctx = _make_context()
        script = await engine.generate_script(ctx)
        for seg in script.segments:
            assert seg.text
            assert seg.duration > 0

    @pytest.mark.asyncio
    async def test_generate_script_calls_model(self, engine: NarrationEngine, client: MagicMock) -> None:
        ctx = _make_context()
        await engine.generate_script(ctx)
        client.aio.models.generate_content.assert_awaited_once()
        call_kwargs = client.aio.models.generate_content.call_args
        assert SCRIPT_MODEL in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_generate_script_timeout_fallback(self) -> None:
        """When model times out, engine returns a fallback segment."""
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(
            side_effect=asyncio.TimeoutError()
        )
        engine = NarrationEngine(client=client)
        ctx = _make_context()
        script = await engine.generate_script(ctx)
        assert len(script.segments) == 1
        assert "Colosseum" in script.segments[0].text

    @pytest.mark.asyncio
    async def test_generate_script_malformed_json_fallback(self) -> None:
        """When model returns unparseable text, engine still returns segments."""
        client = _mock_genai_client(generate_text="This is not JSON")
        engine = NarrationEngine(client=client)
        ctx = _make_context()
        script = await engine.generate_script(ctx)
        assert len(script.segments) >= 1

    @pytest.mark.asyncio
    async def test_generate_script_markdown_fenced_json(self) -> None:
        """Engine strips markdown code fences from model response."""
        fenced = '```json\n[{"text": "Hello world.", "duration": 3.0}]\n```'
        client = _mock_genai_client(generate_text=fenced)
        engine = NarrationEngine(client=client)
        ctx = _make_context()
        script = await engine.generate_script(ctx)
        assert script.segments[0].text == "Hello world."


class TestNarrationEngineSpeech:
    """Test suite for speech synthesis via Live API."""

    @pytest.mark.asyncio
    async def test_synthesize_speech_yields_chunks(self, engine: NarrationEngine) -> None:
        script = _make_script()
        chunks = []
        async for chunk in engine.synthesize_speech(script):
            chunks.append(chunk)
        # Should have at least one audio chunk + one final chunk
        assert len(chunks) >= 1

    @pytest.mark.asyncio
    async def test_synthesize_speech_final_chunk(self, engine: NarrationEngine) -> None:
        script = _make_script()
        chunks = []
        async for chunk in engine.synthesize_speech(script):
            chunks.append(chunk)
        assert chunks[-1].is_final

    @pytest.mark.asyncio
    async def test_synthesize_speech_sequence_numbers(self, engine: NarrationEngine) -> None:
        script = _make_script()
        sequences = []
        async for chunk in engine.synthesize_speech(script):
            sequences.append(chunk.sequence)
        # Sequences should be monotonically increasing
        for i in range(1, len(sequences)):
            assert sequences[i] >= sequences[i - 1]

    @pytest.mark.asyncio
    async def test_synthesize_speech_uses_live_model(
        self, engine: NarrationEngine, client: MagicMock,
    ) -> None:
        script = _make_script()
        async for _ in engine.synthesize_speech(script):
            pass
        client.aio.live.connect.assert_called_once()
        call_kwargs = client.aio.live.connect.call_args
        assert LIVE_AUDIO_MODEL in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_synthesize_speech_sends_text(
        self, engine: NarrationEngine, client: MagicMock,
    ) -> None:
        script = _make_script(text="Hello world.")
        async for _ in engine.synthesize_speech(script):
            pass
        # The mock session's send_content should have been called
        mock_session = await client.aio.live.connect().__aenter__()
        mock_session.send_content.assert_awaited()

    @pytest.mark.asyncio
    async def test_synthesize_speech_applies_voice(self, engine: NarrationEngine, client: MagicMock) -> None:
        script = _make_script(tone=EmotionalTone.ENTHUSIASTIC)
        async for _ in engine.synthesize_speech(script):
            pass
        call_kwargs = client.aio.live.connect.call_args
        config = call_kwargs[1].get("config") or call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs[1]["config"]
        assert config["speech_config"]["voice_config"]["prebuilt_voice_config"]["voice_name"] == "Puck"

    @pytest.mark.asyncio
    async def test_synthesize_speech_collected(self, engine: NarrationEngine) -> None:
        script = _make_script()
        result = await engine.synthesize_speech_collected(script)
        assert result.chunk_count >= 1
        assert result.transcript
        assert result.tone == EmotionalTone.NEUTRAL

    @pytest.mark.asyncio
    async def test_synthesize_speech_error_graceful(self) -> None:
        """Speech synthesis errors should not raise — just stop yielding."""
        client = MagicMock()
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(side_effect=RuntimeError("API error"))
        mock_ctx.__aexit__ = AsyncMock(return_value=False)
        client.aio.live.connect = MagicMock(return_value=mock_ctx)
        engine = NarrationEngine(client=client)
        script = _make_script()
        chunks = []
        async for chunk in engine.synthesize_speech(script):
            chunks.append(chunk)
        assert len(chunks) == 0

    @pytest.mark.asyncio
    async def test_synthesize_speech_base64_audio(self) -> None:
        """Engine should decode base64 audio strings from the API."""
        import base64 as b64

        raw = b"\x01\x02\x03\x04"
        encoded = b64.b64encode(raw).decode()
        client = _mock_genai_client(audio_chunks=[encoded])
        engine = NarrationEngine(client=client)
        script = _make_script()
        chunks = []
        async for chunk in engine.synthesize_speech(script):
            if chunk.data:
                chunks.append(chunk)
        assert len(chunks) >= 1
        assert chunks[0].data == raw


class TestNarrationEngineTranslation:
    """Test suite for script translation."""

    @pytest.mark.asyncio
    async def test_translate_same_language_noop(self, engine: NarrationEngine) -> None:
        script = _make_script()
        result = await engine.translate_script(script, "en")
        assert result is script  # Same object returned

    @pytest.mark.asyncio
    async def test_translate_calls_model(self, engine: NarrationEngine, client: MagicMock) -> None:
        # Set up the mock to return translated JSON
        translated = json.dumps([{"text": "Bienvenue au Colisée.", "duration": 5.0}])
        client.aio.models.generate_content = AsyncMock(
            return_value=MagicMock(text=translated)
        )
        script = _make_script()
        result = await engine.translate_script(script, "fr")
        assert result.language == "fr"
        assert result.segments[0].text == "Bienvenue au Colisée."

    @pytest.mark.asyncio
    async def test_translate_failure_returns_original(self) -> None:
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(
            side_effect=RuntimeError("API error")
        )
        engine = NarrationEngine(client=client)
        script = _make_script()
        result = await engine.translate_script(script, "de")
        # Should return original on failure
        assert result.segments[0].text == script.segments[0].text


class TestNarrationEngineFullPipeline:
    """Test the generate_narration end-to-end convenience method."""

    @pytest.mark.asyncio
    async def test_generate_narration_streams_audio(self) -> None:
        script_json = json.dumps([{"text": "Hello.", "duration": 2.0}])
        client = _mock_genai_client(
            generate_text=script_json,
            audio_chunks=[b"\x00" * 200],
        )
        engine = NarrationEngine(client=client)
        ctx = _make_context()
        chunks = []
        async for chunk in engine.generate_narration(ctx):
            chunks.append(chunk)
        assert len(chunks) >= 1


# ══════════════════════════════════════════════════════════════
# Model tests
# ══════════════════════════════════════════════════════════════


class TestModels:
    """Test data model validation and defaults."""

    def test_narration_context_defaults(self) -> None:
        ctx = NarrationContext()
        assert ctx.mode == "sight"
        assert ctx.language == "en"
        assert ctx.depth_level == DepthLevel.EXPLORER

    def test_narration_segment_auto_id(self) -> None:
        seg = NarrationSegment(text="Hello")
        assert seg.id
        assert len(seg.id) == 12

    def test_narration_script_defaults(self) -> None:
        script = NarrationScript()
        assert script.language == "en"
        assert script.depth_level == DepthLevel.EXPLORER
        assert script.tone == EmotionalTone.NEUTRAL

    def test_audio_chunk_fields(self) -> None:
        chunk = AudioChunk(data=b"\x00\x01")
        assert chunk.sequence == 0
        assert not chunk.is_final

    def test_voice_parameters_validation(self) -> None:
        params = VoiceParameters(speaking_rate=1.5, pitch=3.0, volume_gain_db=-2.0)
        assert params.speaking_rate == 1.5

    def test_emotional_tone_values(self) -> None:
        assert set(EmotionalTone) == {
            EmotionalTone.RESPECTFUL,
            EmotionalTone.ENTHUSIASTIC,
            EmotionalTone.CONTEMPLATIVE,
            EmotionalTone.NEUTRAL,
        }

    def test_depth_level_values(self) -> None:
        assert set(DepthLevel) == {
            DepthLevel.EXPLORER,
            DepthLevel.SCHOLAR,
            DepthLevel.EXPERT,
        }

    def test_narration_result_fields(self) -> None:
        from backend.services.narration_engine.models import NarrationResult

        result = NarrationResult(script=NarrationScript())
        assert result.chunk_count == 0
        assert result.duration == 0.0
