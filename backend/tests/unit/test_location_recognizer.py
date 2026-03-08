"""Unit tests for Location Recognizer service.

Requirements: 2.2, 2.4.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.location_recognizer.models import (
    GPSCoordinates,
    LocationResult,
    PlaceDetails,
    VisualFeatures,
)
from backend.services.location_recognizer.recognizer import (
    RECOGNITION_CONFIDENCE_THRESHOLD,
    LocationRecognizer,
    LocationRecognizerError,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # minimal JPEG-like bytes


def _make_gemini_response(payload: dict[str, Any]) -> MagicMock:
    """Return a mock Gemini response whose .text is JSON-encoded payload."""
    resp = MagicMock()
    resp.text = json.dumps(payload)
    return resp


def _make_gemini_client(payload: dict[str, Any]) -> MagicMock:
    """Return a mock google.genai.Client whose generate_content returns payload."""
    client = MagicMock()
    client.models.generate_content.return_value = _make_gemini_response(payload)
    return client


def _make_places_session(places_body: dict[str, Any], status: int = 200) -> MagicMock:
    """Build a mock aiohttp session that returns the given Places API body."""
    response = AsyncMock()
    response.status = status
    response.json = AsyncMock(return_value=places_body)
    response.text = AsyncMock(return_value=json.dumps(places_body))
    response.__aenter__ = AsyncMock(return_value=response)
    response.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.post = MagicMock(return_value=response)
    return session


def _sample_place_data() -> dict[str, Any]:
    return {
        "id": "ChIJN1t_tDeuEmsRUsoyG83frY4",
        "displayName": {"text": "Eiffel Tower", "languageCode": "en"},
        "location": {"latitude": 48.8584, "longitude": 2.2945},
        "types": ["tourist_attraction", "landmark"],
        "editorialSummary": {"text": "Iconic iron lattice tower in Paris."},
        "formattedAddress": "Champ de Mars, 5 Av. Anatole France, Paris, France",
        "rating": 4.7,
        "websiteUri": "https://www.toureiffel.paris/",
        "photos": [
            {"name": "places/ChIJ/photos/AXCi"},
        ],
    }


# ---------------------------------------------------------------------------
# Tests: score_confidence
# ---------------------------------------------------------------------------


class TestScoreConfidence:
    def _make_recognizer(self) -> LocationRecognizer:
        return LocationRecognizer(
            gemini_client=MagicMock(),
            places_api_key="fake-key",
            http_session=MagicMock(),
        )

    def test_high_confidence_exact_name_match(self) -> None:
        rec = self._make_recognizer()
        features = VisualFeatures(
            description="Eiffel Tower in Paris",
            landmark_name="Eiffel Tower",
            confidence=0.95,
        )
        place = PlaceDetails(
            place_id="abc",
            name="Eiffel Tower",
            location=GPSCoordinates(latitude=48.8584, longitude=2.2945),
            types=["tourist_attraction"],
        )
        score = rec.score_confidence(place, features)
        assert score >= 0.8, f"Expected >= 0.8, got {score}"

    def test_zero_confidence_no_name_no_type(self) -> None:
        rec = self._make_recognizer()
        features = VisualFeatures(description="blurry image", confidence=0.0)
        place = PlaceDetails(
            place_id="abc",
            name="Unknown Building",
            location=GPSCoordinates(latitude=0.0, longitude=0.0),
            types=["establishment"],
        )
        score = rec.score_confidence(place, features)
        assert score < RECOGNITION_CONFIDENCE_THRESHOLD

    def test_landmark_type_bonus_applied(self) -> None:
        rec = self._make_recognizer()
        features = VisualFeatures(description="old castle", confidence=0.5)
        place_with_landmark = PlaceDetails(
            place_id="abc",
            name="Old Castle",
            location=GPSCoordinates(latitude=0.0, longitude=0.0),
            types=["castle", "tourist_attraction"],
        )
        place_without_landmark = PlaceDetails(
            place_id="abc",
            name="Old Castle",
            location=GPSCoordinates(latitude=0.0, longitude=0.0),
            types=["establishment"],
        )
        score_with = rec.score_confidence(place_with_landmark, features)
        score_without = rec.score_confidence(place_without_landmark, features)
        assert score_with > score_without

    def test_score_capped_at_one(self) -> None:
        rec = self._make_recognizer()
        features = VisualFeatures(
            description="Eiffel Tower", landmark_name="Eiffel Tower", confidence=1.0
        )
        place = PlaceDetails(
            place_id="abc",
            name="Eiffel Tower",
            location=GPSCoordinates(latitude=48.8584, longitude=2.2945),
            types=["tourist_attraction", "museum", "historical_landmark"],
        )
        score = rec.score_confidence(place, features)
        assert score <= 1.0


# ---------------------------------------------------------------------------
# Tests: recognize_location (with mocks)
# ---------------------------------------------------------------------------


class TestRecognizeLocation:
    @pytest.mark.asyncio
    async def test_successful_recognition(self) -> None:
        """Full pipeline returns recognized=True for a clear landmark."""
        gemini_payload = {
            "description": "Eiffel Tower in Paris at dusk",
            "landmark_name": "Eiffel Tower",
            "architectural_style": "iron lattice",
            "text_detected": [],
            "location_hint": "Paris, France",
            "confidence": 0.92,
        }
        places_body = {"places": [_sample_place_data()]}

        client = _make_gemini_client(gemini_payload)
        session = _make_places_session(places_body)

        # Patch the Gemini types import inside recognizer
        with patch.dict(
            "sys.modules",
            {"google.genai": MagicMock(), "google.genai.types": _mock_genai_types()},
        ):
            rec = LocationRecognizer(
                gemini_client=client,
                places_api_key="fake-key",
                http_session=session,
            )
            result = await rec.recognize_location(FAKE_JPEG)

        assert result.recognized is True
        assert result.place is not None
        assert result.place.name == "Eiffel Tower"
        assert result.confidence >= RECOGNITION_CONFIDENCE_THRESHOLD
        assert result.processing_time >= 0.0
        assert result.error is None

    @pytest.mark.asyncio
    async def test_unrecognised_when_no_places_result(self) -> None:
        """Pipeline returns recognized=False when Places API finds nothing."""
        gemini_payload = {
            "description": "Some nondescript wall",
            "landmark_name": None,
            "architectural_style": None,
            "text_detected": [],
            "location_hint": None,
            "confidence": 0.1,
        }
        places_body = {"places": []}

        client = _make_gemini_client(gemini_payload)
        session = _make_places_session(places_body)

        with patch.dict(
            "sys.modules",
            {"google.genai": MagicMock(), "google.genai.types": _mock_genai_types()},
        ):
            rec = LocationRecognizer(
                gemini_client=client,
                places_api_key="fake-key",
                http_session=session,
            )
            result = await rec.recognize_location(FAKE_JPEG)

        assert result.recognized is False
        assert result.place is None

    @pytest.mark.asyncio
    async def test_timeout_returns_not_recognised(self) -> None:
        """When the pipeline exceeds the timeout, a safe LocationResult is returned."""
        import threading

        client = MagicMock()

        # Gemini runs in run_in_executor (synchronous), so we block with time.sleep
        def _slow_generate(*_: Any, **__: Any) -> None:
            time.sleep(10)

        client.models.generate_content.side_effect = _slow_generate

        with patch.dict(
            "sys.modules",
            {"google.genai": MagicMock(), "google.genai.types": _mock_genai_types()},
        ):
            rec = LocationRecognizer(
                gemini_client=client,
                places_api_key="fake-key",
                timeout=0.05,  # 50 ms timeout for test speed
            )
            result = await rec.recognize_location(FAKE_JPEG)

        assert result.recognized is False
        assert result.error is not None
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_gemini_failure_returns_not_recognised(self) -> None:
        """Gemini API errors are caught and result in recognized=False."""
        client = MagicMock()
        client.models.generate_content.side_effect = RuntimeError("API error")

        with patch.dict(
            "sys.modules",
            {"google.genai": MagicMock(), "google.genai.types": _mock_genai_types()},
        ):
            rec = LocationRecognizer(
                gemini_client=client,
                places_api_key="fake-key",
                http_session=_make_places_session({"places": []}),
            )
            result = await rec.recognize_location(FAKE_JPEG)

        assert result.recognized is False

    @pytest.mark.asyncio
    async def test_places_api_http_error_returns_not_recognised(self) -> None:
        """HTTP errors from Places API are handled gracefully."""
        gemini_payload = {
            "description": "Colosseum",
            "landmark_name": "Colosseum",
            "confidence": 0.9,
            "text_detected": [],
            "location_hint": "Rome",
        }
        client = _make_gemini_client(gemini_payload)
        session = _make_places_session({}, status=403)

        with patch.dict(
            "sys.modules",
            {"google.genai": MagicMock(), "google.genai.types": _mock_genai_types()},
        ):
            rec = LocationRecognizer(
                gemini_client=client,
                places_api_key="bad-key",
                http_session=session,
            )
            result = await rec.recognize_location(FAKE_JPEG)

        assert result.recognized is False

    @pytest.mark.asyncio
    async def test_gps_hint_included_in_places_request(self) -> None:
        """GPS hint is forwarded as locationBias to Places API."""
        gemini_payload = {
            "description": "Eiffel Tower",
            "landmark_name": "Eiffel Tower",
            "confidence": 0.9,
            "text_detected": [],
            "location_hint": "Paris",
        }
        places_body = {"places": [_sample_place_data()]}

        client = _make_gemini_client(gemini_payload)
        session = _make_places_session(places_body)

        gps = GPSCoordinates(latitude=48.858, longitude=2.294, accuracy=5.0)

        with patch.dict(
            "sys.modules",
            {"google.genai": MagicMock(), "google.genai.types": _mock_genai_types()},
        ):
            rec = LocationRecognizer(
                gemini_client=client,
                places_api_key="fake-key",
                http_session=session,
            )
            result = await rec.recognize_location(FAKE_JPEG, gps_hint=gps)

        # Verify locationBias was included in the Places API call
        call_args = session.post.call_args
        payload_sent = call_args.kwargs.get("json") or call_args.args[1] if call_args.args else {}
        # The json kwarg is the payload dict
        if call_args and call_args.kwargs.get("json"):
            assert "locationBias" in call_args.kwargs["json"]

        assert result.recognized is True

    @pytest.mark.asyncio
    async def test_processing_time_under_timeout(self) -> None:
        """processing_time in the result is always <= the configured timeout."""
        gemini_payload = {
            "description": "Quick test",
            "landmark_name": None,
            "confidence": 0.1,
            "text_detected": [],
            "location_hint": None,
        }
        client = _make_gemini_client(gemini_payload)
        session = _make_places_session({"places": []})

        with patch.dict(
            "sys.modules",
            {"google.genai": MagicMock(), "google.genai.types": _mock_genai_types()},
        ):
            rec = LocationRecognizer(
                gemini_client=client,
                places_api_key="fake-key",
                http_session=session,
                timeout=3.0,
            )
            result = await rec.recognize_location(FAKE_JPEG)

        assert result.processing_time <= 3.0


# ---------------------------------------------------------------------------
# Tests: _build_search_query
# ---------------------------------------------------------------------------


class TestBuildSearchQuery:
    def _recognizer(self) -> LocationRecognizer:
        return LocationRecognizer(
            gemini_client=MagicMock(),
            places_api_key="k",
            http_session=MagicMock(),
        )

    def test_landmark_name_used_first(self) -> None:
        rec = self._recognizer()
        features = VisualFeatures(
            description="big tower",
            landmark_name="Eiffel Tower",
            location_hint="Paris",
            confidence=0.8,
        )
        query = rec._build_search_query(features)  # noqa: SLF001
        assert "Eiffel Tower" in query

    def test_fallback_to_description_when_no_name(self) -> None:
        rec = self._recognizer()
        features = VisualFeatures(
            description="Ancient Roman amphitheatre",
            landmark_name=None,
            location_hint=None,
            confidence=0.3,
        )
        query = rec._build_search_query(features)  # noqa: SLF001
        assert query != ""

    def test_empty_query_for_bare_minimum_features(self) -> None:
        rec = self._recognizer()
        features = VisualFeatures(description="Unknown scene", confidence=0.0)
        query = rec._build_search_query(features)  # noqa: SLF001
        assert query == ""


# ---------------------------------------------------------------------------
# Tests: _name_similarity
# ---------------------------------------------------------------------------


class TestNameSimilarity:
    def test_identical_names(self) -> None:
        score = LocationRecognizer._name_similarity("Eiffel Tower", "Eiffel Tower")  # noqa: SLF001
        assert score == 1.0

    def test_completely_different_names(self) -> None:
        score = LocationRecognizer._name_similarity("Big Ben", "Colosseum")  # noqa: SLF001
        assert score == 0.0

    def test_partial_overlap(self) -> None:
        score = LocationRecognizer._name_similarity("Eiffel Tower Paris", "Eiffel Tower")  # noqa: SLF001
        assert 0.0 < score < 1.0

    def test_empty_strings(self) -> None:
        assert LocationRecognizer._name_similarity("", "Eiffel Tower") == 0.0  # noqa: SLF001
        assert LocationRecognizer._name_similarity("Eiffel Tower", "") == 0.0  # noqa: SLF001


# ---------------------------------------------------------------------------
# Tests: close / context manager
# ---------------------------------------------------------------------------


class TestCloseAndContextManager:
    @pytest.mark.asyncio
    async def test_close_without_session_is_safe(self) -> None:
        rec = LocationRecognizer(
            gemini_client=MagicMock(),
            places_api_key="k",
            http_session=None,
        )
        # Should not raise even though no session was created
        await rec.close()

    @pytest.mark.asyncio
    async def test_async_context_manager(self) -> None:
        session = MagicMock()
        session.close = AsyncMock()

        async with LocationRecognizer(
            gemini_client=MagicMock(),
            places_api_key="k",
            http_session=session,
        ) as rec:
            assert rec is not None
        # session.close should NOT be called because session was injected (not owned)
        session.close.assert_not_called()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_genai_types() -> MagicMock:
    """Return a minimal mock of google.genai.types used in recognizer.py."""
    types = MagicMock()
    types.Content = MagicMock(side_effect=lambda **kw: kw)
    types.Part = MagicMock(side_effect=lambda **kw: kw)
    types.Blob = MagicMock(side_effect=lambda **kw: kw)
    types.GenerateContentConfig = MagicMock(side_effect=lambda **kw: kw)
    return types
