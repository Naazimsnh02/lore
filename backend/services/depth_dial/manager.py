"""Depth Dial Manager — adjusts content complexity based on user expertise level.

Design reference: LORE design.md, Section "Depth Dial Configuration".
Requirements: 14.1–14.6.
Property 13: complexity(Explorer, T) < complexity(Scholar, T) < complexity(Expert, T).

The manager provides two complementary mechanisms:

1. **Prompt-based adaptation** (``get_narration_prompt_config``, ``build_narration_instructions``):
   Returns prompt engineering directives that downstream services (NarrationEngine,
   NanoIllustrator, SearchGrounder) embed in their LLM calls so that *generated*
   content is already at the right complexity.  This is the primary path.

2. **Post-hoc adaptation** (``adapt_content``):
   Re-writes existing text to a target depth via an LLM call.  Useful when
   the user changes depth mid-session and previously queued content needs
   retroactive adjustment (Req 14.5, 14.6).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .models import (
    ContentAdaptationRequest,
    ContentAdaptationResult,
    DEPTH_COMPLEXITY,
    DepthDialState,
    DepthLevel,
    DepthLevelConfig,
    NarrationPromptConfig,
)

logger = logging.getLogger(__name__)


# ── Level configurations (design.md reference values) ──────────────────────

_LEVEL_CONFIGS: dict[DepthLevel, DepthLevelConfig] = {
    DepthLevel.EXPLORER: DepthLevelConfig(
        complexity=1,
        vocabulary="simple",
        detail_level="overview",
        technical_depth="minimal",
        examples="many",
        duration_multiplier=1.0,
    ),
    DepthLevel.SCHOLAR: DepthLevelConfig(
        complexity=2,
        vocabulary="intermediate",
        detail_level="detailed",
        technical_depth="moderate",
        examples="some",
        duration_multiplier=1.5,
    ),
    DepthLevel.EXPERT: DepthLevelConfig(
        complexity=3,
        vocabulary="advanced",
        detail_level="comprehensive",
        technical_depth="deep",
        examples="few",
        duration_multiplier=2.0,
    ),
}


# ── Prompt engineering configs per level ───────────────────────────────────

_PROMPT_CONFIGS: dict[DepthLevel, NarrationPromptConfig] = {
    DepthLevel.EXPLORER: NarrationPromptConfig(
        system_instruction=(
            "You are narrating a documentary for a general audience. "
            "Use simple, everyday language that anyone can understand. "
            "Avoid jargon and technical terms. If a complex concept must "
            "be mentioned, immediately explain it with a concrete analogy."
        ),
        vocabulary_instruction="Use simple, everyday vocabulary. Avoid technical terms entirely.",
        detail_instruction=(
            "Give a high-level overview. Focus on the most interesting facts. "
            "Keep explanations short and engaging."
        ),
        example_instruction="Include plenty of concrete, relatable examples and analogies.",
        max_sentences_per_segment=5,
        target_reading_level="grade 6",
    ),
    DepthLevel.SCHOLAR: NarrationPromptConfig(
        system_instruction=(
            "You are narrating a documentary for a curious, educated audience. "
            "Use intermediate vocabulary with some technical terms, but explain "
            "them briefly on first use. Provide historical context and connect "
            "ideas to broader themes."
        ),
        vocabulary_instruction=(
            "Use intermediate vocabulary. Introduce technical terms with brief in-line definitions."
        ),
        detail_instruction=(
            "Provide detailed explanations with historical context. "
            "Explain the significance of events and connect them to related concepts."
        ),
        example_instruction="Include some illustrative examples; balance detail with brevity.",
        max_sentences_per_segment=8,
        target_reading_level="undergraduate",
    ),
    DepthLevel.EXPERT: NarrationPromptConfig(
        system_instruction=(
            "You are narrating a documentary for subject-matter experts. "
            "Use precise technical terminology without simplification. "
            "Reference scholarly debates, cite methodologies, and explore "
            "nuances that a specialist would appreciate."
        ),
        vocabulary_instruction=(
            "Use advanced, precise technical terminology. "
            "Do not simplify or explain well-known domain concepts."
        ),
        detail_instruction=(
            "Provide comprehensive analysis. Discuss scholarly debates, "
            "methodologies, primary sources, and historiographic perspectives."
        ),
        example_instruction=(
            "Use few but highly specific examples. "
            "Prefer citations and primary-source references."
        ),
        max_sentences_per_segment=12,
        target_reading_level="postgraduate",
    ),
}


# ── Adaptation prompt templates (for post-hoc rewriting) ──────────────────

_SIMPLIFY_PROMPT = """\
Rewrite the following content for a general audience (Explorer level):
- Use simple, everyday language
- Add concrete examples and analogies
- Break down complex ideas into short sentences
- Remove jargon; if a term must stay, explain it immediately
- Keep the same factual meaning

Content:
{content}

Rewritten content:"""

_CONTEXT_PROMPT = """\
Enhance the following content for a curious, educated audience (Scholar level):
- Add historical context and explain significance
- Introduce relevant connections to related concepts
- Use intermediate vocabulary; define technical terms briefly
- Maintain factual accuracy

Content:
{content}

Enhanced content:"""

_TECHNICAL_PROMPT = """\
Enhance the following content for subject-matter experts (Expert level):
- Use precise technical terminology without simplification
- Include scholarly references and methodological notes
- Discuss nuances, debates, and complexities in the field
- Maintain factual accuracy

Content:
{content}

Expert-level content:"""

_ADAPTATION_PROMPTS: dict[DepthLevel, str] = {
    DepthLevel.EXPLORER: _SIMPLIFY_PROMPT,
    DepthLevel.SCHOLAR: _CONTEXT_PROMPT,
    DepthLevel.EXPERT: _TECHNICAL_PROMPT,
}


class DepthDialManager:
    """Manages content complexity adaptation across depth levels.

    Thread-safe for concurrent use across multiple sessions — each session
    has its own ``DepthDialState`` stored in ``_sessions``.

    Args:
        genai_client: Optional ``google.genai.Client`` for post-hoc
            content adaptation.  If *None*, ``adapt_content`` falls back
            to returning the original text unchanged.
        model: Gemini model name used for content adaptation calls.
    """

    def __init__(
        self,
        genai_client: Any = None,
        model: str = "gemini-3-flash-preview",
    ) -> None:
        self._client = genai_client
        self._model = model
        self._sessions: dict[str, DepthDialState] = {}

    # ── Level configuration getters ────────────────────────────────────────

    def get_level_config(self, level: DepthLevel) -> DepthLevelConfig:
        """Return the full configuration dict for *level* (Req 14.1)."""
        return _LEVEL_CONFIGS[level]

    def get_all_configs(self) -> dict[DepthLevel, DepthLevelConfig]:
        """Return all level configurations."""
        return dict(_LEVEL_CONFIGS)

    def get_complexity(self, level: DepthLevel) -> int:
        """Return numeric complexity (Property 13 ordering)."""
        return DEPTH_COMPLEXITY[level]

    # ── Prompt-based adaptation (primary path) ─────────────────────────────

    def get_narration_prompt_config(self, level: DepthLevel) -> NarrationPromptConfig:
        """Return prompt engineering config for *level*.

        Downstream services embed these instructions in their LLM calls
        so that generated content is produced at the correct complexity
        from the start (Req 14.2–14.4).
        """
        return _PROMPT_CONFIGS[level]

    def build_narration_instructions(self, level: DepthLevel) -> str:
        """Build a single instruction block for injection into narration prompts.

        This is a convenience wrapper over ``get_narration_prompt_config``.
        """
        cfg = _PROMPT_CONFIGS[level]
        return (
            f"{cfg.system_instruction}\n\n"
            f"Vocabulary: {cfg.vocabulary_instruction}\n"
            f"Detail: {cfg.detail_instruction}\n"
            f"Examples: {cfg.example_instruction}\n"
            f"Target reading level: {cfg.target_reading_level}.\n"
            f"Maximum {cfg.max_sentences_per_segment} sentences per segment."
        )

    def build_illustration_instructions(self, level: DepthLevel) -> str:
        """Return style guidance for illustration prompts based on depth."""
        cfg = _LEVEL_CONFIGS[level]
        if level == DepthLevel.EXPLORER:
            return (
                "Create a vibrant, approachable illustration suitable for a general audience. "
                "Use bold colours, clear composition, and include visual labels or annotations."
            )
        if level == DepthLevel.SCHOLAR:
            return (
                "Create a detailed illustration with historical accuracy. "
                "Include contextual elements that show time period and significance."
            )
        # Expert
        return (
            "Create a precise, scholarly illustration. "
            "Prioritise technical accuracy, include scale references, "
            "and use a subdued, academic colour palette."
        )

    def get_duration_multiplier(self, level: DepthLevel) -> float:
        """Return the narration duration multiplier for *level*.

        Explorer = 1.0×, Scholar = 1.5×, Expert = 2.0×.
        """
        return _LEVEL_CONFIGS[level].duration_multiplier

    # ── Post-hoc content adaptation ────────────────────────────────────────

    async def adapt_content(
        self,
        content: str,
        level: DepthLevel,
        topic: str | None = None,
        language: str = "en",
    ) -> ContentAdaptationResult:
        """Adapt *content* to the specified depth *level* via LLM rewrite.

        If no genai_client is configured the original content is returned
        unchanged (graceful degradation).

        Args:
            content:  Source text to adapt.
            level:    Target depth level.
            topic:    Optional topic hint for better adaptation.
            language: Target language code.

        Returns:
            ``ContentAdaptationResult`` with adapted text and metadata.
        """
        config = _LEVEL_CONFIGS[level]
        original_word_count = len(content.split())

        if not self._client:
            logger.warning("No genai_client configured — returning content unchanged")
            return ContentAdaptationResult(
                original_content=content,
                adapted_content=content,
                level=level,
                config=config,
                word_count_original=original_word_count,
                word_count_adapted=original_word_count,
            )

        prompt_template = _ADAPTATION_PROMPTS[level]
        prompt = prompt_template.format(content=content)

        if topic:
            prompt = f"Topic context: {topic}\n\n{prompt}"
        if language != "en":
            prompt += f"\n\nIMPORTANT: Output must be in language code '{language}'."

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=prompt,
            )
            adapted = response.text.strip() if response.text else content
        except Exception as exc:
            logger.error("Content adaptation failed: %s", exc)
            return ContentAdaptationResult(
                original_content=content,
                adapted_content=content,
                level=level,
                config=config,
                word_count_original=original_word_count,
                word_count_adapted=original_word_count,
                error=str(exc),
            )

        adapted_word_count = len(adapted.split())

        return ContentAdaptationResult(
            original_content=content,
            adapted_content=adapted,
            level=level,
            config=config,
            word_count_original=original_word_count,
            word_count_adapted=adapted_word_count,
        )

    async def simplify_content(self, content: str) -> str:
        """Convenience — adapt *content* to Explorer level (Req 14.2)."""
        result = await self.adapt_content(content, DepthLevel.EXPLORER)
        return result.adapted_content

    async def add_context(self, content: str) -> str:
        """Convenience — adapt *content* to Scholar level (Req 14.3)."""
        result = await self.adapt_content(content, DepthLevel.SCHOLAR)
        return result.adapted_content

    async def add_technical_depth(self, content: str) -> str:
        """Convenience — adapt *content* to Expert level (Req 14.4)."""
        result = await self.adapt_content(content, DepthLevel.EXPERT)
        return result.adapted_content

    # ── Session state management (Req 14.5, 14.6) ─────────────────────────

    def get_session_state(self, session_id: str) -> DepthDialState:
        """Return or create depth dial state for *session_id*."""
        if session_id not in self._sessions:
            self._sessions[session_id] = DepthDialState(session_id=session_id)
        return self._sessions[session_id]

    async def change_depth_dial(
        self,
        session_id: str,
        new_level: DepthLevel,
        session_memory: Any = None,
    ) -> DepthDialState:
        """Change depth dial during an active session (Req 14.5, 14.6).

        Updates in-memory state and optionally persists to Firestore via
        ``session_memory``.  Already-generated content is NOT retroactively
        changed — only subsequent content adapts to the new level.

        Args:
            session_id:     Active session identifier.
            new_level:      Target depth level.
            session_memory: Optional SessionMemoryManager for persistence.

        Returns:
            Updated ``DepthDialState``.
        """
        state = self.get_session_state(session_id)
        old_level = state.current_level

        if old_level == new_level:
            logger.info("Depth dial unchanged for session %s (already %s)", session_id, new_level.value)
            return state

        state.previous_level = old_level
        state.current_level = new_level
        state.change_count += 1

        logger.info(
            "Depth dial changed: session=%s %s→%s (change #%d)",
            session_id,
            old_level.value,
            new_level.value,
            state.change_count,
        )

        # Persist to Firestore if SessionMemoryManager is available
        if session_memory:
            try:
                await session_memory.update_session(
                    session_id,
                    {"depth_dial": new_level.value},
                )
            except Exception as exc:
                logger.warning("Failed to persist depth dial change: %s", exc)

        return state

    def get_current_level(self, session_id: str) -> DepthLevel:
        """Return the current depth level for a session (defaults to EXPLORER)."""
        return self.get_session_state(session_id).current_level

    def reset_session(self, session_id: str) -> None:
        """Remove depth dial state for a session."""
        self._sessions.pop(session_id, None)
