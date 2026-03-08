"""Conversation management for VoiceMode.

Tracks conversation history, manages context windows, and classifies user
intents to drive the documentary generation pipeline.

Design reference: LORE design.md, Conversation Management section.
Requirements: 3.4 (continuous conversation), 13.1 (branch detection),
              13.2 (branch creation), 13.4 (branch depth ≤ 3).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .models import (
    ConversationIntent,
    ConversationState,
    ConversationTurn,
    IntentClassification,
    VoiceModeContext,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

DEFAULT_CONTEXT_WINDOW: int = 10  # Last N interactions to keep in context
MAX_BRANCH_DEPTH: int = 3  # Req 13.4
INACTIVITY_TIMEOUT_S: float = 300.0  # 5 minutes before session considered stale

# Intent classification keywords / heuristics
_BRANCH_INDICATORS = [
    "tell me more about",
    "what about",
    "let's explore",
    "can we go deeper",
    "branch into",
    "dive into",
    "sidebar",
    "tangent",
    "also tell me about",
    "let's also look at",
    "what else about",
    "more on",
    "expand on",
]

_QUESTION_INDICATORS = [
    "what is",
    "what are",
    "what was",
    "what were",
    "who is",
    "who was",
    "who were",
    "where is",
    "where was",
    "when did",
    "when was",
    "how did",
    "how does",
    "how was",
    "how many",
    "how much",
    "why did",
    "why does",
    "why is",
    "why was",
    "is it true",
    "did they",
    "can you explain",
    "could you explain",
    "?",
]

_COMMAND_INDICATORS = [
    "stop",
    "pause",
    "resume",
    "switch mode",
    "change mode",
    "change language",
    "switch language",
    "change depth",
    "set depth",
    "go back",
    "return to",
    "exit branch",
    "close branch",
    "export",
    "save",
]

_FOLLOW_UP_INDICATORS = [
    "and also",
    "additionally",
    "furthermore",
    "what happened next",
    "then what",
    "continue",
    "go on",
    "keep going",
    "more",
    "what else",
    "and then",
    "after that",
    "yes",
    "right",
    "okay",
    "sure",
]


class ConversationManager:
    """Manages conversation state, history, and intent classification.

    Provides context-aware intent classification to determine whether the
    user is starting a new topic, following up, requesting a branch
    documentary, asking a question, or issuing a command.

    Attributes:
        state: Current conversation state (session, topic, branch depth).
        history: Full conversation turn history.
    """

    def __init__(
        self,
        *,
        session_id: str = "",
        user_id: str = "",
        context_window: int = DEFAULT_CONTEXT_WINDOW,
        genai_client: Any = None,
    ) -> None:
        self._context_window = context_window
        self._client = genai_client  # optional for LLM-based intent classification
        self.state = ConversationState(
            session_id=session_id, user_id=user_id
        )
        self.history: list[ConversationTurn] = []

    # ── Public API ───────────────────────────────────────────────────────────

    async def handle_input(
        self, voice_context: VoiceModeContext
    ) -> IntentClassification:
        """Process a new user voice input and classify its intent.

        Steps:
          1. Record the user turn in history.
          2. Classify intent using heuristics (+ optional LLM fallback).
          3. Update conversation state.
          4. Return the classification result.

        Args:
            voice_context: Parsed voice context from VoiceModeHandler.

        Returns:
            IntentClassification with intent, confidence, and optional
            extracted branch topic.
        """
        text = voice_context.original_query or voice_context.topic
        topic = voice_context.topic

        # 1. Record user turn
        turn = ConversationTurn(
            role="user",
            content=text,
            topic=topic,
            language=voice_context.language,
        )
        self._add_turn(turn)

        # 2. Classify intent
        context_turns = self.get_context()
        classification = await self._classify_intent(text, topic, context_turns)

        # 3. Update state
        turn.intent = classification.intent
        self.state.last_activity = time.time()

        if classification.intent == ConversationIntent.NEW_TOPIC:
            self.state.current_topic = topic
            self.state.current_language = voice_context.language
        elif classification.intent == ConversationIntent.BRANCH:
            if self.state.branch_depth < MAX_BRANCH_DEPTH:
                self.state.branch_depth += 1
                self.state.branch_stack.append(
                    classification.branch_topic or topic
                )
            else:
                # Downgrade to follow_up when at max depth (Req 13.4)
                logger.info(
                    "Max branch depth (%d) reached — downgrading to follow_up",
                    MAX_BRANCH_DEPTH,
                )
                classification = IntentClassification(
                    intent=ConversationIntent.FOLLOW_UP,
                    confidence=classification.confidence,
                    extracted_topic=classification.extracted_topic,
                    reasoning=f"Branch depth limit ({MAX_BRANCH_DEPTH}) reached; treated as follow-up",
                )
        elif classification.intent == ConversationIntent.COMMAND:
            # Handle "go back" / "exit branch" commands
            if self._is_branch_exit(text) and self.state.branch_depth > 0:
                self.state.branch_depth -= 1
                if self.state.branch_stack:
                    self.state.branch_stack.pop()

        return classification

    def add_assistant_turn(self, content: str, topic: Optional[str] = None) -> None:
        """Record an assistant response in the conversation history."""
        turn = ConversationTurn(
            role="assistant",
            content=content,
            topic=topic or self.state.current_topic,
            language=self.state.current_language,
        )
        self._add_turn(turn)

    def get_context(self) -> list[ConversationTurn]:
        """Return the most recent turns within the context window (Req 3.4)."""
        return self.history[-self._context_window:]

    def get_context_summary(self) -> str:
        """Return a text summary of recent context for LLM prompting."""
        turns = self.get_context()
        if not turns:
            return ""
        lines = []
        for t in turns:
            role = "User" if t.role == "user" else "Assistant"
            lines.append(f"{role}: {t.content}")
        return "\n".join(lines)

    def get_current_topic(self) -> Optional[str]:
        return self.state.current_topic

    def get_topics_discussed(self) -> list[str]:
        """Return all unique topics from conversation history."""
        topics: list[str] = []
        seen: set[str] = set()
        for t in self.history:
            if t.topic and t.topic not in seen:
                topics.append(t.topic)
                seen.add(t.topic)
        return topics

    @property
    def turn_count(self) -> int:
        return self.state.turn_count

    @property
    def branch_depth(self) -> int:
        return self.state.branch_depth

    def is_stale(self) -> bool:
        """Check if the conversation has been inactive too long."""
        return (time.time() - self.state.last_activity) > INACTIVITY_TIMEOUT_S

    def reset(self) -> None:
        """Reset conversation state (e.g. new session)."""
        self.history.clear()
        self.state = ConversationState(
            session_id=self.state.session_id,
            user_id=self.state.user_id,
        )

    # ── Internal ─────────────────────────────────────────────────────────────

    def _add_turn(self, turn: ConversationTurn) -> None:
        self.history.append(turn)
        self.state.turn_count += 1

    async def _classify_intent(
        self,
        text: str,
        topic: str,
        context: list[ConversationTurn],
    ) -> IntentClassification:
        """Classify intent using keyword heuristics.

        Falls back to LLM-based classification when a genai_client is
        available and heuristics are ambiguous.
        """
        lower = text.lower().strip()

        # 1. Command check (highest priority — these are system-level)
        if self._matches_any(lower, _COMMAND_INDICATORS):
            return IntentClassification(
                intent=ConversationIntent.COMMAND,
                confidence=0.9,
                extracted_topic=topic,
                reasoning="Matched command keyword",
            )

        # 2. Branch request check
        branch_topic = self._extract_branch_topic(lower)
        if branch_topic is not None:
            return IntentClassification(
                intent=ConversationIntent.BRANCH,
                confidence=0.85,
                extracted_topic=topic,
                branch_topic=branch_topic,
                reasoning="Matched branch indicator",
            )

        # 3. Question check
        if self._matches_any(lower, _QUESTION_INDICATORS) or lower.endswith("?"):
            return IntentClassification(
                intent=ConversationIntent.QUESTION,
                confidence=0.85,
                extracted_topic=topic,
                reasoning="Matched question indicator",
            )

        # 4. Follow-up check (requires existing topic)
        if self.state.current_topic and self._matches_any(lower, _FOLLOW_UP_INDICATORS):
            return IntentClassification(
                intent=ConversationIntent.FOLLOW_UP,
                confidence=0.8,
                extracted_topic=topic,
                reasoning="Matched follow-up indicator with existing topic",
            )

        # 5. If there's an existing topic and the input is short, treat as follow-up
        if self.state.current_topic and len(lower.split()) <= 3:
            return IntentClassification(
                intent=ConversationIntent.FOLLOW_UP,
                confidence=0.6,
                extracted_topic=topic,
                reasoning="Short input with existing topic context",
            )

        # 6. Default: new topic
        return IntentClassification(
            intent=ConversationIntent.NEW_TOPIC,
            confidence=0.75,
            extracted_topic=topic,
            reasoning="No specific indicator matched — treated as new topic",
        )

    @staticmethod
    def _matches_any(text: str, indicators: list[str]) -> bool:
        """Check if text starts with or contains any of the indicator phrases."""
        for indicator in indicators:
            if indicator in text:
                return True
        return False

    @staticmethod
    def _extract_branch_topic(text: str) -> Optional[str]:
        """Try to extract a branch sub-topic from branch-indicator phrases.

        Returns the extracted sub-topic or None if no branch indicator found.
        """
        for indicator in _BRANCH_INDICATORS:
            if indicator in text:
                # The branch topic is the text after the indicator
                idx = text.index(indicator) + len(indicator)
                remainder = text[idx:].strip().rstrip("?.!,;:")
                if remainder:
                    return remainder
                return None  # indicator present but no topic after it
        return None

    @staticmethod
    def _is_branch_exit(text: str) -> bool:
        """Check if the text is a request to exit the current branch."""
        lower = text.lower().strip()
        exit_phrases = [
            "go back",
            "return to",
            "exit branch",
            "close branch",
            "back to main",
            "leave branch",
            "return to main",
        ]
        return any(phrase in lower for phrase in exit_phrases)
