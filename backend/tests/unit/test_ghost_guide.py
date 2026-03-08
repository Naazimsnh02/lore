"""Unit tests for the Multilingual Ghost Guide.

Tests cover:
  - GhostGuide.generate_in_language (Task 18.1)
  - GhostGuide.switch_language mid-session (Req 17.5, 17.6)
  - Cultural style retrieval (Req 17.4)
  - Voice selection per language
  - 24-language support verification (Req 17.1)
  - Graceful fallback for unsupported languages
  - Session language tracking
  - Context enrichment preserves original fields

Requirements validated: 17.1–17.6.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from backend.services.narration_engine.engine import NarrationEngine
from backend.services.narration_engine.ghost_guide import (
    CULTURAL_STYLES,
    SUPPORTED_LANGUAGES,
    GhostGuide,
    _DEFAULT_CULTURAL_STYLE,
    _DEFAULT_VOICE,
    _LANGUAGE_VOICES,
)
from backend.services.narration_engine.models import (
    DepthLevel,
    EmotionalTone,
    NarrationContext,
    NarrationScript,
    NarrationSegment,
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
        "session_id": "session-001",
    }
    defaults.update(overrides)
    return NarrationContext(**defaults)


def _make_script(
    text: str = "Welcome to the Colosseum.",
    tone: EmotionalTone = EmotionalTone.NEUTRAL,
    segments: int = 3,
    language: str = "en",
) -> NarrationScript:
    """Create a simple NarrationScript with multiple segments."""
    segs = [
        NarrationSegment(
            text=f"{text} Segment {i + 1}.",
            duration=5.0,
            tone=tone,
        )
        for i in range(segments)
    ]
    return NarrationScript(
        segments=segs,
        total_duration=5.0 * segments,
        language=language,
        depth_level=DepthLevel.EXPLORER,
        tone=tone,
    )


def _make_translated_script(
    original: NarrationScript,
    target_language: str,
) -> NarrationScript:
    """Create a mock translated version of a script."""
    translated_segs = [
        NarrationSegment(
            text=f"[{target_language}] {seg.text}",
            duration=seg.duration,
            tone=seg.tone,
        )
        for seg in original.segments
    ]
    return NarrationScript(
        segments=translated_segs,
        total_duration=original.total_duration,
        language=target_language,
        depth_level=original.depth_level,
        tone=original.tone,
    )


def _mock_engine() -> MagicMock:
    """Create a mock NarrationEngine with async methods."""
    engine = MagicMock(spec=NarrationEngine)

    # generate_script returns a script in the requested language
    async def _gen_script(context, depth_level=None):
        segs = [
            NarrationSegment(
                text=f"Narration about {context.place_name or context.topic or 'topic'}.",
                duration=10.0,
                tone=EmotionalTone.NEUTRAL,
            ),
        ]
        return NarrationScript(
            segments=segs,
            total_duration=10.0,
            language=context.language,
            depth_level=context.depth_level,
            tone=EmotionalTone.NEUTRAL,
        )

    engine.generate_script = AsyncMock(side_effect=_gen_script)

    # translate_script returns a translated copy
    async def _translate(script, target_lang):
        return _make_translated_script(script, target_lang)

    engine.translate_script = AsyncMock(side_effect=_translate)

    return engine


@pytest.fixture
def engine():
    return _mock_engine()


@pytest.fixture
def guide(engine):
    return GhostGuide(narration_engine=engine, default_language="en")


# ── Test: 24 language support (Req 17.1) ─────────────────────


class TestSupportedLanguages:
    """Verify all 24 languages are supported."""

    def test_exactly_24_languages(self):
        assert len(SUPPORTED_LANGUAGES) == 24

    def test_all_expected_codes_present(self):
        expected = {
            "en", "es", "fr", "de", "it", "pt", "nl", "ru",
            "zh", "ja", "ko", "ar", "hi", "tr", "pl", "sv",
            "da", "no", "fi", "el", "th", "vi", "id", "he",
        }
        assert set(SUPPORTED_LANGUAGES.keys()) == expected

    def test_is_supported_for_all_languages(self, guide):
        for code in SUPPORTED_LANGUAGES:
            assert guide.is_supported(code) is True

    def test_is_not_supported_unknown_code(self, guide):
        assert guide.is_supported("xx") is False
        assert guide.is_supported("") is False
        assert guide.is_supported("zz") is False

    def test_supported_languages_property(self, guide):
        langs = guide.supported_languages
        assert langs == SUPPORTED_LANGUAGES
        # Should be a copy, not the original
        langs["xx"] = "FakeLanguage"
        assert "xx" not in guide.supported_languages


# ── Test: Cultural styles (Req 17.4) ─────────────────────────


class TestCulturalStyles:
    """Verify cultural style retrieval."""

    def test_japanese_cultural_style(self, guide):
        style = guide.get_cultural_style("ja")
        assert "です/ます" in style
        assert "kigo" in style

    def test_arabic_cultural_style(self, guide):
        style = guide.get_cultural_style("ar")
        assert "Modern Standard Arabic" in style
        assert "Islamic Golden Age" in style

    def test_chinese_cultural_style(self, guide):
        style = guide.get_cultural_style("zh")
        assert "Simplified Chinese" in style

    def test_hindi_cultural_style(self, guide):
        style = guide.get_cultural_style("hi")
        assert "Devanagari" in style

    def test_korean_cultural_style(self, guide):
        style = guide.get_cultural_style("ko")
        assert "합니다" in style

    def test_french_cultural_style(self, guide):
        style = guide.get_cultural_style("fr")
        assert "literary French" in style

    def test_german_cultural_style(self, guide):
        style = guide.get_cultural_style("de")
        assert "Hochdeutsch" in style

    def test_spanish_cultural_style(self, guide):
        style = guide.get_cultural_style("es")
        assert "Latin American Spanish" in style

    def test_italian_cultural_style(self, guide):
        style = guide.get_cultural_style("it")
        assert "Renaissance" in style

    def test_portuguese_cultural_style(self, guide):
        style = guide.get_cultural_style("pt")
        assert "Brazilian Portuguese" in style

    def test_russian_cultural_style(self, guide):
        style = guide.get_cultural_style("ru")
        assert "Russian" in style

    def test_turkish_cultural_style(self, guide):
        style = guide.get_cultural_style("tr")
        assert "Ottoman" in style

    def test_all_cultural_styles_non_empty(self, guide):
        for lang in SUPPORTED_LANGUAGES:
            style = guide.get_cultural_style(lang)
            assert isinstance(style, str)
            assert len(style) > 10

    def test_unsupported_language_gets_default_style(self, guide):
        style = guide.get_cultural_style("xx")
        assert style == _DEFAULT_CULTURAL_STYLE
        assert "culturally appropriate" in style


# ── Test: Voice selection ─────────────────────────────────────


class TestVoiceSelection:
    """Verify voice-per-language mapping."""

    def test_english_voice(self, guide):
        assert guide.get_voice_for_language("en") == "Kore"

    def test_japanese_voice(self, guide):
        assert guide.get_voice_for_language("ja") == "Enceladus"

    def test_arabic_voice(self, guide):
        assert guide.get_voice_for_language("ar") == "Charon"

    def test_hindi_voice(self, guide):
        assert guide.get_voice_for_language("hi") == "Puck"

    def test_all_supported_languages_have_voice(self, guide):
        for code in SUPPORTED_LANGUAGES:
            voice = guide.get_voice_for_language(code)
            assert voice in {"Kore", "Charon", "Puck", "Enceladus"}

    def test_unsupported_language_gets_default_voice(self, guide):
        assert guide.get_voice_for_language("xx") == _DEFAULT_VOICE


# ── Test: generate_in_language (Req 17.2) ─────────────────────


class TestGenerateInLanguage:
    """Test direct generation in a target language."""

    @pytest.mark.asyncio
    async def test_generate_in_english(self, guide, engine):
        ctx = _make_context(language="en")
        script = await guide.generate_in_language(ctx, "en")

        assert script.language == "en"
        engine.generate_script.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generate_in_japanese(self, guide, engine):
        ctx = _make_context(language="en")
        script = await guide.generate_in_language(ctx, "ja")

        assert script.language == "ja"
        # Verify the context was enriched with Japanese cultural style
        call_args = engine.generate_script.call_args
        enriched_ctx = call_args[0][0]
        assert enriched_ctx.language == "ja"
        assert "です/ます" in enriched_ctx.custom_instructions

    @pytest.mark.asyncio
    async def test_generate_in_arabic(self, guide, engine):
        ctx = _make_context(language="en")
        script = await guide.generate_in_language(ctx, "ar")

        assert script.language == "ar"
        call_args = engine.generate_script.call_args
        enriched_ctx = call_args[0][0]
        assert "Modern Standard Arabic" in enriched_ctx.custom_instructions

    @pytest.mark.asyncio
    async def test_unsupported_language_falls_back(self, guide, engine):
        ctx = _make_context(language="en")
        script = await guide.generate_in_language(ctx, "xx")

        # Should fall back to default (English)
        assert script.language == "en"
        call_args = engine.generate_script.call_args
        enriched_ctx = call_args[0][0]
        assert enriched_ctx.language == "en"

    @pytest.mark.asyncio
    async def test_preserves_context_fields(self, guide, engine):
        ctx = _make_context(
            place_name="Eiffel Tower",
            topic="French architecture",
            depth_level=DepthLevel.SCHOLAR,
            session_id="s-42",
        )
        await guide.generate_in_language(ctx, "fr")

        call_args = engine.generate_script.call_args
        enriched_ctx = call_args[0][0]
        assert enriched_ctx.place_name == "Eiffel Tower"
        assert enriched_ctx.topic == "French architecture"
        assert enriched_ctx.depth_level == DepthLevel.SCHOLAR
        assert enriched_ctx.session_id == "s-42"

    @pytest.mark.asyncio
    async def test_merges_existing_custom_instructions(self, guide, engine):
        ctx = _make_context(custom_instructions="Speak slowly.")
        await guide.generate_in_language(ctx, "ja")

        call_args = engine.generate_script.call_args
        enriched_ctx = call_args[0][0]
        assert "Speak slowly." in enriched_ctx.custom_instructions
        assert "です/ます" in enriched_ctx.custom_instructions

    @pytest.mark.asyncio
    async def test_tracks_session_language(self, guide):
        ctx = _make_context(session_id="s-100")
        await guide.generate_in_language(ctx, "fr")

        assert guide.get_session_language("s-100") == "fr"

    @pytest.mark.asyncio
    async def test_no_session_tracking_without_session_id(self, guide):
        ctx = _make_context(session_id=None)
        await guide.generate_in_language(ctx, "de")
        # Should not crash; no session to track
        assert guide.get_session_language("nonexistent") == "en"

    @pytest.mark.asyncio
    async def test_does_not_mutate_original_context(self, guide):
        ctx = _make_context(language="en", custom_instructions=None)
        await guide.generate_in_language(ctx, "ja")

        # Original should be unchanged
        assert ctx.language == "en"
        assert ctx.custom_instructions is None


# ── Test: switch_language (Req 17.5, 17.6) ───────────────────


class TestSwitchLanguage:
    """Test mid-session language switching."""

    @pytest.mark.asyncio
    async def test_switch_from_english_to_french(self, guide, engine):
        script = _make_script(segments=5, language="en")
        new_script = await guide.switch_language(script, "fr", stream_position=2)

        assert new_script.language == "fr"
        # Should have translated only remaining 3 segments
        engine.translate_script.assert_awaited_once()
        call_args = engine.translate_script.call_args
        partial = call_args[0][0]
        assert len(partial.segments) == 3

    @pytest.mark.asyncio
    async def test_switch_continues_from_position(self, guide, engine):
        script = _make_script(segments=4, language="en")
        new_script = await guide.switch_language(script, "es", stream_position=3)

        call_args = engine.translate_script.call_args
        partial = call_args[0][0]
        assert len(partial.segments) == 1  # Only segment 3 remains

    @pytest.mark.asyncio
    async def test_switch_from_position_zero(self, guide, engine):
        script = _make_script(segments=3, language="en")
        new_script = await guide.switch_language(script, "de", stream_position=0)

        call_args = engine.translate_script.call_args
        partial = call_args[0][0]
        assert len(partial.segments) == 3  # All segments

    @pytest.mark.asyncio
    async def test_switch_past_end_returns_empty(self, guide):
        script = _make_script(segments=2, language="en")
        new_script = await guide.switch_language(script, "ja", stream_position=5)

        assert len(new_script.segments) == 0
        assert new_script.language == "ja"
        assert new_script.total_duration == 0.0

    @pytest.mark.asyncio
    async def test_switch_to_same_language_slices_only(self, guide, engine):
        script = _make_script(segments=4, language="fr")
        new_script = await guide.switch_language(script, "fr", stream_position=2)

        # Should NOT call translate_script
        engine.translate_script.assert_not_awaited()
        assert new_script.language == "fr"
        assert len(new_script.segments) == 2

    @pytest.mark.asyncio
    async def test_switch_to_unsupported_falls_back(self, guide, engine):
        script = _make_script(segments=3, language="en")
        new_script = await guide.switch_language(script, "xx", stream_position=0)

        # Falls back to "en", same as current → just slices
        engine.translate_script.assert_not_awaited()
        assert new_script.language == "en"
        assert len(new_script.segments) == 3

    @pytest.mark.asyncio
    async def test_switch_preserves_depth_and_tone(self, guide):
        script = NarrationScript(
            segments=[
                NarrationSegment(text="Seg1", duration=5.0, tone=EmotionalTone.CONTEMPLATIVE),
                NarrationSegment(text="Seg2", duration=5.0, tone=EmotionalTone.CONTEMPLATIVE),
            ],
            total_duration=10.0,
            language="en",
            depth_level=DepthLevel.EXPERT,
            tone=EmotionalTone.CONTEMPLATIVE,
        )
        new_script = await guide.switch_language(script, "it", stream_position=0)

        assert new_script.depth_level == DepthLevel.EXPERT
        assert new_script.tone == EmotionalTone.CONTEMPLATIVE


# ── Test: Session language tracking ───────────────────────────


class TestSessionTracking:
    """Test session language state management."""

    def test_default_session_language(self, guide):
        assert guide.get_session_language("unknown-session") == "en"

    def test_set_session_language(self, guide):
        guide.set_session_language("s-1", "ja")
        assert guide.get_session_language("s-1") == "ja"

    def test_set_unsupported_session_language_falls_back(self, guide):
        guide.set_session_language("s-1", "xx")
        assert guide.get_session_language("s-1") == "en"

    def test_clear_session(self, guide):
        guide.set_session_language("s-1", "fr")
        guide.clear_session("s-1")
        assert guide.get_session_language("s-1") == "en"

    def test_clear_nonexistent_session_no_error(self, guide):
        guide.clear_session("nonexistent")  # Should not raise

    @pytest.mark.asyncio
    async def test_generate_updates_session_language(self, guide):
        ctx = _make_context(session_id="s-track")
        await guide.generate_in_language(ctx, "ko")
        assert guide.get_session_language("s-track") == "ko"

        await guide.generate_in_language(ctx, "de")
        assert guide.get_session_language("s-track") == "de"


# ── Test: Edge cases and graceful degradation ─────────────────


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_empty_script_switch(self, guide):
        empty_script = NarrationScript(
            segments=[],
            total_duration=0.0,
            language="en",
        )
        result = await guide.switch_language(empty_script, "fr", stream_position=0)
        assert len(result.segments) == 0
        assert result.language == "fr"

    @pytest.mark.asyncio
    async def test_single_segment_switch_at_zero(self, guide, engine):
        script = _make_script(segments=1, language="en")
        result = await guide.switch_language(script, "ja", stream_position=0)
        assert result.language == "ja"
        engine.translate_script.assert_awaited_once()

    def test_custom_default_language(self):
        engine = _mock_engine()
        guide = GhostGuide(narration_engine=engine, default_language="es")
        assert guide.get_session_language("any") == "es"

    @pytest.mark.asyncio
    async def test_engine_failure_propagates(self):
        engine = _mock_engine()
        engine.generate_script = AsyncMock(side_effect=RuntimeError("API error"))
        guide = GhostGuide(narration_engine=engine)

        ctx = _make_context()
        with pytest.raises(RuntimeError, match="API error"):
            await guide.generate_in_language(ctx, "en")

    @pytest.mark.asyncio
    async def test_translation_failure_propagates(self):
        engine = _mock_engine()
        engine.translate_script = AsyncMock(side_effect=RuntimeError("Translation error"))
        guide = GhostGuide(narration_engine=engine)

        script = _make_script(segments=2, language="en")
        with pytest.raises(RuntimeError, match="Translation error"):
            await guide.switch_language(script, "fr", stream_position=0)

    def test_enriched_context_has_all_place_types(self, guide):
        """Ensure enrichment copies list fields correctly."""
        ctx = _make_context(
            place_types=["museum", "tourist_attraction"],
            previous_topics=["Roman Empire", "Gladiators"],
        )
        enriched = guide._enrich_context(ctx, "it")
        assert enriched.place_types == ["museum", "tourist_attraction"]
        assert enriched.previous_topics == ["Roman Empire", "Gladiators"]
        # Ensure they are copies, not references
        enriched.place_types.append("new_type")
        assert "new_type" not in ctx.place_types
