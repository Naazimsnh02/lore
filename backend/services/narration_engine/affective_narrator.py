"""Affective Narrator — adapts narration tone to emotional context.

Design reference: LORE design.md, Affective Narration section.
Requirements: 11.1–11.6.

The Affective Narrator analyses documentary context (location types,
topic sentiment) to select an appropriate emotional tone, then maps
that tone to concrete voice parameters for the Gemini Live API.

Tone profiles (Req 11.2–11.4):
  respectful    — war memorials, tragedies → slower, lower, quieter
  enthusiastic  — festivals, achievements  → faster, higher, energetic
  contemplative — ancient ruins, mysteries  → slightly slower, thoughtful
  neutral       — factual, default          → standard settings
"""

from __future__ import annotations

import logging
from typing import Optional

from .models import EmotionalTone, NarrationContext, VoiceParameters

logger = logging.getLogger(__name__)

# Voice names suited to each tone
_TONE_VOICES: dict[EmotionalTone, str] = {
    EmotionalTone.RESPECTFUL: "Charon",       # Informative, measured
    EmotionalTone.ENTHUSIASTIC: "Puck",        # Upbeat, energetic
    EmotionalTone.CONTEMPLATIVE: "Enceladus",  # Breathy, thoughtful
    EmotionalTone.NEUTRAL: "Kore",             # Firm, standard
}

# Location types → tone mapping (Req 11.2, 11.3, 11.4)
_RESPECTFUL_TYPES = frozenset({
    "cemetery", "memorial", "war_memorial", "funeral_home",
    "place_of_worship", "church", "mosque", "synagogue", "temple",
})
_CONTEMPLATIVE_TYPES = frozenset({
    "museum", "library", "university", "art_gallery", "book_store",
    "archaeological_site", "historic_site", "ruins",
})
_ENTHUSIASTIC_TYPES = frozenset({
    "festival", "celebration", "park", "amusement_park", "zoo",
    "stadium", "tourist_attraction", "aquarium", "theme_park",
})

# Keyword-based sentiment analysis
_NEGATIVE_KEYWORDS = frozenset({
    "war", "tragedy", "death", "disaster", "conflict", "massacre",
    "famine", "plague", "genocide", "slavery", "persecution",
    "destruction", "catastrophe", "suffering", "atrocity",
})
_POSITIVE_KEYWORDS = frozenset({
    "celebration", "achievement", "discovery", "victory", "innovation",
    "triumph", "festival", "renaissance", "golden age", "revolution",
    "breakthrough", "independence", "liberation", "prosperity",
})


class AffectiveNarrator:
    """Determines emotional tone and voice parameters for narration.

    The narrator is stateless — each call to determine_emotional_tone
    receives a NarrationContext and returns the best-fit EmotionalTone.
    adapt_tone then maps that tone to concrete VoiceParameters.
    """

    def __init__(self, default_voice: str = "Kore") -> None:
        self._default_voice = default_voice
        self._tone_profiles: dict[EmotionalTone, VoiceParameters] = {
            EmotionalTone.RESPECTFUL: VoiceParameters(
                voice_name=_TONE_VOICES[EmotionalTone.RESPECTFUL],
                speaking_rate=0.9,
                pitch=-2.0,
                volume_gain_db=-3.0,
                pause_duration=1.5,
                vocabulary="formal",
            ),
            EmotionalTone.ENTHUSIASTIC: VoiceParameters(
                voice_name=_TONE_VOICES[EmotionalTone.ENTHUSIASTIC],
                speaking_rate=1.1,
                pitch=2.0,
                volume_gain_db=0.0,
                pause_duration=0.5,
                vocabulary="energetic",
            ),
            EmotionalTone.CONTEMPLATIVE: VoiceParameters(
                voice_name=_TONE_VOICES[EmotionalTone.CONTEMPLATIVE],
                speaking_rate=0.95,
                pitch=0.0,
                volume_gain_db=-1.0,
                pause_duration=1.0,
                vocabulary="thoughtful",
            ),
            EmotionalTone.NEUTRAL: VoiceParameters(
                voice_name=_TONE_VOICES[EmotionalTone.NEUTRAL],
                speaking_rate=1.0,
                pitch=0.0,
                volume_gain_db=0.0,
                pause_duration=0.8,
                vocabulary="standard",
            ),
        }

    # ── Public API ─────────────────────────────────────────────

    def determine_emotional_tone(self, context: NarrationContext) -> EmotionalTone:
        """Analyse context to choose the appropriate narration tone.

        Priority order:
          1. Location types (most reliable signal)
          2. Topic sentiment (keyword-based heuristic)
          3. Default to neutral
        """
        # 1. Check location types (Req 11.2–11.4)
        if context.place_types:
            type_set = set(context.place_types)
            if type_set & _RESPECTFUL_TYPES:
                logger.debug("Tone → respectful (location types: %s)", type_set & _RESPECTFUL_TYPES)
                return EmotionalTone.RESPECTFUL
            if type_set & _CONTEMPLATIVE_TYPES:
                logger.debug("Tone → contemplative (location types: %s)", type_set & _CONTEMPLATIVE_TYPES)
                return EmotionalTone.CONTEMPLATIVE
            if type_set & _ENTHUSIASTIC_TYPES:
                logger.debug("Tone → enthusiastic (location types: %s)", type_set & _ENTHUSIASTIC_TYPES)
                return EmotionalTone.ENTHUSIASTIC

        # 2. Topic sentiment
        topic_text = context.topic or context.place_description or ""
        if topic_text:
            sentiment = self.analyze_sentiment(topic_text)
            if sentiment < -0.3:
                logger.debug("Tone → respectful (sentiment=%.2f)", sentiment)
                return EmotionalTone.RESPECTFUL
            if sentiment > 0.3:
                logger.debug("Tone → enthusiastic (sentiment=%.2f)", sentiment)
                return EmotionalTone.ENTHUSIASTIC
            if abs(sentiment) <= 0.3 and len(topic_text) > 20:
                logger.debug("Tone → contemplative (neutral sentiment, long topic)")
                return EmotionalTone.CONTEMPLATIVE

        return EmotionalTone.NEUTRAL

    def adapt_tone(self, tone: EmotionalTone) -> VoiceParameters:
        """Map an emotional tone to concrete voice parameters."""
        return self._tone_profiles.get(tone, self._tone_profiles[EmotionalTone.NEUTRAL])

    def get_tone_instruction(self, tone: EmotionalTone) -> str:
        """Return a natural-language instruction for the Gemini model
        that guides its speaking style to match the desired tone.

        Since the Live API does not expose speaking_rate/pitch as config
        parameters, we control tone via prompt engineering.
        """
        instructions = {
            EmotionalTone.RESPECTFUL: (
                "Speak in a respectful, measured, and solemn tone. "
                "Use a slower pace with longer pauses between sentences. "
                "Choose formal, dignified vocabulary. "
                "Be sensitive and empathetic in your delivery."
            ),
            EmotionalTone.ENTHUSIASTIC: (
                "Speak with enthusiasm and energy! "
                "Use an upbeat, lively pace with shorter pauses. "
                "Choose vibrant, exciting vocabulary. "
                "Convey a sense of wonder and excitement."
            ),
            EmotionalTone.CONTEMPLATIVE: (
                "Speak in a thoughtful, contemplative tone. "
                "Use a moderate pace with natural pauses for reflection. "
                "Choose evocative, descriptive vocabulary. "
                "Invite the listener to ponder and imagine."
            ),
            EmotionalTone.NEUTRAL: (
                "Speak in a clear, informative tone. "
                "Use a natural, conversational pace. "
                "Choose accessible, precise vocabulary. "
                "Be engaging but balanced in your delivery."
            ),
        }
        return instructions.get(tone, instructions[EmotionalTone.NEUTRAL])

    # ── Sentiment helpers ──────────────────────────────────────

    @staticmethod
    def analyze_sentiment(text: str) -> float:
        """Simple keyword-based sentiment analysis returning [-1.0, 1.0].

        Negative values → sombre/tragic content.
        Positive values → celebratory/uplifting content.
        """
        text_lower = text.lower()
        neg = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in text_lower)
        pos = sum(1 for kw in _POSITIVE_KEYWORDS if kw in text_lower)
        total = neg + pos
        if total == 0:
            return 0.0
        return (pos - neg) / total
