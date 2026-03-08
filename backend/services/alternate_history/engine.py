"""Alternate History Engine — generates plausible what-if scenarios.

Design reference: LORE design.md — Alternate History Workflow.
Requirements:
  15.1 — Enable what-if scenario generation in LoreMode
  15.2 — Generate plausible alternative historical narratives
  15.3 — Ground scenarios in verified historical facts
  15.4 — Clearly label content as speculative
  15.5 — Explain causal reasoning for alternative outcomes
  15.6 — Create speculative video content (via Veo, downstream)

Architecture notes
------------------
The engine orchestrates:
  1. Detection — AlternateHistoryDetector identifies what-if questions
  2. Grounding — SearchGrounder verifies base historical facts (Req 15.3)
  3. Generation — Gemini generates plausible alternative narratives (Req 15.2)
  4. Causal reasoning — builds a causal chain (Req 15.5)
  5. Labeling — wraps all output as speculative (Req 15.4)

Constructor accepts injected dependencies for testability.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Optional

from .detector import AlternateHistoryDetector
from .models import (
    AlternateHistoryScenario,
    CausalLink,
    ContentLabel,
    HistoricalEvent,
    ScenarioStatus,
    SpeculativeContent,
    WhatIfQuestion,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_MODEL_ID = "gemini-3-flash-preview"
_GENERATION_TIMEOUT_S = 10.0
_GROUNDING_TIMEOUT_S = 5.0


class AlternateHistoryEngine:
    """Generates grounded alternate history scenarios.

    Parameters
    ----------
    genai_client:
        An initialised ``google.genai.Client`` for narrative generation.
    search_grounder:
        SearchGrounder instance for historical fact verification (Req 15.3).
    detector:
        AlternateHistoryDetector instance.  If ``None``, one is created
        using *genai_client*.
    """

    def __init__(
        self,
        genai_client: Any = None,
        search_grounder: Any = None,
        detector: Optional[AlternateHistoryDetector] = None,
    ) -> None:
        self._client = genai_client
        self._grounder = search_grounder
        self._detector = detector or AlternateHistoryDetector(genai_client)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def detector(self) -> AlternateHistoryDetector:
        """Access the underlying detector for what-if detection."""
        return self._detector

    def is_what_if(self, text: str) -> bool:
        """Convenience proxy to ``detector.is_what_if``."""
        return self._detector.is_what_if(text)

    async def generate_scenario(
        self,
        question: str,
        *,
        session_id: str = "",
        location: str = "",
        context_topic: str = "",
    ) -> AlternateHistoryScenario:
        """Full pipeline: detect → extract → ground → generate → label.

        Parameters
        ----------
        question:
            The user's what-if question text.
        session_id:
            Current session identifier.
        location:
            Optional location context (from camera / GPS).
        context_topic:
            Optional broader topic context from the conversation.

        Returns
        -------
        AlternateHistoryScenario
            A fully populated scenario, or one with ``status=FAILED`` and
            an ``error`` message on failure.
        """
        # Initialize with a placeholder WhatIfQuestion; will be replaced after extraction
        scenario = AlternateHistoryScenario(
            session_id=session_id,
            what_if_question=WhatIfQuestion(original_question=question),
        )

        try:
            # Step 1: Extract structured scenario from question
            scenario.status = ScenarioStatus.GROUNDING
            what_if = await self._detector.extract_scenario(question)
            scenario.what_if_question = what_if
            scenario.base_event = what_if.base_event
            scenario.divergence_point = what_if.divergence_point

            # Step 2: Ground in historical facts (Req 15.3)
            grounding = await self._ground_historical_facts(what_if, location)
            scenario.historical_grounding = grounding

            # Enrich base event description from grounding results
            if grounding and not scenario.base_event.description:
                scenario.base_event = self._enrich_event(
                    scenario.base_event, grounding
                )

            # Step 3: Generate alternative narrative + causal chain (Req 15.2, 15.5)
            scenario.status = ScenarioStatus.GENERATING
            narrative, causal_chain, plausibility = await self._generate_narrative(
                what_if=what_if,
                grounding=grounding,
                location=location,
                context_topic=context_topic,
            )
            scenario.alternative_narrative = narrative
            scenario.causal_chain = causal_chain
            scenario.plausibility = plausibility

            scenario.status = ScenarioStatus.COMPLETED
            logger.info(
                "Generated alternate history scenario %s for '%s' (plausibility=%.2f)",
                scenario.scenario_id,
                question[:60],
                plausibility,
            )

        except Exception as exc:
            scenario.status = ScenarioStatus.FAILED
            scenario.error = str(exc)
            logger.error(
                "Alternate history generation failed for '%s': %s",
                question[:60],
                exc,
                exc_info=True,
            )

        return scenario

    def label_speculative(
        self,
        text: str,
        scenario_id: str = "",
        label: ContentLabel = ContentLabel.SPECULATIVE,
    ) -> SpeculativeContent:
        """Wrap text with a speculative label (Req 15.4).

        All content from the alternate history engine must be clearly
        marked as speculative so the UI can display appropriate disclaimers.
        """
        return SpeculativeContent(
            label=label,
            text=text,
            source_scenario_id=scenario_id,
        )

    def build_narration_instructions(
        self, scenario: AlternateHistoryScenario
    ) -> str:
        """Build custom narration instructions from a completed scenario.

        Used by the Orchestrator to pass context to the NarrationEngine
        so the generated narration correctly frames the alternate history.
        """
        parts = [
            "Generate a speculative 'what if' alternate history narration.",
            "IMPORTANT: Clearly distinguish between established historical facts "
            "and speculative elements throughout.",
        ]

        if scenario.base_event.name != "Unknown":
            parts.append(
                f"Base historical event: {scenario.base_event.name}"
                f" ({scenario.base_event.date})."
                if scenario.base_event.date
                else f"Base historical event: {scenario.base_event.name}."
            )

        if scenario.divergence_point:
            parts.append(f"Point of divergence: {scenario.divergence_point}.")

        if scenario.alternative_narrative:
            parts.append(
                f"Alternative narrative summary: {scenario.alternative_narrative[:500]}"
            )

        if scenario.causal_chain:
            chain_text = " → ".join(
                f"{link.from_event} leads to {link.to_event}"
                for link in scenario.causal_chain[:5]
            )
            parts.append(f"Causal chain: {chain_text}.")

        if scenario.historical_grounding:
            parts.append(
                f"This scenario is grounded in {len(scenario.historical_grounding)} "
                "verified historical sources."
            )

        parts.append(
            "Begin with the verified facts, then transition into the speculative "
            "scenario using phrases like 'But imagine if...' or 'In this alternate "
            "timeline...'. End by reflecting on what this tells us about historical "
            "contingency."
        )

        return " ".join(parts)

    def build_illustration_instructions(
        self, scenario: AlternateHistoryScenario
    ) -> str:
        """Build custom illustration prompt from a completed scenario."""
        parts = [
            "Create a speculative illustration for an alternate history scenario.",
            f'Question: "{scenario.what_if_question.original_question}"',
        ]

        if scenario.divergence_point:
            parts.append(f"Divergence: {scenario.divergence_point}.")

        if scenario.base_event.location:
            parts.append(f"Location: {scenario.base_event.location}.")

        parts.append(
            "Style: dramatic, cinematic, slightly surreal to convey the "
            "speculative nature. Include a subtle visual indicator (e.g., "
            "a shimmering border or sepia tint) to mark this as alternate history."
        )

        return " ".join(parts)

    # ── Historical grounding (Req 15.3) ───────────────────────────────────────

    async def _ground_historical_facts(
        self,
        what_if: WhatIfQuestion,
        location: str = "",
    ) -> list[dict[str, Any]]:
        """Verify the base historical event via SearchGrounder."""
        if not self._grounder:
            logger.warning("No SearchGrounder available; skipping fact grounding")
            return []

        try:
            from ..search_grounder.models import (
                DocumentaryContext,
                FactualClaim,
            )
        except ImportError:
            logger.warning("SearchGrounder models not available")
            return []

        claims_text = []
        if what_if.base_event.name and what_if.base_event.name != "Unknown":
            claims_text.append(
                f"Historical event: {what_if.base_event.name}. "
                f"{what_if.base_event.description}"
            )
        if what_if.divergence_point:
            claims_text.append(
                f"Historical context for: {what_if.divergence_point}"
            )

        if not claims_text:
            return []

        ctx = DocumentaryContext(topic=what_if.original_question, mode="lore")
        if location:
            ctx.location_name = location

        claims = [
            FactualClaim(text=text, context=ctx) for text in claims_text
        ]

        try:
            results = await asyncio.wait_for(
                self._grounder.verify_batch(claims),
                timeout=_GROUNDING_TIMEOUT_S,
            )
            grounding = []
            for result in results:
                entry: dict[str, Any] = {
                    "claim": result.claim.text,
                    "verified": result.verified,
                    "confidence": result.confidence,
                    "sources": [
                        {"url": s.url, "title": s.title, "authority": s.authority.value}
                        for s in result.sources
                    ],
                }
                grounding.append(entry)
            return grounding

        except asyncio.TimeoutError:
            logger.warning("Historical fact grounding timed out")
            return []
        except Exception:
            logger.warning("Historical fact grounding failed", exc_info=True)
            return []

    # ── Narrative generation (Req 15.2, 15.5) ────────────────────────────────

    async def _generate_narrative(
        self,
        what_if: WhatIfQuestion,
        grounding: list[dict[str, Any]],
        location: str = "",
        context_topic: str = "",
    ) -> tuple[str, list[CausalLink], float]:
        """Generate alternative narrative and causal chain via Gemini.

        Returns
        -------
        tuple of (narrative_text, causal_chain, plausibility_score)
        """
        if not self._client:
            return self._generate_fallback(what_if)

        grounding_summary = ""
        if grounding:
            facts = [g["claim"] for g in grounding if g.get("verified")]
            if facts:
                grounding_summary = "Verified facts: " + "; ".join(facts)

        prompt = self._build_generation_prompt(
            what_if, grounding_summary, location, context_topic
        )

        try:
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=_MODEL_ID,
                    contents=prompt,
                ),
                timeout=_GENERATION_TIMEOUT_S,
            )

            raw = response.text.strip()
            return self._parse_generation_response(raw, what_if)

        except asyncio.TimeoutError:
            logger.warning("Narrative generation timed out")
            return self._generate_fallback(what_if)
        except Exception:
            logger.warning("Narrative generation failed", exc_info=True)
            return self._generate_fallback(what_if)

    def _build_generation_prompt(
        self,
        what_if: WhatIfQuestion,
        grounding_summary: str,
        location: str,
        context_topic: str,
    ) -> str:
        """Build the Gemini prompt for narrative generation."""
        parts = [
            "You are a historian and storyteller. Generate an alternate history "
            "scenario in JSON (no markdown fences) with these fields:",
            '  "narrative": string — a plausible 2-4 paragraph alternative history narrative',
            '  "causal_chain": array of {"from_event": str, "to_event": str, '
            '"reasoning": str, "confidence": float 0-1}',
            '  "plausibility": float 0-1 — how historically plausible this scenario is',
            "",
            f"What-if question: {what_if.original_question}",
        ]

        if what_if.base_event.name != "Unknown":
            parts.append(f"Base event: {what_if.base_event.name}")
        if what_if.divergence_point:
            parts.append(f"Divergence point: {what_if.divergence_point}")
        if grounding_summary:
            parts.append(grounding_summary)
        if location:
            parts.append(f"Location context: {location}")
        if context_topic:
            parts.append(f"Broader topic: {context_topic}")

        parts.append(
            "\nGround your narrative in real historical facts. The causal chain "
            "should explain step by step how the divergence leads to different "
            "outcomes. Be specific and plausible."
        )

        return "\n".join(parts)

    def _parse_generation_response(
        self, raw: str, what_if: WhatIfQuestion
    ) -> tuple[str, list[CausalLink], float]:
        """Parse Gemini's JSON response into structured output."""
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # If JSON parsing fails, treat the entire response as narrative
            logger.warning("Failed to parse generation response as JSON")
            return raw[:2000], [], 0.5

        narrative = data.get("narrative", "")
        plausibility = float(data.get("plausibility", 0.5))
        plausibility = max(0.0, min(1.0, plausibility))

        causal_chain: list[CausalLink] = []
        for link_data in data.get("causal_chain", []):
            try:
                causal_chain.append(
                    CausalLink(
                        from_event=link_data.get("from_event", ""),
                        to_event=link_data.get("to_event", ""),
                        reasoning=link_data.get("reasoning", ""),
                        confidence=max(
                            0.0, min(1.0, float(link_data.get("confidence", 0.5)))
                        ),
                    )
                )
            except (ValueError, TypeError):
                continue

        return narrative, causal_chain, plausibility

    @staticmethod
    def _generate_fallback(
        what_if: WhatIfQuestion,
    ) -> tuple[str, list[CausalLink], float]:
        """Fallback when LLM generation is unavailable."""
        narrative = (
            f"Exploring the alternate history: {what_if.original_question}\n\n"
            f"Based on the historical event '{what_if.base_event.name}', "
            f"this scenario considers what might have happened if "
            f"{what_if.divergence_point or 'events had unfolded differently'}. "
            f"While we can only speculate, examining such alternatives "
            f"helps us understand the contingent nature of history."
        )
        return narrative, [], 0.3

    @staticmethod
    def _enrich_event(
        event: HistoricalEvent, grounding: list[dict[str, Any]]
    ) -> HistoricalEvent:
        """Enrich a HistoricalEvent with details from grounding results."""
        data = event.model_dump()
        for entry in grounding:
            claim = entry.get("claim", "")
            if claim and not data.get("description"):
                data["description"] = claim
                break
        return HistoricalEvent(**data)
