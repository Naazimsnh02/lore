"""Alternate History Detector — what-if question detection and scenario extraction.

Design reference: LORE design.md — AlternateHistoryDetector class.
Requirements:
  15.1 — Enable what-if scenario generation in LoreMode
  15.2 — Generate plausible alternative narratives
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from .models import HistoricalEvent, WhatIfQuestion

logger = logging.getLogger(__name__)

# ── What-if detection patterns ───────────────────────────────────────────────

_WHAT_IF_PATTERNS: list[str] = [
    r"\bwhat if\b",
    r"\bimagine if\b",
    r"\bsuppose\b",
    r"\bwhat would happen if\b",
    r"\bhow would .+ be different if\b",
    r"\bwhat could have happened if\b",
    r"\balternate history\b",
    r"\balternative history\b",
    r"\bwhat would .+ look like if\b",
    r"\bhow might .+ have changed if\b",
]

# Prefixes stripped to extract the core scenario
_QUESTION_PREFIXES: list[str] = [
    r"what if\s+",
    r"imagine if\s+",
    r"suppose\s+(?:that\s+)?",
    r"what would happen if\s+",
    r"how would .+ be different if\s+",
    r"what could have happened if\s+",
    r"alternate history:?\s*",
    r"alternative history:?\s*",
    r"what would .+ look like if\s+",
    r"how might .+ have changed if\s+",
]

# Gemini model for scenario extraction
_MODEL_ID = "gemini-3-flash-preview"

# Timeout for LLM-based extraction
_EXTRACTION_TIMEOUT_S = 5.0


class AlternateHistoryDetector:
    """Detects what-if questions and extracts scenario details.

    Parameters
    ----------
    genai_client:
        An initialised ``google.genai.Client`` instance.  When ``None``,
        scenario extraction falls back to heuristic-only parsing.
    """

    def __init__(self, genai_client: Any = None) -> None:
        self._client = genai_client

    # ── Detection ─────────────────────────────────────────────────────────────

    def is_what_if(self, text: str) -> bool:
        """Return ``True`` if *text* contains a what-if question pattern."""
        if not text:
            return False
        text_lower = text.lower()
        return any(re.search(p, text_lower) for p in _WHAT_IF_PATTERNS)

    # ── Extraction ────────────────────────────────────────────────────────────

    async def extract_scenario(self, text: str) -> WhatIfQuestion:
        """Parse a what-if question into structured components.

        Attempts LLM-based extraction first (if client available), then
        falls back to heuristic parsing.
        """
        if not text:
            return WhatIfQuestion(original_question=text)

        # Try LLM extraction for richer results
        if self._client:
            try:
                return await self._extract_with_llm(text)
            except Exception:
                logger.warning(
                    "LLM extraction failed for '%s', falling back to heuristics",
                    text[:80],
                    exc_info=True,
                )

        return self._extract_heuristic(text)

    # ── LLM-based extraction ──────────────────────────────────────────────────

    async def _extract_with_llm(self, text: str) -> WhatIfQuestion:
        """Use Gemini to parse the what-if question into structured fields."""
        import asyncio

        prompt = (
            "You are a historian parsing a what-if question. Extract the following "
            "as JSON (no markdown fences):\n"
            '  "base_event_name": string — the real historical event being modified\n'
            '  "base_event_date": string — approximate date or period\n'
            '  "base_event_location": string — geographic location\n'
            '  "base_event_description": string — brief description of what actually happened\n'
            '  "base_event_significance": string — why it mattered\n'
            '  "divergence_point": string — the specific change the user is imagining\n'
            "\n"
            f"Question: {text}\n"
        )

        response = await asyncio.wait_for(
            self._client.aio.models.generate_content(
                model=_MODEL_ID,
                contents=prompt,
            ),
            timeout=_EXTRACTION_TIMEOUT_S,
        )

        raw = response.text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)

        event = HistoricalEvent(
            name=data.get("base_event_name", "Unknown"),
            date=data.get("base_event_date", ""),
            location=data.get("base_event_location", ""),
            description=data.get("base_event_description", ""),
            significance=data.get("base_event_significance", ""),
        )

        return WhatIfQuestion(
            original_question=text,
            base_event=event,
            divergence_point=data.get("divergence_point", ""),
        )

    # ── Heuristic extraction ─────────────────────────────────────────────────

    def _extract_heuristic(self, text: str) -> WhatIfQuestion:
        """Extract scenario details using simple text heuristics."""
        scenario_text = text
        for prefix in _QUESTION_PREFIXES:
            scenario_text = re.sub(
                f"^{prefix}", "", scenario_text.strip(), flags=re.IGNORECASE
            )

        # Clean up trailing punctuation
        scenario_text = scenario_text.strip().rstrip("?.")

        # Simple split: the scenario text IS the divergence point, and
        # we try to extract an event name from it
        event_name = self._guess_event_name(scenario_text)

        return WhatIfQuestion(
            original_question=text,
            base_event=HistoricalEvent(name=event_name),
            divergence_point=scenario_text,
        )

    @staticmethod
    def _guess_event_name(text: str) -> str:
        """Best-effort extraction of an event or subject name from text.

        Looks for capitalised proper nouns or falls back to the first
        meaningful clause.
        """
        # Find sequences of capitalised words (likely proper nouns)
        proper_nouns = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", text)
        if proper_nouns:
            # Take the longest proper noun sequence
            return max(proper_nouns, key=len)

        # Fallback: first clause (up to a comma or 60 chars)
        clause = text.split(",")[0].strip()
        if len(clause) > 60:
            clause = clause[:60].rsplit(" ", 1)[0] + "…"
        return clause if clause else "Unknown"
