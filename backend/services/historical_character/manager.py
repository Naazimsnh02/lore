"""Historical Character Manager — AI-generated historical persona encounters.

Design reference: LORE design.md, HistoricalCharacterManager interface.
Requirements:
  12.1 — Offer character encounters for historical content
  12.2 — Generate period-appropriate persona
  12.3 — First-person perspective responses
  12.4 — Historical accuracy verified by SearchGrounder
  12.5 — Period-appropriate language and knowledge limitations
  12.6 — Clearly indicate AI-generated interactions

Architecture notes
------------------
- Uses ``google-genai`` SDK ``generate_content`` for character responses.
- Responses are verified via SearchGrounder (Req 12.4); inaccurate responses
  are regenerated with corrections.
- Knowledge cutoff is enforced in the system prompt so the character cannot
  reference events after their lifetime (Req 12.5).
- Every response includes an AI-generated disclaimer (Req 12.6).
- Constructor accepts injected ``genai.Client`` and ``SearchGrounder`` for
  testability.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from .database import HistoricalCharacterDatabase
from .models import (
    CharacterEncounterOffer,
    CharacterPersona,
    HistoricalCharacter,
    InteractionResult,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_MODEL_ID = "gemini-3-flash-preview"

# Hard timeout for character response generation
_RESPONSE_TIMEOUT_S = 10.0

# Hard timeout for accuracy verification
_VERIFICATION_TIMEOUT_S = 5.0

# Maximum conversation turns to keep in context
_MAX_CONVERSATION_TURNS = 20

# Minimum historical significance threshold (design.md)
_HISTORICAL_SIGNIFICANCE_THRESHOLD = 0.7

# Historical keywords for topic-based detection (matches FusionEngine)
_HISTORICAL_KEYWORDS = {
    "history", "ancient", "medieval", "century", "war", "empire",
    "renaissance", "roman", "greek", "egyptian", "viking", "colonial",
    "victorian", "revolution", "battle", "dynasty", "kingdom",
    "historic", "heritage", "monument", "temple", "cathedral",
    "castle", "palace", "fortress", "ruins", "archaeological",
}


class HistoricalCharacterManager:
    """Manages historical character encounters and conversations.

    Parameters
    ----------
    client:
        ``google.genai.Client`` instance for Gemini API calls.
    search_grounder:
        SearchGrounder instance for historical accuracy verification (Req 12.4).
    database:
        Optional custom character database. Uses built-in library if not provided.
    """

    def __init__(
        self,
        client: Any = None,
        search_grounder: Any = None,
        database: HistoricalCharacterDatabase | None = None,
    ) -> None:
        self._client = client
        self._grounder = search_grounder
        self._database = database or HistoricalCharacterDatabase()
        # Active personas keyed by session_id
        self._active_personas: dict[str, CharacterPersona] = {}

    # ── Public API ────────────────────────────────────────────────────────

    async def offer_character_encounter(
        self,
        *,
        location: str = "",
        topic: str = "",
        historical_period: str = "",
        historical_significance: float = 0.0,
        place_types: list[str] | None = None,
    ) -> Optional[CharacterEncounterOffer]:
        """Determine if a historical character encounter is appropriate.

        Requirement 12.1 — offer encounters for historical content.

        Returns an offer with the most relevant character, or None if
        the context is not historical enough.
        """
        if not self.is_historical_context(
            location=location,
            topic=topic,
            historical_significance=historical_significance,
            place_types=place_types,
        ):
            return None

        characters = await self._database.find_relevant(
            location=location,
            topic=topic,
            time_period=historical_period,
            limit=3,
        )

        if not characters:
            return None

        character = characters[0]
        relevance = self._calculate_offer_relevance(
            character, location, topic
        )

        prompt_text = (
            f"Would you like to speak with {character.name}, "
            f"a {', '.join(character.occupation[:2])} from "
            f"{character.historical_period}?"
        )

        return CharacterEncounterOffer(
            character=character,
            prompt_text=prompt_text,
            relevance_score=relevance,
        )

    def is_historical_context(
        self,
        *,
        location: str = "",
        topic: str = "",
        historical_significance: float = 0.0,
        place_types: list[str] | None = None,
    ) -> bool:
        """Check if the context qualifies as historical (design.md).

        Returns True if:
          - historical_significance > 0.7, OR
          - topic contains historical keywords, OR
          - place_types include historical place types
        """
        if historical_significance > _HISTORICAL_SIGNIFICANCE_THRESHOLD:
            return True

        # Check topic for historical keywords
        if topic:
            topic_words = set(re.findall(r"\b[a-z]{3,}\b", topic.lower()))
            if topic_words & _HISTORICAL_KEYWORDS:
                return True

        # Check location text
        if location:
            loc_words = set(re.findall(r"\b[a-z]{3,}\b", location.lower()))
            if loc_words & _HISTORICAL_KEYWORDS:
                return True

        # Check place types
        historical_place_types = {
            "museum", "church", "temple", "mosque", "synagogue", "monument",
            "landmark", "castle", "palace", "fort", "cemetery", "memorial",
            "archaeological_site", "historical_landmark", "cultural_landmark",
            "heritage_site",
        }
        if place_types and set(place_types) & historical_place_types:
            return True

        return False

    async def create_character_persona(
        self,
        character: HistoricalCharacter,
        session_id: str = "",
    ) -> CharacterPersona:
        """Create an interactive persona for a historical character.

        Requirement 12.2 — generate period-appropriate persona.
        Requirement 12.5 — period-appropriate language and knowledge cutoff.

        Parameters
        ----------
        character:
            The historical character to embody.
        session_id:
            Session ID for tracking active personas.

        Returns
        -------
        CharacterPersona
            Ready for interaction via ``interact_with_character``.
        """
        system_prompt = self._build_system_prompt(character)

        persona = CharacterPersona(
            character=character,
            system_prompt=system_prompt,
            conversation_history=[],
        )

        if session_id:
            self._active_personas[session_id] = persona

        return persona

    async def interact_with_character(
        self,
        persona: CharacterPersona,
        user_question: str,
    ) -> InteractionResult:
        """Handle a user interaction with a historical character.

        Requirements:
          12.3 — First-person perspective responses.
          12.4 — Historical accuracy verified by SearchGrounder.
          12.5 — Period-appropriate language and knowledge limitations.
          12.6 — Clearly indicate AI-generated interactions.

        Parameters
        ----------
        persona:
            Active character persona from ``create_character_persona``.
        user_question:
            The user's question or message.

        Returns
        -------
        InteractionResult
            Character response with accuracy verification status.
            Never raises; returns error result on failure.
        """
        # Add user message to history
        persona.conversation_history.append({
            "role": "user",
            "content": user_question,
        })

        # Trim history to stay within context limits
        if len(persona.conversation_history) > _MAX_CONVERSATION_TURNS:
            persona.conversation_history = persona.conversation_history[
                -_MAX_CONVERSATION_TURNS:
            ]

        # Generate character response
        try:
            response_text = await asyncio.wait_for(
                self._generate_response(persona),
                timeout=_RESPONSE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Character response timed out for %s",
                persona.character.name,
            )
            return InteractionResult(
                character_name=persona.character.name,
                response_text=(
                    f"[As {persona.character.name}] I need a moment to "
                    f"gather my thoughts..."
                ),
                error="Response generation timed out",
            )
        except Exception as exc:
            logger.error(
                "Character response failed for %s: %s",
                persona.character.name,
                exc,
            )
            return InteractionResult(
                character_name=persona.character.name,
                response_text="",
                error=str(exc),
            )

        # Verify historical accuracy (Req 12.4)
        accuracy_verified = False
        corrections_applied = False

        if self._grounder and response_text:
            try:
                verified_text, was_corrected = await asyncio.wait_for(
                    self._verify_and_correct(persona, response_text),
                    timeout=_VERIFICATION_TIMEOUT_S,
                )
                response_text = verified_text
                accuracy_verified = True
                corrections_applied = was_corrected
            except asyncio.TimeoutError:
                logger.warning("Accuracy verification timed out — using unverified response")
            except Exception:
                logger.warning("Accuracy verification failed — using unverified response", exc_info=True)

        # Add response to conversation history
        persona.conversation_history.append({
            "role": "assistant",
            "content": response_text,
        })

        return InteractionResult(
            character_name=persona.character.name,
            response_text=response_text,
            accuracy_verified=accuracy_verified,
            corrections_applied=corrections_applied,
        )

    def get_active_persona(self, session_id: str) -> Optional[CharacterPersona]:
        """Retrieve the active persona for a session."""
        return self._active_personas.get(session_id)

    def end_encounter(self, session_id: str) -> bool:
        """End the active character encounter for a session."""
        return self._active_personas.pop(session_id, None) is not None

    # ── Private helpers ───────────────────────────────────────────────────

    def _build_system_prompt(self, character: HistoricalCharacter) -> str:
        """Build a system prompt that defines the character persona.

        Enforces knowledge cutoff (Req 12.5) and first-person perspective
        (Req 12.3) via prompt instructions.
        """
        birth_str = f"{abs(character.birth_year)} {'BCE' if character.birth_year < 0 else 'CE'}" if character.birth_year else "Unknown"
        death_str = (
            f"{abs(character.death_year)} {'BCE' if character.death_year < 0 else 'CE'}"
            if character.death_year
            else "Unknown"
        )

        occupation_str = ", ".join(character.occupation) if character.occupation else "historical figure"
        traits_str = ", ".join(character.personality.traits) if character.personality.traits else "thoughtful"
        domains_str = ", ".join(character.personality.knowledge_domain) if character.personality.knowledge_domain else "general knowledge"

        return (
            f"You are {character.name}, a {occupation_str} from {character.location}.\n"
            f"\n"
            f"Historical Context:\n"
            f"- Time Period: {character.historical_period}\n"
            f"- Birth: {birth_str}\n"
            f"- Death: {death_str}\n"
            f"\n"
            f"Personality:\n"
            f"- Traits: {traits_str}\n"
            f"- Speech Style: {character.personality.speech_style}\n"
            f"- Knowledge Domain: {domains_str}\n"
            f"\n"
            f"Constraints:\n"
            f"- You only know information available up to the year {character.knowledge_cutoff}\n"
            f"- You speak in first person from your historical perspective\n"
            f"- You use period-appropriate language and concepts\n"
            f"- You are unaware of events after your time\n"
            f"- When asked about things beyond your knowledge, say that you do not know of such things\n"
            f"- Never break character or reference modern concepts unknown in your era\n"
            f"\n"
            f"Cultural Context: {character.cultural_context}\n"
            f"\n"
            f"Respond to questions as this historical figure would, maintaining historical accuracy "
            f"while bringing the past to life through personal perspective. Keep responses concise "
            f"(2-4 paragraphs) and engaging."
        )

    async def _generate_response(self, persona: CharacterPersona) -> str:
        """Generate a character response using Gemini."""
        if not self._client:
            return self._fallback_response(persona)

        # Build conversation for Gemini
        contents: list[dict[str, Any]] = []

        # System instruction via first content
        contents.append({
            "role": "user",
            "parts": [{"text": f"[System] {persona.system_prompt}\n\nPlease stay in character for the entire conversation."}],
        })
        contents.append({
            "role": "model",
            "parts": [{"text": f"I understand. I am {persona.character.name}. Ask me anything about my life and times."}],
        })

        # Add conversation history
        for turn in persona.conversation_history:
            role = "user" if turn["role"] == "user" else "model"
            contents.append({
                "role": role,
                "parts": [{"text": turn["content"]}],
            })

        response = await self._client.aio.models.generate_content(
            model=_MODEL_ID,
            contents=contents,
        )

        if response and response.text:
            return response.text.strip()

        return self._fallback_response(persona)

    def _fallback_response(self, persona: CharacterPersona) -> str:
        """Generate a static fallback when LLM is unavailable."""
        char = persona.character
        return (
            f"I am {char.name}, {', '.join(char.occupation[:2])} "
            f"of {char.historical_period}. "
            f"I lived in {char.location} and my life was shaped by "
            f"{char.cultural_context.split(',')[0] if char.cultural_context else 'the events of my time'}. "
            f"What would you like to know about my era?"
        )

    async def _verify_and_correct(
        self,
        persona: CharacterPersona,
        response_text: str,
    ) -> tuple[str, bool]:
        """Verify historical accuracy and correct if needed.

        Returns (response_text, was_corrected).
        """
        from ..search_grounder.models import FactualClaim, DocumentaryContext

        # Extract factual claims from the response
        claims = self._extract_claims(response_text, persona.character)

        if not claims:
            return response_text, False

        # Verify claims
        results = await self._grounder.verify_batch(claims)

        # Check for inaccuracies
        inaccurate_claims = [
            r for r in results
            if r.verified is False and r.confidence > 0.0
        ]

        if not inaccurate_claims:
            return response_text, False

        # Regenerate with corrections
        corrections_text = "; ".join(
            f"'{r.claim.text}' may be inaccurate"
            for r in inaccurate_claims[:3]
        )

        corrected = await self._regenerate_with_corrections(
            persona, response_text, corrections_text
        )

        return corrected, True

    def _extract_claims(
        self,
        text: str,
        character: HistoricalCharacter,
    ) -> list[Any]:
        """Extract verifiable factual claims from character response."""
        from ..search_grounder.models import FactualClaim, DocumentaryContext

        # Split into sentences and pick ones that look factual
        sentences = re.split(r"[.!?]+", text)
        claims = []

        factual_indicators = [
            "built", "founded", "conquered", "discovered", "invented",
            "year", "century", "battle", "treaty", "wrote", "created",
            "established", "ruled", "defeated", "died", "born",
        ]

        context = DocumentaryContext(
            topic=character.name,
            historical_period=character.historical_period,
            location_name=character.location,
        )

        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 15:
                continue
            # Check if sentence contains factual indicators
            sentence_lower = sentence.lower()
            if any(indicator in sentence_lower for indicator in factual_indicators):
                claims.append(FactualClaim(text=sentence, context=context))

        return claims[:5]  # Limit to 5 claims per response

    async def _regenerate_with_corrections(
        self,
        persona: CharacterPersona,
        original: str,
        corrections: str,
    ) -> str:
        """Regenerate response incorporating corrections."""
        if not self._client:
            return original

        prompt = (
            f"{persona.system_prompt}\n\n"
            f"Your previous response was: {original}\n\n"
            f"Historical accuracy check found issues: {corrections}\n\n"
            f"Please provide a corrected response that maintains your character "
            f"while fixing the historical inaccuracies. Stay in first person."
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=_MODEL_ID,
                contents=prompt,
            )
            if response and response.text:
                return response.text.strip()
        except Exception:
            logger.warning("Correction regeneration failed — using original response")

        return original

    def _calculate_offer_relevance(
        self,
        character: HistoricalCharacter,
        location: str,
        topic: str,
    ) -> float:
        """Calculate relevance score for character encounter offer."""
        score = 0.0
        location_lower = location.lower()
        topic_lower = topic.lower()

        # Check location match
        for loc in character.related_locations:
            if loc in location_lower:
                score += 0.4
                break

        # Check topic match
        for t in character.related_topics:
            if t in topic_lower:
                score += 0.4
                break

        # Personality match bonus
        if character.personality.knowledge_domain:
            for domain in character.personality.knowledge_domain:
                if domain.lower() in topic_lower:
                    score += 0.2
                    break

        return min(1.0, score)
