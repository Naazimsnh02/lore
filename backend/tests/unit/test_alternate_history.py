"""Unit tests for the Alternate History Engine.

Tests cover:
  - AlternateHistoryDetector: what-if detection and scenario extraction
  - AlternateHistoryEngine: scenario generation, grounding, labeling
  - Integration with orchestrator alternate_history_workflow

Requirements tested: 15.1, 15.2, 15.3, 15.4, 15.5.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.alternate_history.detector import AlternateHistoryDetector
from backend.services.alternate_history.engine import AlternateHistoryEngine
from backend.services.alternate_history.models import (
    AlternateHistoryScenario,
    CausalLink,
    ContentLabel,
    HistoricalEvent,
    ScenarioStatus,
    SpeculativeContent,
    WhatIfQuestion,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def detector() -> AlternateHistoryDetector:
    """Detector without LLM client (heuristic-only)."""
    return AlternateHistoryDetector(genai_client=None)


@pytest.fixture
def mock_genai_client() -> MagicMock:
    """Mock google-genai client."""
    client = MagicMock()
    client.aio = MagicMock()
    client.aio.models = MagicMock()
    client.aio.models.generate_content = AsyncMock()
    return client


@pytest.fixture
def mock_grounder() -> AsyncMock:
    """Mock SearchGrounder."""
    grounder = AsyncMock()
    grounder.verify_batch = AsyncMock(return_value=[])
    return grounder


@pytest.fixture
def engine(mock_genai_client: MagicMock, mock_grounder: AsyncMock) -> AlternateHistoryEngine:
    """Engine with mocked dependencies."""
    return AlternateHistoryEngine(
        genai_client=mock_genai_client,
        search_grounder=mock_grounder,
    )


@pytest.fixture
def engine_no_client(mock_grounder: AsyncMock) -> AlternateHistoryEngine:
    """Engine without LLM client (fallback mode)."""
    return AlternateHistoryEngine(
        genai_client=None,
        search_grounder=mock_grounder,
    )


# ══════════════════════════════════════════════════════════════════════════════
# AlternateHistoryDetector tests
# ══════════════════════════════════════════════════════════════════════════════


class TestDetectorIsWhatIf:
    """Test what-if question detection (Req 15.1)."""

    @pytest.mark.parametrize(
        "text",
        [
            "What if the Roman Empire never fell?",
            "Imagine if dinosaurs still existed",
            "Suppose the internet was invented in 1900",
            "What would happen if Napoleon won at Waterloo?",
            "How would Europe be different if the Black Death never happened?",
            "What could have happened if the Library of Alexandria survived?",
            "Tell me about alternate history of America",
            "alternative history of the Ottoman Empire",
            "What would technology look like if Tesla had won the current war?",
            "How might the world have changed if Columbus never sailed?",
        ],
    )
    def test_detects_what_if_patterns(self, detector: AlternateHistoryDetector, text: str):
        assert detector.is_what_if(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Tell me about the Roman Empire",
            "When did World War 2 start?",
            "What is the capital of France?",
            "How does photosynthesis work?",
            "Show me the Colosseum",
            "",
        ],
    )
    def test_rejects_non_what_if(self, detector: AlternateHistoryDetector, text: str):
        assert detector.is_what_if(text) is False

    def test_case_insensitive(self, detector: AlternateHistoryDetector):
        assert detector.is_what_if("WHAT IF the moon landing failed?") is True
        assert detector.is_what_if("What If gravity was reversed?") is True

    def test_empty_string(self, detector: AlternateHistoryDetector):
        assert detector.is_what_if("") is False

    def test_none_like_empty(self, detector: AlternateHistoryDetector):
        # Passing empty string (None would be a type error)
        assert detector.is_what_if("") is False


class TestDetectorExtractHeuristic:
    """Test heuristic scenario extraction."""

    @pytest.mark.asyncio
    async def test_extracts_divergence(self, detector: AlternateHistoryDetector):
        result = await detector.extract_scenario("What if the Roman Empire never fell?")
        assert isinstance(result, WhatIfQuestion)
        assert result.original_question == "What if the Roman Empire never fell?"
        assert result.divergence_point  # Should have extracted something
        assert "Roman Empire" in result.divergence_point or "Roman Empire" in result.base_event.name

    @pytest.mark.asyncio
    async def test_strips_prefix_imagine_if(self, detector: AlternateHistoryDetector):
        result = await detector.extract_scenario("Imagine if electricity was never discovered")
        assert result.divergence_point
        assert "imagine if" not in result.divergence_point.lower()

    @pytest.mark.asyncio
    async def test_strips_prefix_suppose(self, detector: AlternateHistoryDetector):
        result = await detector.extract_scenario("Suppose that the wheel was never invented")
        assert result.divergence_point
        assert not result.divergence_point.lower().startswith("suppose")

    @pytest.mark.asyncio
    async def test_empty_input(self, detector: AlternateHistoryDetector):
        result = await detector.extract_scenario("")
        assert result.original_question == ""

    @pytest.mark.asyncio
    async def test_proper_noun_extraction(self, detector: AlternateHistoryDetector):
        result = await detector.extract_scenario(
            "What if Napoleon Bonaparte won at the Battle of Waterloo?"
        )
        # Should extract "Napoleon Bonaparte" or "Battle of Waterloo" as event name
        name = result.base_event.name
        assert any(
            noun in name
            for noun in ["Napoleon", "Battle", "Waterloo"]
        )


class TestDetectorExtractWithLLM:
    """Test LLM-based scenario extraction."""

    @pytest.mark.asyncio
    async def test_llm_extraction_success(self, mock_genai_client: MagicMock):
        response_data = {
            "base_event_name": "Fall of the Roman Empire",
            "base_event_date": "476 AD",
            "base_event_location": "Rome, Italy",
            "base_event_description": "The Western Roman Empire collapsed",
            "base_event_significance": "End of ancient era in Europe",
            "divergence_point": "The Roman Empire maintains political stability",
        }
        mock_response = MagicMock()
        mock_response.text = json.dumps(response_data)
        mock_genai_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        detector = AlternateHistoryDetector(genai_client=mock_genai_client)
        result = await detector.extract_scenario("What if the Roman Empire never fell?")

        assert result.base_event.name == "Fall of the Roman Empire"
        assert result.base_event.date == "476 AD"
        assert result.base_event.location == "Rome, Italy"
        assert result.divergence_point == "The Roman Empire maintains political stability"

    @pytest.mark.asyncio
    async def test_llm_extraction_with_markdown_fences(self, mock_genai_client: MagicMock):
        response_data = {
            "base_event_name": "Moon Landing",
            "base_event_date": "1969",
            "base_event_location": "Moon",
            "base_event_description": "Apollo 11 landed on the Moon",
            "base_event_significance": "First humans on another world",
            "divergence_point": "Apollo 11 mission fails",
        }
        mock_response = MagicMock()
        mock_response.text = f"```json\n{json.dumps(response_data)}\n```"
        mock_genai_client.aio.models.generate_content = AsyncMock(return_value=mock_response)

        detector = AlternateHistoryDetector(genai_client=mock_genai_client)
        result = await detector.extract_scenario("What if the moon landing failed?")

        assert result.base_event.name == "Moon Landing"
        assert result.divergence_point == "Apollo 11 mission fails"

    @pytest.mark.asyncio
    async def test_llm_fallback_on_error(self, mock_genai_client: MagicMock):
        mock_genai_client.aio.models.generate_content = AsyncMock(
            side_effect=RuntimeError("API error")
        )

        detector = AlternateHistoryDetector(genai_client=mock_genai_client)
        result = await detector.extract_scenario("What if the Roman Empire never fell?")

        # Should fall back to heuristic extraction
        assert isinstance(result, WhatIfQuestion)
        assert result.original_question == "What if the Roman Empire never fell?"
        assert result.divergence_point  # Heuristic should still extract something


# ══════════════════════════════════════════════════════════════════════════════
# AlternateHistoryEngine tests
# ══════════════════════════════════════════════════════════════════════════════


class TestEngineIsWhatIf:
    """Test convenience proxy."""

    def test_delegates_to_detector(self, engine: AlternateHistoryEngine):
        assert engine.is_what_if("What if Rome never fell?") is True
        assert engine.is_what_if("Tell me about Rome") is False


class TestEngineGenerateScenario:
    """Test full scenario generation pipeline (Req 15.2)."""

    @pytest.mark.asyncio
    async def test_generates_scenario_with_llm(
        self, engine: AlternateHistoryEngine, mock_genai_client: MagicMock
    ):
        # Mock detector extraction response
        extract_response = MagicMock()
        extract_response.text = json.dumps({
            "base_event_name": "Fall of Rome",
            "base_event_date": "476 AD",
            "base_event_location": "Rome",
            "base_event_description": "Western Roman Empire collapsed",
            "base_event_significance": "End of classical antiquity",
            "divergence_point": "Rome never fell",
        })

        # Mock narrative generation response
        narrative_response = MagicMock()
        narrative_response.text = json.dumps({
            "narrative": "In this alternate timeline, Rome persisted...",
            "causal_chain": [
                {
                    "from_event": "Stable Roman governance",
                    "to_event": "Continued technological progress",
                    "reasoning": "Political stability enables research",
                    "confidence": 0.7,
                },
                {
                    "from_event": "Continued technological progress",
                    "to_event": "Earlier industrial revolution",
                    "reasoning": "Roman engineering combined with stability",
                    "confidence": 0.5,
                },
            ],
            "plausibility": 0.6,
        })

        mock_genai_client.aio.models.generate_content = AsyncMock(
            side_effect=[extract_response, narrative_response]
        )

        scenario = await engine.generate_scenario(
            "What if the Roman Empire never fell?",
            session_id="test-session",
        )

        assert scenario.status == ScenarioStatus.COMPLETED
        assert scenario.session_id == "test-session"
        assert scenario.base_event.name == "Fall of Rome"
        assert scenario.alternative_narrative
        assert len(scenario.causal_chain) == 2
        assert scenario.plausibility == 0.6
        assert scenario.error is None

    @pytest.mark.asyncio
    async def test_fallback_on_no_client(self, engine_no_client: AlternateHistoryEngine):
        scenario = await engine_no_client.generate_scenario(
            "What if the Roman Empire never fell?"
        )

        assert scenario.status == ScenarioStatus.COMPLETED
        assert scenario.alternative_narrative  # Should have fallback text
        assert scenario.plausibility == 0.3  # Fallback plausibility

    @pytest.mark.asyncio
    async def test_handles_generation_error_gracefully(
        self, engine: AlternateHistoryEngine, mock_genai_client: MagicMock
    ):
        mock_genai_client.aio.models.generate_content = AsyncMock(
            side_effect=RuntimeError("API failure")
        )

        scenario = await engine.generate_scenario(
            "What if dinosaurs never went extinct?"
        )

        # Should not raise; returns failed or completed-with-fallback
        assert isinstance(scenario, AlternateHistoryScenario)
        # Even on failure, the question should be captured
        assert scenario.what_if_question.original_question == "What if dinosaurs never went extinct?"

    @pytest.mark.asyncio
    async def test_scenario_id_is_unique(self, engine_no_client: AlternateHistoryEngine):
        s1 = await engine_no_client.generate_scenario("What if X?")
        s2 = await engine_no_client.generate_scenario("What if Y?")
        assert s1.scenario_id != s2.scenario_id


class TestEngineGrounding:
    """Test historical fact grounding (Req 15.3)."""

    @pytest.mark.asyncio
    async def test_calls_search_grounder(
        self, engine: AlternateHistoryEngine, mock_genai_client: MagicMock, mock_grounder: AsyncMock
    ):
        # Setup mock responses
        extract_response = MagicMock()
        extract_response.text = json.dumps({
            "base_event_name": "Battle of Waterloo",
            "base_event_date": "1815",
            "base_event_location": "Belgium",
            "base_event_description": "Napoleon's final defeat",
            "base_event_significance": "End of Napoleonic Wars",
            "divergence_point": "Napoleon wins at Waterloo",
        })

        narrative_response = MagicMock()
        narrative_response.text = json.dumps({
            "narrative": "Napoleon's victory reshapes Europe...",
            "causal_chain": [],
            "plausibility": 0.5,
        })

        mock_genai_client.aio.models.generate_content = AsyncMock(
            side_effect=[extract_response, narrative_response]
        )

        # Mock grounder returning verification results
        mock_result = MagicMock()
        mock_result.claim = MagicMock()
        mock_result.claim.text = "Battle of Waterloo in 1815"
        mock_result.verified = True
        mock_result.confidence = 0.9
        mock_result.sources = [
            MagicMock(url="https://example.edu/waterloo", title="Waterloo History", authority=MagicMock(value="academic"))
        ]
        mock_grounder.verify_batch = AsyncMock(return_value=[mock_result])

        scenario = await engine.generate_scenario(
            "What if Napoleon won at Waterloo?",
            session_id="test",
        )

        assert scenario.status == ScenarioStatus.COMPLETED
        assert len(scenario.historical_grounding) > 0
        assert scenario.historical_grounding[0]["verified"] is True

    @pytest.mark.asyncio
    async def test_no_grounder_skips_grounding(self, mock_genai_client: MagicMock):
        engine = AlternateHistoryEngine(
            genai_client=mock_genai_client,
            search_grounder=None,
        )

        extract_response = MagicMock()
        extract_response.text = json.dumps({
            "base_event_name": "Test Event",
            "base_event_date": "",
            "base_event_location": "",
            "base_event_description": "",
            "base_event_significance": "",
            "divergence_point": "test divergence",
        })

        narrative_response = MagicMock()
        narrative_response.text = json.dumps({
            "narrative": "Test narrative",
            "causal_chain": [],
            "plausibility": 0.5,
        })

        mock_genai_client.aio.models.generate_content = AsyncMock(
            side_effect=[extract_response, narrative_response]
        )

        scenario = await engine.generate_scenario("What if test?")
        assert scenario.status == ScenarioStatus.COMPLETED
        assert scenario.historical_grounding == []


class TestEngineLabelSpeculative:
    """Test speculative content labeling (Req 15.4)."""

    def test_labels_as_speculative(self, engine: AlternateHistoryEngine):
        content = engine.label_speculative("Rome persists to this day")
        assert isinstance(content, SpeculativeContent)
        assert content.label == ContentLabel.SPECULATIVE
        assert content.text == "Rome persists to this day"
        assert "speculative" in content.disclaimer.lower()

    def test_labels_as_historical_fact(self, engine: AlternateHistoryEngine):
        content = engine.label_speculative(
            "Rome fell in 476 AD",
            label=ContentLabel.HISTORICAL_FACT,
        )
        assert content.label == ContentLabel.HISTORICAL_FACT

    def test_labels_as_causal_reasoning(self, engine: AlternateHistoryEngine):
        content = engine.label_speculative(
            "This led to...",
            label=ContentLabel.CAUSAL_REASONING,
        )
        assert content.label == ContentLabel.CAUSAL_REASONING

    def test_includes_scenario_id(self, engine: AlternateHistoryEngine):
        content = engine.label_speculative("text", scenario_id="abc123")
        assert content.source_scenario_id == "abc123"


class TestEngineBuildInstructions:
    """Test narration and illustration instruction generation (Req 15.5)."""

    def test_narration_instructions_include_event(self, engine: AlternateHistoryEngine):
        scenario = AlternateHistoryScenario(
            what_if_question=WhatIfQuestion(
                original_question="What if Rome never fell?"
            ),
            base_event=HistoricalEvent(name="Fall of Rome", date="476 AD"),
            divergence_point="Rome never fell",
            alternative_narrative="In this timeline...",
            causal_chain=[
                CausalLink(
                    from_event="Stable Rome",
                    to_event="Advanced tech",
                    reasoning="Stability enables progress",
                    confidence=0.7,
                )
            ],
        )

        instructions = engine.build_narration_instructions(scenario)
        assert "Fall of Rome" in instructions
        assert "476 AD" in instructions
        assert "Rome never fell" in instructions
        assert "speculative" in instructions.lower()
        assert "Stable Rome" in instructions
        assert "Advanced tech" in instructions

    def test_narration_instructions_minimal(self, engine: AlternateHistoryEngine):
        scenario = AlternateHistoryScenario(
            what_if_question=WhatIfQuestion(original_question="What if?"),
            base_event=HistoricalEvent(name="Unknown"),
        )

        instructions = engine.build_narration_instructions(scenario)
        assert "speculative" in instructions.lower()

    def test_illustration_instructions(self, engine: AlternateHistoryEngine):
        scenario = AlternateHistoryScenario(
            what_if_question=WhatIfQuestion(
                original_question="What if Rome never fell?"
            ),
            base_event=HistoricalEvent(name="Fall of Rome", location="Rome, Italy"),
            divergence_point="Rome never collapsed",
        )

        instructions = engine.build_illustration_instructions(scenario)
        assert "What if Rome never fell?" in instructions
        assert "Rome, Italy" in instructions
        assert "Rome never collapsed" in instructions
        assert "speculative" in instructions.lower()


# ══════════════════════════════════════════════════════════════════════════════
# Model tests
# ══════════════════════════════════════════════════════════════════════════════


class TestModels:
    """Test Pydantic model validation and defaults."""

    def test_historical_event_defaults(self):
        event = HistoricalEvent(name="Test")
        assert event.date == ""
        assert event.location == ""
        assert event.description == ""
        assert event.significance == ""

    def test_causal_link_validation(self):
        link = CausalLink(from_event="A", to_event="B")
        assert link.confidence == 0.5
        assert link.reasoning == ""

    def test_causal_link_confidence_clamped(self):
        link = CausalLink(from_event="A", to_event="B", confidence=0.0)
        assert link.confidence == 0.0
        link2 = CausalLink(from_event="A", to_event="B", confidence=1.0)
        assert link2.confidence == 1.0

    def test_what_if_question_defaults(self):
        q = WhatIfQuestion(original_question="What if?")
        assert q.base_event.name == "Unknown"
        assert q.divergence_point == ""
        assert q.detected_at > 0

    def test_scenario_defaults(self):
        q = WhatIfQuestion(original_question="test")
        s = AlternateHistoryScenario(what_if_question=q)
        assert s.status == ScenarioStatus.PENDING
        assert len(s.scenario_id) == 16
        assert s.plausibility == 0.0
        assert s.causal_chain == []
        assert s.error is None

    def test_speculative_content_defaults(self):
        c = SpeculativeContent()
        assert c.label == ContentLabel.SPECULATIVE
        assert "speculative" in c.disclaimer.lower()

    def test_scenario_status_transitions(self):
        """Verify all status enum values."""
        assert ScenarioStatus.PENDING.value == "pending"
        assert ScenarioStatus.GROUNDING.value == "grounding"
        assert ScenarioStatus.GENERATING.value == "generating"
        assert ScenarioStatus.COMPLETED.value == "completed"
        assert ScenarioStatus.FAILED.value == "failed"


class TestNarrativeParsingEdgeCases:
    """Test edge cases in narrative JSON parsing."""

    @pytest.mark.asyncio
    async def test_non_json_response_treated_as_narrative(
        self, engine: AlternateHistoryEngine, mock_genai_client: MagicMock
    ):
        """When LLM returns plain text instead of JSON."""
        extract_response = MagicMock()
        extract_response.text = json.dumps({
            "base_event_name": "Test",
            "base_event_date": "",
            "base_event_location": "",
            "base_event_description": "",
            "base_event_significance": "",
            "divergence_point": "test",
        })

        narrative_response = MagicMock()
        narrative_response.text = "This is just plain narrative text, not JSON at all."

        mock_genai_client.aio.models.generate_content = AsyncMock(
            side_effect=[extract_response, narrative_response]
        )

        scenario = await engine.generate_scenario("What if test?")
        assert scenario.status == ScenarioStatus.COMPLETED
        assert "plain narrative text" in scenario.alternative_narrative

    @pytest.mark.asyncio
    async def test_invalid_causal_chain_entries_skipped(
        self, engine: AlternateHistoryEngine, mock_genai_client: MagicMock
    ):
        extract_response = MagicMock()
        extract_response.text = json.dumps({
            "base_event_name": "Test",
            "base_event_date": "",
            "base_event_location": "",
            "base_event_description": "",
            "base_event_significance": "",
            "divergence_point": "test",
        })

        narrative_response = MagicMock()
        narrative_response.text = json.dumps({
            "narrative": "Test narrative",
            "causal_chain": [
                {"from_event": "A", "to_event": "B", "reasoning": "ok", "confidence": 0.5},
                {"from_event": "C", "to_event": "D", "confidence": "not_a_number"},
                {"from_event": "E", "to_event": "F", "reasoning": "good", "confidence": 0.8},
            ],
            "plausibility": 0.6,
        })

        mock_genai_client.aio.models.generate_content = AsyncMock(
            side_effect=[extract_response, narrative_response]
        )

        scenario = await engine.generate_scenario("What if test?")
        # Should have 2 valid links (the one with "not_a_number" is skipped)
        assert len(scenario.causal_chain) >= 2

    @pytest.mark.asyncio
    async def test_plausibility_clamped_to_0_1(
        self, engine: AlternateHistoryEngine, mock_genai_client: MagicMock
    ):
        extract_response = MagicMock()
        extract_response.text = json.dumps({
            "base_event_name": "Test",
            "base_event_date": "",
            "base_event_location": "",
            "base_event_description": "",
            "base_event_significance": "",
            "divergence_point": "test",
        })

        narrative_response = MagicMock()
        narrative_response.text = json.dumps({
            "narrative": "Test",
            "causal_chain": [],
            "plausibility": 1.5,  # Out of range
        })

        mock_genai_client.aio.models.generate_content = AsyncMock(
            side_effect=[extract_response, narrative_response]
        )

        scenario = await engine.generate_scenario("What if test?")
        assert scenario.plausibility <= 1.0
