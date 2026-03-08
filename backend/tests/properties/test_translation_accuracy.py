"""Property test: Language Translation Accuracy Invariant.

Feature: lore-multimodal-documentary-app
Property 12: Language Translation Accuracy Invariant

Validates: Requirements 17.2, 17.3 — translated content preserves factual
accuracy.  Key facts (proper nouns, dates, numbers) must survive translation.

Strategy:
  - Generate random narration scripts containing factual data (names, dates,
    numbers).
  - Translate via the GhostGuide / NarrationEngine mock.
  - Verify that the mock translator (which simulates a well-behaved LLM)
    preserves the factual entities.

Since we cannot call a real LLM in property tests, we validate the *contract*:
  1. The GhostGuide always delegates to NarrationEngine.translate_script.
  2. The returned script has the correct target language set.
  3. Segment count is preserved (no segments dropped).
  4. Language switching from any position produces a script whose segment
     count equals the remaining segments.
  5. The GhostGuide enriches context with cultural style for the target
     language without losing existing context fields.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from backend.services.narration_engine.ghost_guide import (
    GhostGuide,
    SUPPORTED_LANGUAGES,
    CULTURAL_STYLES,
    _DEFAULT_CULTURAL_STYLE,
)
from backend.services.narration_engine.models import (
    DepthLevel,
    EmotionalTone,
    NarrationContext,
    NarrationScript,
    NarrationSegment,
)


# ── Hypothesis strategies ─────────────────────────────────────

language_code = st.sampled_from(list(SUPPORTED_LANGUAGES.keys()))

depth_level = st.sampled_from(list(DepthLevel))

emotional_tone = st.sampled_from(list(EmotionalTone))

# Factual names / numbers that must survive translation
_PROPER_NOUNS = [
    "Colosseum", "Eiffel Tower", "Great Wall", "Taj Mahal", "Pyramids",
    "Parthenon", "Machu Picchu", "Angkor Wat", "Petra", "Stonehenge",
]
_DATES = [
    "72 AD", "1889", "221 BC", "1632", "2560 BC",
    "438 BC", "1450", "802 AD", "312 BC", "3000 BC",
]

proper_noun = st.sampled_from(_PROPER_NOUNS)
date_str = st.sampled_from(_DATES)


@st.composite
def factual_segment(draw: st.DrawFn) -> NarrationSegment:
    """Generate a narration segment containing verifiable facts."""
    noun = draw(proper_noun)
    date = draw(date_str)
    number = draw(st.integers(min_value=1, max_value=10_000))
    tone = draw(emotional_tone)

    text = (
        f"The {noun}, built around {date}, spans {number} meters and "
        f"remains one of the most visited landmarks in the world."
    )
    duration = max(1.0, len(text.split()) / 2.5)

    return NarrationSegment(text=text, duration=duration, tone=tone)


@st.composite
def factual_script(draw: st.DrawFn) -> NarrationScript:
    """Generate a narration script with 1-5 factual segments."""
    num_segments = draw(st.integers(min_value=1, max_value=5))
    segments = [draw(factual_segment()) for _ in range(num_segments)]
    total_dur = sum(s.duration for s in segments)
    lang = draw(language_code)
    depth = draw(depth_level)
    tone = draw(emotional_tone)

    return NarrationScript(
        segments=segments,
        total_duration=total_dur,
        language=lang,
        depth_level=depth,
        tone=tone,
    )


@st.composite
def narration_context(draw: st.DrawFn) -> NarrationContext:
    """Generate a random NarrationContext."""
    noun = draw(proper_noun)
    lang = draw(language_code)
    depth = draw(depth_level)

    return NarrationContext(
        mode=draw(st.sampled_from(["sight", "voice", "lore"])),
        place_name=noun,
        place_description=f"Historic site: {noun}",
        place_types=draw(st.lists(
            st.sampled_from(["museum", "tourist_attraction", "historic_site", "park"]),
            min_size=0,
            max_size=3,
        )),
        language=lang,
        depth_level=depth,
        session_id=draw(st.text(min_size=4, max_size=12, alphabet="abcdef0123456789")),
        previous_topics=draw(st.lists(st.text(min_size=1, max_size=20), max_size=3)),
    )


# ── Mock engine factory ──────────────────────────────────────


def _mock_engine_preserving_facts() -> MagicMock:
    """Create a mock NarrationEngine whose translate_script preserves facts.

    The mock simulates a well-behaved translator: it prefixes each segment
    with a language tag but keeps all proper nouns, dates, and numbers.
    """
    engine = MagicMock()

    async def _generate_script(context, depth_level=None):
        segs = [
            NarrationSegment(
                text=f"Documentary about {context.place_name or 'topic'}.",
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

    async def _translate_script(script, target_lang):
        translated_segs = [
            NarrationSegment(
                text=f"[{target_lang}] {seg.text}",
                duration=seg.duration,
                tone=seg.tone,
            )
            for seg in script.segments
        ]
        return NarrationScript(
            segments=translated_segs,
            total_duration=script.total_duration,
            language=target_lang,
            depth_level=script.depth_level,
            tone=script.tone,
        )

    engine.generate_script = AsyncMock(side_effect=_generate_script)
    engine.translate_script = AsyncMock(side_effect=_translate_script)
    return engine


# ── Property tests ───────────────────────────────────────────


class TestTranslationAccuracyProperty:
    """Feature: lore-multimodal-documentary-app, Property 12:
    Language Translation Accuracy Invariant.

    For any factual narration script translated to any supported language,
    the factual content (proper nouns, dates, numbers) must be preserved.
    """

    @given(script=factual_script(), target=language_code)
    @settings(max_examples=120, deadline=2000)
    @pytest.mark.asyncio
    async def test_translation_preserves_facts(
        self, script: NarrationScript, target: str,
    ):
        """Translated script preserves all proper nouns, dates, and numbers."""
        engine = _mock_engine_preserving_facts()
        guide = GhostGuide(narration_engine=engine)

        assume(script.language != target)

        translated = await guide.switch_language(script, target, stream_position=0)

        # Collect facts from original
        for i, orig_seg in enumerate(script.segments):
            trans_seg = translated.segments[i]
            # Extract numbers from original
            orig_numbers = set(re.findall(r"\d+", orig_seg.text))
            trans_numbers = set(re.findall(r"\d+", trans_seg.text))
            assert orig_numbers == trans_numbers, (
                f"Numbers lost in translation: {orig_numbers} vs {trans_numbers}"
            )
            # Extract proper nouns (words from our known set)
            for noun in _PROPER_NOUNS:
                if noun in orig_seg.text:
                    assert noun in trans_seg.text, (
                        f"Proper noun '{noun}' lost in translation"
                    )

    @given(script=factual_script(), target=language_code)
    @settings(max_examples=120, deadline=2000)
    @pytest.mark.asyncio
    async def test_translation_preserves_segment_count(
        self, script: NarrationScript, target: str,
    ):
        """Translated script has the same number of segments as the original."""
        engine = _mock_engine_preserving_facts()
        guide = GhostGuide(narration_engine=engine)

        assume(script.language != target)

        translated = await guide.switch_language(script, target, stream_position=0)
        assert len(translated.segments) == len(script.segments)

    @given(script=factual_script(), target=language_code)
    @settings(max_examples=120, deadline=2000)
    @pytest.mark.asyncio
    async def test_translation_sets_target_language(
        self, script: NarrationScript, target: str,
    ):
        """Translated script has the target language code."""
        engine = _mock_engine_preserving_facts()
        guide = GhostGuide(narration_engine=engine)

        assume(script.language != target)

        translated = await guide.switch_language(script, target, stream_position=0)
        assert translated.language == target

    @given(
        script=factual_script(),
        target=language_code,
        position=st.integers(min_value=0, max_value=4),
    )
    @settings(max_examples=120, deadline=2000)
    @pytest.mark.asyncio
    async def test_switch_position_yields_correct_remaining_count(
        self, script: NarrationScript, target: str, position: int,
    ):
        """Language switch from position N yields len(segments) - N segments."""
        engine = _mock_engine_preserving_facts()
        guide = GhostGuide(narration_engine=engine)

        assume(script.language != target)

        translated = await guide.switch_language(script, target, stream_position=position)
        expected = max(0, len(script.segments) - position)
        assert len(translated.segments) == expected

    @given(ctx=narration_context(), target=language_code)
    @settings(max_examples=120, deadline=2000)
    @pytest.mark.asyncio
    async def test_generate_in_language_enriches_context(
        self, ctx: NarrationContext, target: str,
    ):
        """Generating in any language enriches context with cultural style."""
        engine = _mock_engine_preserving_facts()
        guide = GhostGuide(narration_engine=engine)

        script = await guide.generate_in_language(ctx, target)

        # Verify engine was called
        engine.generate_script.assert_awaited_once()

        # Verify the enriched context has cultural style
        call_args = engine.generate_script.call_args
        enriched = call_args[0][0]

        expected_style = guide.get_cultural_style(target)
        assert expected_style in enriched.custom_instructions

        # Verify original place_name is preserved
        assert enriched.place_name == ctx.place_name

    @given(lang=language_code)
    @settings(max_examples=100, deadline=500)
    def test_cultural_style_always_non_empty(self, lang: str):
        """Every supported language returns a non-empty cultural style."""
        guide = GhostGuide(narration_engine=MagicMock())
        style = guide.get_cultural_style(lang)
        assert isinstance(style, str)
        assert len(style) > 0

    @given(lang=language_code)
    @settings(max_examples=100, deadline=500)
    def test_voice_always_valid(self, lang: str):
        """Every supported language maps to a valid Gemini voice."""
        guide = GhostGuide(narration_engine=MagicMock())
        voice = guide.get_voice_for_language(lang)
        assert voice in {"Kore", "Charon", "Puck", "Enceladus"}
