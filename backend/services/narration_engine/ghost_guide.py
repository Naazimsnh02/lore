"""Multilingual Ghost Guide — adapts narration to 24 languages with cultural sensitivity.

Design reference: LORE design.md, Multilingual Ghost Guide section.
Requirements: 17.1–17.6.

The Ghost Guide wraps the NarrationEngine to:

  1. Generate narration directly in a target language with culturally
     appropriate style instructions (Req 17.2, 17.4).

  2. Switch languages mid-session while continuing from the current
     stream position (Req 17.5, 17.6).

  3. Select the best Gemini voice for each language based on the
     language family and available prebuilt voices.

  4. Support 24 languages with graceful fallback for unsupported codes
     (Req 17.1).

Dependency injection
  The Ghost Guide delegates to a NarrationEngine instance for actual
  script generation and translation.  It adds language-aware prompt
  augmentation and cultural style instructions.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .models import (
    DepthLevel,
    EmotionalTone,
    NarrationContext,
    NarrationScript,
    NarrationSegment,
)

logger = logging.getLogger(__name__)

# ── Supported languages (24 — Req 17.1) ─────────────────────
SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "nl": "Dutch",
    "ru": "Russian",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "ar": "Arabic",
    "hi": "Hindi",
    "tr": "Turkish",
    "pl": "Polish",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "el": "Greek",
    "th": "Thai",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "he": "Hebrew",
}

# ── Cultural narration style instructions (Req 17.4) ─────────
CULTURAL_STYLES: dict[str, str] = {
    "ja": (
        "Use polite, formal Japanese narration style (です/ます form). "
        "Incorporate seasonal references (kigo) where appropriate."
    ),
    "ar": (
        "Use Modern Standard Arabic. Right-to-left narrative flow. "
        "Reference historical Islamic Golden Age connections where relevant."
    ),
    "zh": (
        "Use Simplified Chinese. Incorporate relevant Chinese historical "
        "parallels or cultural connections."
    ),
    "hi": (
        "Use Devanagari Hindi. Reference relevant connections to Indian "
        "history and culture."
    ),
    "ko": (
        "Use formal Korean (합니다 form). Reference relevant Korean "
        "historical connections."
    ),
    "fr": (
        "Use literary French style. Reference French cultural and "
        "intellectual traditions where relevant."
    ),
    "de": (
        "Use standard Hochdeutsch. Reference Germanic historical "
        "connections where relevant."
    ),
    "es": (
        "Use neutral Latin American Spanish. Reference Hispanic cultural "
        "connections where relevant."
    ),
    "it": (
        "Use standard Italian. Reference Italian Renaissance and cultural "
        "heritage where relevant."
    ),
    "pt": (
        "Use Brazilian Portuguese. Reference Lusophone cultural connections "
        "where relevant."
    ),
    "ru": (
        "Use standard Russian. Reference relevant Russian literary and "
        "cultural traditions."
    ),
    "tr": (
        "Use standard Turkish. Reference Ottoman and Anatolian historical "
        "connections where relevant."
    ),
}

# ── Language → preferred Gemini voice mapping ─────────────────
# Gemini prebuilt voices that work best for each language family.
# Languages not listed fall back to the default voice "Kore".
_LANGUAGE_VOICES: dict[str, str] = {
    "en": "Kore",
    "es": "Kore",
    "fr": "Kore",
    "de": "Kore",
    "it": "Kore",
    "pt": "Kore",
    "nl": "Kore",
    "ru": "Charon",
    "zh": "Enceladus",
    "ja": "Enceladus",
    "ko": "Enceladus",
    "ar": "Charon",
    "hi": "Puck",
    "tr": "Charon",
    "pl": "Kore",
    "sv": "Kore",
    "da": "Kore",
    "no": "Kore",
    "fi": "Kore",
    "el": "Charon",
    "th": "Enceladus",
    "vi": "Enceladus",
    "id": "Puck",
    "he": "Charon",
}

_DEFAULT_VOICE = "Kore"
_DEFAULT_CULTURAL_STYLE = (
    "Use culturally appropriate narration style for the target language. "
    "Where relevant, draw connections to the local cultural heritage."
)


class GhostGuide:
    """Multilingual Ghost Guide — adapts narration to 24 languages.

    The Ghost Guide sits on top of the NarrationEngine and enriches
    narration requests with language-specific and culturally-aware
    prompt instructions.  It handles language selection, switching, and
    voice mapping so the rest of the system can remain language-agnostic.

    Parameters
    ----------
    narration_engine:
        A ``NarrationEngine`` instance used for script generation and
        translation.
    default_language:
        ISO 639-1 language code used when no explicit language is
        provided.
    """

    def __init__(
        self,
        narration_engine: Any,
        default_language: str = "en",
    ) -> None:
        self._engine = narration_engine
        self._default_language = default_language
        # Track the active language per session for mid-session switching
        self._session_languages: dict[str, str] = {}

    # ── Public API ──────────────────────────────────────────────

    async def generate_in_language(
        self,
        context: NarrationContext,
        language: str,
    ) -> NarrationScript:
        """Generate narration directly in the target language (Req 17.2).

        Augments the context with cultural style instructions and sets
        the language before delegating to the NarrationEngine.

        Parameters
        ----------
        context:
            The documentary narration context.
        language:
            ISO 639-1 language code for output.

        Returns
        -------
        NarrationScript in the requested language, or in the default
        language if the requested one is unsupported.
        """
        effective_lang = language if self.is_supported(language) else self._default_language
        if not self.is_supported(language):
            logger.warning(
                "Unsupported language '%s', falling back to '%s'",
                language,
                self._default_language,
            )

        # Enrich context with cultural instruction and language
        enriched = self._enrich_context(context, effective_lang)

        script = await self._engine.generate_script(enriched)

        # Track session language
        if context.session_id:
            self._session_languages[context.session_id] = effective_lang

        return script

    async def switch_language(
        self,
        current_script: NarrationScript,
        new_language: str,
        stream_position: int = 0,
    ) -> NarrationScript:
        """Switch language mid-session, continuing from stream position.

        Translates the remaining segments (from ``stream_position``
        onward) into the new language while preserving factual accuracy
        (Req 17.5, 17.6).

        Parameters
        ----------
        current_script:
            The currently playing NarrationScript.
        new_language:
            ISO 639-1 code for the new language.
        stream_position:
            Index of the segment to continue from.  Segments before this
            index are dropped; the returned script starts at the new
            language from this point.

        Returns
        -------
        A new NarrationScript in the target language containing only the
        remaining segments.
        """
        effective_lang = new_language if self.is_supported(new_language) else self._default_language
        if not self.is_supported(new_language):
            logger.warning(
                "Language switch to unsupported '%s', falling back to '%s'",
                new_language,
                self._default_language,
            )

        # If already in the target language, just slice
        if current_script.language == effective_lang:
            remaining = current_script.segments[stream_position:]
            total_dur = sum(s.duration for s in remaining)
            return NarrationScript(
                segments=remaining,
                total_duration=total_dur,
                language=effective_lang,
                depth_level=current_script.depth_level,
                tone=current_script.tone,
            )

        # Build a partial script from the remaining segments
        remaining_segments = current_script.segments[stream_position:]
        if not remaining_segments:
            logger.info("No remaining segments to translate at position %d", stream_position)
            return NarrationScript(
                segments=[],
                total_duration=0.0,
                language=effective_lang,
                depth_level=current_script.depth_level,
                tone=current_script.tone,
            )

        partial_script = NarrationScript(
            segments=remaining_segments,
            total_duration=sum(s.duration for s in remaining_segments),
            language=current_script.language,
            depth_level=current_script.depth_level,
            tone=current_script.tone,
        )

        # Delegate translation to the engine
        translated = await self._engine.translate_script(partial_script, effective_lang)

        return translated

    def get_cultural_style(self, language: str) -> str:
        """Get culturally appropriate narration style instruction (Req 17.4).

        Returns a string prompt fragment that guides the narration model
        to produce culturally sensitive output for the given language.
        """
        return CULTURAL_STYLES.get(language, _DEFAULT_CULTURAL_STYLE)

    def is_supported(self, language: str) -> bool:
        """Check if a language code is supported (Req 17.1).

        Parameters
        ----------
        language:
            ISO 639-1 language code (e.g. "en", "ja", "ar").

        Returns
        -------
        True if the language is among the 24 supported languages.
        """
        return language in SUPPORTED_LANGUAGES

    def get_voice_for_language(self, language: str) -> str:
        """Get the most appropriate Gemini voice name for a language.

        Returns a prebuilt Gemini voice name selected based on the
        language family.  Falls back to the default voice for unknown
        languages.
        """
        return _LANGUAGE_VOICES.get(language, _DEFAULT_VOICE)

    def get_session_language(self, session_id: str) -> str:
        """Get the active language for a session.

        Returns the last language set via ``generate_in_language`` or
        ``set_session_language``, or the default language if none has
        been set.
        """
        return self._session_languages.get(session_id, self._default_language)

    def set_session_language(self, session_id: str, language: str) -> None:
        """Explicitly set the active language for a session."""
        effective = language if self.is_supported(language) else self._default_language
        self._session_languages[session_id] = effective

    def clear_session(self, session_id: str) -> None:
        """Remove language tracking for a session."""
        self._session_languages.pop(session_id, None)

    @property
    def supported_languages(self) -> dict[str, str]:
        """Return a copy of the supported language map."""
        return dict(SUPPORTED_LANGUAGES)

    # ── Private helpers ─────────────────────────────────────────

    def _enrich_context(
        self,
        context: NarrationContext,
        language: str,
    ) -> NarrationContext:
        """Create a new NarrationContext enriched with cultural style.

        The original context is not mutated; a copy is returned with
        the language and custom_instructions updated.
        """
        cultural_style = self.get_cultural_style(language)

        # Merge cultural instruction with any existing custom instructions
        existing = context.custom_instructions or ""
        merged_instructions = (
            f"{existing}\n\n{cultural_style}" if existing else cultural_style
        )

        return NarrationContext(
            mode=context.mode,
            topic=context.topic,
            place_name=context.place_name,
            place_description=context.place_description,
            place_types=list(context.place_types),
            visual_description=context.visual_description,
            latitude=context.latitude,
            longitude=context.longitude,
            language=language,
            depth_level=context.depth_level,
            session_id=context.session_id,
            user_id=context.user_id,
            previous_topics=list(context.previous_topics),
            custom_instructions=merged_instructions,
        )
