"""Location Recognizer service – identifies landmarks from camera frames.

Design reference: LORE design.md, Section 8 – Location Recognizer.
Requirements: 2.2, 2.4.

Architecture notes
------------------
Recognition pipeline (two-stage):

  Stage 1 – Visual feature extraction (Gemini Vision)
    The raw JPEG/PNG camera frame is sent to Gemini with a structured prompt
    that asks for a JSON response describing the scene, any visible landmark
    name, architectural style, visible text/signage, and a geographic hint.

  Stage 2 – Places API lookup
    The extracted landmark name (or a fallback query built from the Gemini
    description) is submitted to the Google Places API v1 Text Search endpoint.
    The top result is returned as PlaceDetails.

Confidence scoring combines:
  - Gemini's own confidence in the extraction (Stage 1)
  - The textual similarity between the Gemini landmark name and the Places
    API result name (Stage 2)
  - Whether the Places API result types include recognised landmark/tourist
    categories (bonus)

Timeout handling
  The entire pipeline must complete within RECOGNITION_TIMEOUT_SECONDS (3 s,
  Requirement 2.2).  asyncio.wait_for is used on the outer coroutine.

Dependency injection
  Both the Gemini client and the aiohttp session used for Places API calls are
  injected via constructor parameters so that unit tests can replace them with
  mocks without patching module globals.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any, Optional

import aiohttp

from .models import (
    GPSCoordinates,
    LocationResult,
    PlaceDetails,
    VisualFeatures,
)

logger = logging.getLogger(__name__)

# Hard timeout for the full recognition pipeline (Requirement 2.2)
RECOGNITION_TIMEOUT_SECONDS: float = 3.0

# Minimum confidence to consider a match "recognised"
RECOGNITION_CONFIDENCE_THRESHOLD: float = 0.35

# Google Places API v1 text search endpoint
_PLACES_TEXT_SEARCH_URL = (
    "https://places.googleapis.com/v1/places:searchText"
)

# Place types that receive a confidence bonus (well-known landmark categories)
_LANDMARK_TYPES: frozenset[str] = frozenset(
    {
        "tourist_attraction",
        "museum",
        "church",
        "hindu_temple",
        "mosque",
        "synagogue",
        "stadium",
        "amusement_park",
        "zoo",
        "art_gallery",
        "natural_feature",
        "premise",
        "monument",
        "historical_landmark",
        "national_park",
        "castle",
        "palace",
    }
)

# Gemini prompt asking for structured visual feature extraction
_GEMINI_VISION_PROMPT = """Analyse the image and respond ONLY with valid JSON (no markdown fences).

Return exactly this structure:
{
  "description": "<one-sentence description of the scene>",
  "landmark_name": "<name of any visible landmark, monument, or notable building – null if none>",
  "architectural_style": "<architectural or environmental style – null if unclear>",
  "text_detected": ["<any visible text, signs, or labels>"],
  "location_hint": "<city, region, or country implied by the image – null if unknown>",
  "confidence": <float 0.0–1.0 indicating how confident you are in the above>
}

Rules:
- Use null (not the string "null") for missing optional fields.
- Keep "description" under 100 characters.
- "confidence" should be 0.9+ only when a well-known landmark is clearly visible.
"""


class LocationRecognizerError(Exception):
    """Base exception for LocationRecognizer failures."""


class LocationRecognizer:
    """Identifies landmarks and locations from camera frames.

    Parameters
    ----------
    gemini_client:
        An initialised ``google.genai.Client`` instance.  Injected to allow
        mocking in tests.
    places_api_key:
        Google Places API key (or None when mocking).
    vision_model:
        Gemini model name to use for visual feature extraction.
    http_session:
        Optional pre-created ``aiohttp.ClientSession``.  If None, a new
        session is created on first use and closed in ``close()``.
    timeout:
        Maximum seconds for the full recognition pipeline (default 3.0).
    confidence_threshold:
        Minimum confidence to mark a result as ``recognized=True``.
    """

    def __init__(
        self,
        gemini_client: Any,
        places_api_key: str,
        vision_model: str = "gemini-3-flash-preview",
        http_session: Optional[aiohttp.ClientSession] = None,
        timeout: float = RECOGNITION_TIMEOUT_SECONDS,
        confidence_threshold: float = RECOGNITION_CONFIDENCE_THRESHOLD,
    ) -> None:
        self._gemini = gemini_client
        self._places_key = places_api_key
        self._vision_model = vision_model
        self._http_session = http_session
        self._owns_session = http_session is None
        self._timeout = timeout
        self._confidence_threshold = confidence_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def recognize_location(
        self,
        frame_bytes: bytes,
        mime_type: str = "image/jpeg",
        gps_hint: Optional[GPSCoordinates] = None,
    ) -> LocationResult:
        """Identify the landmark or location visible in a camera frame.

        Parameters
        ----------
        frame_bytes:
            Raw image bytes (JPEG or PNG).
        mime_type:
            MIME type of the image (e.g. ``"image/jpeg"``).
        gps_hint:
            Optional GPS fix that narrows the Places API search radius.

        Returns
        -------
        LocationResult
            Always returns a result.  ``recognized`` is False when the
            pipeline fails or confidence is below threshold.
        """
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._run_pipeline(frame_bytes, mime_type, gps_hint),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            logger.warning(
                "Location recognition timed out after %.2fs (limit %.2fs)",
                elapsed,
                self._timeout,
            )
            return LocationResult(
                recognized=False,
                confidence=0.0,
                processing_time=elapsed,
                error=f"Recognition timed out after {self._timeout}s",
            )
        except Exception as exc:  # noqa: BLE001
            elapsed = time.monotonic() - start
            logger.exception("Location recognition failed: %s", exc)
            return LocationResult(
                recognized=False,
                confidence=0.0,
                processing_time=elapsed,
                error=str(exc),
            )
        return result

    async def query_places_api(
        self,
        visual_features: VisualFeatures,
        gps_hint: Optional[GPSCoordinates] = None,
    ) -> Optional[PlaceDetails]:
        """Query Google Places API with extracted visual features.

        Parameters
        ----------
        visual_features:
            Structured features produced by Stage 1.
        gps_hint:
            Optional GPS coordinates to bias the search.

        Returns
        -------
        PlaceDetails or None if no match found.
        """
        query = self._build_search_query(visual_features)
        if not query:
            logger.debug("No usable query terms from visual features – skipping Places API")
            return None

        session = await self._get_session()

        payload: dict[str, Any] = {
            "textQuery": query,
            "maxResultCount": 1,
            "languageCode": "en",
        }
        if gps_hint:
            payload["locationBias"] = {
                "circle": {
                    "center": {
                        "latitude": gps_hint.latitude,
                        "longitude": gps_hint.longitude,
                    },
                    "radius": 5000.0,  # 5 km bias radius
                }
            }

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._places_key,
            "X-Goog-FieldMask": (
                "places.id,places.displayName,places.location,"
                "places.types,places.editorialSummary,places.formattedAddress,"
                "places.rating,places.websiteUri,places.photos"
            ),
        }

        try:
            async with session.post(
                _PLACES_TEXT_SEARCH_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    logger.warning(
                        "Places API returned HTTP %s: %s", response.status, body[:200]
                    )
                    return None
                data = await response.json()
        except aiohttp.ClientError as exc:
            logger.warning("Places API request failed: %s", exc)
            return None

        places = data.get("places", [])
        if not places:
            logger.debug("Places API returned no results for query %r", query)
            return None

        return self._parse_place_response(places[0])

    def score_confidence(
        self, match: PlaceDetails, visual_features: VisualFeatures
    ) -> float:
        """Compute overall match confidence from 0.0 to 1.0.

        Combines:
        - Gemini extraction confidence (40 %)
        - Landmark name similarity (40 %)
        - Landmark type bonus (20 %)
        """
        gemini_score = visual_features.confidence * 0.4

        # Textual similarity between extracted name and Places name
        name_score = self._name_similarity(
            visual_features.landmark_name or "", match.name
        )
        name_score *= 0.4

        # Bonus when the place has tourist/landmark type categories
        type_bonus = 0.0
        matched_types = set(match.types) & _LANDMARK_TYPES
        if matched_types:
            type_bonus = min(len(matched_types) * 0.1, 0.2)

        total = gemini_score + name_score + type_bonus
        return round(min(total, 1.0), 4)

    async def close(self) -> None:
        """Release resources owned by this recognizer."""
        if self._owns_session and self._http_session is not None:
            await self._http_session.close()
            self._http_session = None

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "LocationRecognizer":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_pipeline(
        self,
        frame_bytes: bytes,
        mime_type: str,
        gps_hint: Optional[GPSCoordinates],
    ) -> LocationResult:
        """Execute the two-stage recognition pipeline."""
        start = time.monotonic()

        # Stage 1: extract visual features with Gemini Vision
        visual_features = await self._extract_visual_features(frame_bytes, mime_type)

        # Stage 2: look up matching place in Places API
        place = await self.query_places_api(visual_features, gps_hint)

        elapsed = time.monotonic() - start

        if place is None:
            logger.debug("No place found for visual features (%.2fs)", elapsed)
            return LocationResult(
                recognized=False,
                confidence=visual_features.confidence * 0.4,  # partial score
                processing_time=elapsed,
                visual_features=visual_features,
            )

        confidence = self.score_confidence(place, visual_features)
        recognized = confidence >= self._confidence_threshold

        logger.info(
            "Location recognition: name=%r confidence=%.3f recognised=%s elapsed=%.2fs",
            place.name,
            confidence,
            recognized,
            elapsed,
        )

        return LocationResult(
            recognized=recognized,
            place=place if recognized else None,
            confidence=confidence,
            processing_time=elapsed,
            visual_features=visual_features,
        )

    async def _extract_visual_features(
        self, frame_bytes: bytes, mime_type: str
    ) -> VisualFeatures:
        """Call Gemini Vision to extract structured features from a frame."""
        b64_image = base64.standard_b64encode(frame_bytes).decode()

        loop = asyncio.get_running_loop()
        raw_response: dict[str, Any] = {}

        try:
            # Gemini SDK is synchronous – run in executor to avoid blocking
            from google.genai import types as genai_types  # noqa: PLC0415

            contents = [
                genai_types.Content(
                    role="user",
                    parts=[
                        genai_types.Part(
                            inline_data=genai_types.Blob(
                                mime_type=mime_type,
                                data=b64_image,
                            )
                        ),
                        genai_types.Part(text=_GEMINI_VISION_PROMPT),
                    ],
                )
            ]

            def _call_gemini() -> Any:
                return self._gemini.models.generate_content(
                    model=self._vision_model,
                    contents=contents,
                    config=genai_types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.1,  # low temperature for factual extraction
                        max_output_tokens=512,
                    ),
                )

            response = await loop.run_in_executor(None, _call_gemini)
            raw_text = response.text or "{}"
            parsed = json.loads(raw_text)
            raw_response = parsed

        except json.JSONDecodeError as exc:
            logger.warning("Gemini returned non-JSON response: %s", exc)
            parsed = {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini visual extraction failed: %s", exc)
            parsed = {}

        return VisualFeatures(
            description=parsed.get("description") or "Unknown scene",
            landmark_name=parsed.get("landmark_name") or None,
            architectural_style=parsed.get("architectural_style") or None,
            text_detected=parsed.get("text_detected") or [],
            location_hint=parsed.get("location_hint") or None,
            confidence=float(parsed.get("confidence") or 0.0),
            raw_response=raw_response,
        )

    def _build_search_query(self, features: VisualFeatures) -> str:
        """Build the best Places API search query from visual features."""
        parts: list[str] = []

        if features.landmark_name:
            parts.append(features.landmark_name)

        if features.location_hint and features.location_hint not in parts:
            parts.append(features.location_hint)

        if not parts:
            # Fallback: use a condensed version of the Gemini description
            desc = features.description
            if desc and desc != "Unknown scene":
                parts.append(desc[:80])

        return " ".join(parts).strip()

    def _parse_place_response(self, place_data: dict[str, Any]) -> PlaceDetails:
        """Convert a Places API v1 response object into a PlaceDetails model."""
        location_data = place_data.get("location", {})
        coords = GPSCoordinates(
            latitude=float(location_data.get("latitude", 0.0)),
            longitude=float(location_data.get("longitude", 0.0)),
            accuracy=0.0,
            timestamp=time.time(),
        )

        # displayName is {"text": "...", "languageCode": "en"}
        display_name = place_data.get("displayName", {})
        name = display_name.get("text", "") if isinstance(display_name, dict) else str(display_name)

        editorial = place_data.get("editorialSummary", {})
        editorial_text = editorial.get("text", "") if isinstance(editorial, dict) else ""

        # Photos come as a list of photo resource objects; extract names as pseudo-URLs
        photos_raw = place_data.get("photos", [])
        photo_refs = [
            f"https://places.googleapis.com/v1/{p['name']}/media?maxWidthPx=800"
            for p in photos_raw
            if isinstance(p, dict) and p.get("name")
        ]

        return PlaceDetails(
            place_id=place_data.get("id", ""),
            name=name,
            location=coords,
            types=place_data.get("types", []),
            description=editorial_text,
            photos=photo_refs[:5],  # cap at 5 references
            formatted_address=place_data.get("formattedAddress", ""),
            rating=place_data.get("rating"),
            website=place_data.get("websiteUri"),
            editorial_summary=editorial_text,
        )

    @staticmethod
    def _name_similarity(a: str, b: str) -> float:
        """Simple token-overlap similarity between two strings (0.0–1.0)."""
        if not a or not b:
            return 0.0
        tokens_a = set(a.lower().split())
        tokens_b = set(b.lower().split())
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b
        return len(intersection) / len(union)

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return or lazily create the shared aiohttp session."""
        if self._http_session is None:
            self._http_session = aiohttp.ClientSession()
        return self._http_session
