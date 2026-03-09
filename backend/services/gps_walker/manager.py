"""GPS Walking Tour Manager – location-based documentary triggering.

Design reference: LORE design.md, Section 7 – GPS Walker.
Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 29.4.

Architecture notes
------------------
The GPSWalkingTourManager continuously monitors GPS location and detects nearby
landmarks using the Google Places API. When the user moves within 50 meters of
a registered landmark (Requirement 9.2), it decides whether to auto-trigger
documentary content based on:

  1. Proximity (closer landmarks score higher)
  2. User interest history (previously visited topics/types)
  3. Recency (minimum 5-minute interval between triggers for same landmark)

The manager provides:
  - Continuous location monitoring (Requirement 9.1)
  - Nearby landmark detection with 50m radius (Requirement 9.2)
  - Directional guidance to POIs (Requirement 9.4)
  - Landmark prioritization (Requirement 9.5)
  - GPS signal loss handling (Requirement 9.7, 29.4)

Integration with Google Maps Platform:
  - Places API Nearby Search for landmark detection
  - Directions API for turn-by-turn navigation
  - Distance Matrix API for travel time estimates

Dependency injection:
  The aiohttp session and Places API key are injected via constructor to allow
  mocking in tests.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any, AsyncIterator, Callable, Optional

import aiohttp

from .models import (
    Directions,
    DirectionStep,
    GPSCoordinates,
    GPSStatus,
    GPSUpdate,
    Landmark,
    PointOfInterest,
    TriggerDecision,
    UserHistory,
)

logger = logging.getLogger(__name__)

# Requirement 9.2: Auto-trigger within 50 meters
TRIGGER_RADIUS_METERS: float = 50.0

# Minimum interval between triggers for the same landmark (5 minutes)
MIN_TRIGGER_INTERVAL_SECONDS: float = 300.0

# Requirement 9.6: Operate with location accuracy within 10 meters
MAX_ACCEPTABLE_ACCURACY_METERS: float = 10.0

# Google Places API v1 endpoints
_PLACES_NEARBY_SEARCH_URL = "https://places.googleapis.com/v1/places:searchNearby"
_PLACES_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

# Google Maps Directions API endpoint
_DIRECTIONS_API_URL = "https://maps.googleapis.com/maps/api/directions/json"

# Place types that indicate landmarks/tourist attractions
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
        "monument",
        "historical_landmark",
        "national_park",
        "castle",
        "palace",
        "park",
        "point_of_interest",
    }
)


class GPSWalkingTourManager:
    """Manages GPS-based walking tours with automatic landmark detection.

    Parameters
    ----------
    places_api_key:
        Google Places API key.
    maps_api_key:
        Google Maps API key (for Directions API).
    http_session:
        Optional pre-created ``aiohttp.ClientSession``. If None, a new
        session is created on first use and closed in ``close()``.
    trigger_radius:
        Radius in meters for landmark detection (default 50m).
    min_trigger_interval:
        Minimum seconds between triggers for same landmark (default 300s).
    max_accuracy:
        Maximum acceptable GPS accuracy in meters (default 10m).
    """

    def __init__(
        self,
        places_api_key: str,
        maps_api_key: str,
        http_session: Optional[aiohttp.ClientSession] = None,
        trigger_radius: float = TRIGGER_RADIUS_METERS,
        min_trigger_interval: float = MIN_TRIGGER_INTERVAL_SECONDS,
        max_accuracy: float = MAX_ACCEPTABLE_ACCURACY_METERS,
    ) -> None:
        self._places_key = places_api_key
        self._maps_key = maps_api_key
        self._http_session = http_session
        self._owns_session = http_session is None
        self._trigger_radius = trigger_radius
        self._min_trigger_interval = min_trigger_interval
        self._max_accuracy = max_accuracy

        # Track last trigger time per landmark to enforce minimum interval
        self._last_trigger_times: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def monitor_location(
        self, user_id: str, location_stream: AsyncIterator[GPSUpdate]
    ) -> AsyncIterator[TriggerDecision]:
        """Monitor GPS location and yield trigger decisions for nearby landmarks.

        Requirement 9.1: Monitor device location continuously.
        Requirement 9.2: Auto-trigger within 50 meters of landmarks.

        Parameters
        ----------
        user_id:
            User identifier for loading interest history.
        location_stream:
            Async iterator yielding GPS updates from the device.

        Yields
        ------
        TriggerDecision
            Decision about whether to trigger documentary content.
        """
        logger.info("Starting GPS monitoring for user %s", user_id)

        # Load user interest history (would come from SessionMemoryManager)
        user_history = UserHistory(user_id=user_id)

        async for gps_update in location_stream:
            # Requirement 9.7: Handle GPS signal loss
            if gps_update.status == GPSStatus.UNAVAILABLE:
                logger.warning("GPS signal unavailable for user %s", user_id)
                continue

            if gps_update.coordinates is None:
                continue

            # Requirement 9.6: Check accuracy
            if gps_update.coordinates.accuracy > self._max_accuracy:
                logger.debug(
                    "GPS accuracy %.1fm exceeds threshold %.1fm",
                    gps_update.coordinates.accuracy,
                    self._max_accuracy,
                )
                # Continue monitoring but log degraded accuracy
                if gps_update.status != GPSStatus.DEGRADED:
                    logger.warning(
                        "GPS accuracy degraded: %.1fm (threshold %.1fm)",
                        gps_update.coordinates.accuracy,
                        self._max_accuracy,
                    )

            # Detect nearby landmarks
            try:
                landmarks = await self.detect_nearby_landmarks(
                    gps_update.coordinates, self._trigger_radius
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to detect nearby landmarks: %s", exc)
                continue

            if not landmarks:
                logger.debug("No landmarks found within %.1fm", self._trigger_radius)
                continue

            # Prioritize landmarks by proximity and user interest
            prioritized = self.prioritize_landmarks(landmarks, user_history)

            # Check if we should trigger for the top landmark
            if prioritized:
                top_landmark = prioritized[0]
                decision = self.should_trigger_documentary(top_landmark, user_history)

                if decision.should_trigger:
                    # Update trigger tracking
                    self._last_trigger_times[top_landmark.place_id] = time.time()
                    user_history.last_triggered_place_id = top_landmark.place_id
                    user_history.last_trigger_timestamp = time.time()

                    logger.info(
                        "Triggering documentary for landmark: %s (score=%.3f)",
                        top_landmark.name,
                        decision.priority_score,
                    )

                yield decision

    async def detect_nearby_landmarks(
        self, location: GPSCoordinates, radius: float
    ) -> list[Landmark]:
        """Detect landmarks within specified radius of a location.

        Requirement 9.2: Detect landmarks within 50 meters.
        Requirement 9.3: Use Google Maps Platform and Places API.

        Parameters
        ----------
        location:
            Center point for search.
        radius:
            Search radius in meters.

        Returns
        -------
        list[Landmark]
            Detected landmarks sorted by distance (closest first).
        """
        session = await self._get_session()

        payload = {
            "locationRestriction": {
                "circle": {
                    "center": {
                        "latitude": location.latitude,
                        "longitude": location.longitude,
                    },
                    "radius": radius,
                }
            },
            "includedTypes": list(_LANDMARK_TYPES),
            "maxResultCount": 20,
            "languageCode": "en",
        }

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._places_key,
            "X-Goog-FieldMask": (
                "places.id,places.displayName,places.location,"
                "places.types,places.editorialSummary,places.rating,"
                "places.userRatingCount"
            ),
        }

        try:
            async with session.post(
                _PLACES_NEARBY_SEARCH_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=3.0),
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    logger.warning(
                        "Places Nearby Search returned HTTP %s: %s",
                        response.status,
                        body[:200],
                    )
                    return []
                data = await response.json()
        except aiohttp.ClientError as exc:
            logger.warning("Places Nearby Search request failed: %s", exc)
            return []

        places = data.get("places", [])
        if not places:
            logger.debug("No landmarks found within %.1fm radius", radius)
            return []

        landmarks = []
        for place_data in places:
            try:
                landmark = self._parse_landmark(place_data, location)
                landmarks.append(landmark)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to parse landmark: %s", exc)
                continue

        # Sort by distance (closest first)
        landmarks.sort(key=lambda lm: lm.distance_meters)

        logger.info(
            "Found %d landmarks within %.1fm: %s",
            len(landmarks),
            radius,
            [lm.name for lm in landmarks[:5]],
        )

        return landmarks

    def should_trigger_documentary(
        self, landmark: Landmark, user_history: UserHistory
    ) -> TriggerDecision:
        """Decide whether to trigger documentary content for a landmark.

        Requirement 9.2: Auto-trigger within 50 meters.
        Requirement 9.5: Prioritize by proximity and user interest history.

        Parameters
        ----------
        landmark:
            Landmark to evaluate.
        user_history:
            User's interest history.

        Returns
        -------
        TriggerDecision
            Decision with reasoning and priority scores.
        """
        # Check minimum trigger interval
        last_trigger = self._last_trigger_times.get(landmark.place_id, 0.0)
        time_since_last = time.time() - last_trigger

        if time_since_last < self._min_trigger_interval:
            return TriggerDecision(
                should_trigger=False,
                landmark=landmark,
                reason=f"Recently triggered {time_since_last:.0f}s ago (min {self._min_trigger_interval:.0f}s)",
                priority_score=0.0,
                proximity_score=0.0,
                interest_score=0.0,
                recency_penalty=1.0,
            )

        # Calculate proximity score (closer = higher score)
        proximity_score = self._calculate_proximity_score(landmark.distance_meters)

        # Calculate interest score based on user history
        interest_score = self._calculate_interest_score(landmark, user_history)

        # Calculate recency penalty (recently visited = lower score)
        recency_penalty = self._calculate_recency_penalty(landmark, user_history)

        # Combined priority score
        priority_score = (
            proximity_score * 0.5 + interest_score * 0.3 + recency_penalty * 0.2
        )

        # Trigger if priority score exceeds threshold
        should_trigger = priority_score >= 0.4

        reason = self._build_trigger_reason(
            should_trigger, proximity_score, interest_score, recency_penalty
        )

        return TriggerDecision(
            should_trigger=should_trigger,
            landmark=landmark,
            reason=reason,
            priority_score=priority_score,
            proximity_score=proximity_score,
            interest_score=interest_score,
            recency_penalty=recency_penalty,
        )

    async def trigger_documentary(
        self, landmark: Landmark, callback: Optional[Callable] = None
    ) -> dict[str, Any]:
        """Trigger documentary content generation for a landmark.

        Requirement 9.2: Auto-trigger documentary content.

        Parameters
        ----------
        landmark:
            Landmark to generate documentary for.
        callback:
            Optional async callback to invoke with documentary request.

        Returns
        -------
        dict
            Documentary request payload for Orchestrator.
        """
        request = {
            "trigger_type": "gps_walker",
            "place_id": landmark.place_id,
            "place_name": landmark.name,
            "location": {
                "latitude": landmark.location.latitude,
                "longitude": landmark.location.longitude,
            },
            "place_types": landmark.types,
            "description": landmark.description,
            "historical_significance": landmark.historical_significance,
            "distance_meters": landmark.distance_meters,
        }

        if callback:
            try:
                await callback(request)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Documentary trigger callback failed: %s", exc)

        return request

    async def get_directions(
        self, from_location: GPSCoordinates, to_landmark: Landmark
    ) -> Optional[Directions]:
        """Get turn-by-turn directions from current location to a landmark.

        Requirement 9.4: Provide directional guidance to nearby POIs.

        Parameters
        ----------
        from_location:
            Starting location.
        to_landmark:
            Destination landmark.

        Returns
        -------
        Directions or None if directions unavailable.
        """
        session = await self._get_session()

        params = {
            "origin": f"{from_location.latitude},{from_location.longitude}",
            "destination": f"{to_landmark.location.latitude},{to_landmark.location.longitude}",
            "mode": "walking",
            "key": self._maps_key,
        }

        try:
            async with session.get(
                _DIRECTIONS_API_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=3.0),
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    logger.warning(
                        "Directions API returned HTTP %s: %s",
                        response.status,
                        body[:200],
                    )
                    return None
                data = await response.json()
        except aiohttp.ClientError as exc:
            logger.warning("Directions API request failed: %s", exc)
            return None

        if data.get("status") != "OK":
            logger.warning("Directions API status: %s", data.get("status"))
            return None

        routes = data.get("routes", [])
        if not routes:
            return None

        return self._parse_directions(routes[0], from_location, to_landmark.location)

    async def find_nearby_pois(
        self, location: GPSCoordinates, radius: float = 500.0
    ) -> list[PointOfInterest]:
        """Find nearby points of interest for navigation.

        Requirement 9.4: Provide directional guidance to nearby POIs.

        Parameters
        ----------
        location:
            Center point for search.
        radius:
            Search radius in meters (default 500m).

        Returns
        -------
        list[PointOfInterest]
            Nearby POIs with navigation information.
        """
        landmarks = await self.detect_nearby_landmarks(location, radius)

        pois = []
        for landmark in landmarks:
            bearing = self._calculate_bearing(location, landmark.location)
            walk_time = landmark.distance_meters / 80.0  # Assume 80 m/min walking speed

            poi = PointOfInterest(
                place_id=landmark.place_id,
                name=landmark.name,
                location=landmark.location,
                types=landmark.types,
                distance_meters=landmark.distance_meters,
                bearing_degrees=bearing,
                estimated_walk_time_minutes=walk_time,
            )
            pois.append(poi)

        return pois

    def prioritize_landmarks(
        self, landmarks: list[Landmark], user_history: UserHistory
    ) -> list[Landmark]:
        """Prioritize landmarks by proximity and user interest.

        Requirement 9.5: Prioritize by proximity and user interest history.

        Parameters
        ----------
        landmarks:
            List of detected landmarks.
        user_history:
            User's interest history.

        Returns
        -------
        list[Landmark]
            Landmarks sorted by priority (highest first).
        """
        scored_landmarks = []

        for landmark in landmarks:
            proximity_score = self._calculate_proximity_score(landmark.distance_meters)
            interest_score = self._calculate_interest_score(landmark, user_history)
            recency_penalty = self._calculate_recency_penalty(landmark, user_history)

            priority = (
                proximity_score * 0.5 + interest_score * 0.3 + recency_penalty * 0.2
            )

            scored_landmarks.append((priority, landmark))

        # Sort by priority (highest first)
        scored_landmarks.sort(key=lambda x: x[0], reverse=True)

        return [lm for _, lm in scored_landmarks]

    async def close(self) -> None:
        """Release resources owned by this manager."""
        if self._owns_session and self._http_session is not None:
            await self._http_session.close()
            self._http_session = None

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "GPSWalkingTourManager":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_landmark(
        self, place_data: dict[str, Any], user_location: GPSCoordinates
    ) -> Landmark:
        """Parse a Places API response into a Landmark model."""
        location_data = place_data.get("location", {})
        coords = GPSCoordinates(
            latitude=float(location_data.get("latitude", 0.0)),
            longitude=float(location_data.get("longitude", 0.0)),
            accuracy=0.0,
            timestamp=time.time(),
        )

        display_name = place_data.get("displayName", {})
        name = (
            display_name.get("text", "")
            if isinstance(display_name, dict)
            else str(display_name)
        )

        editorial = place_data.get("editorialSummary", {})
        description = (
            editorial.get("text", "") if isinstance(editorial, dict) else ""
        )

        types = place_data.get("types", [])

        # Calculate distance from user
        distance = self._calculate_distance(user_location, coords)

        # Estimate historical significance from place types and rating
        significance = self._estimate_historical_significance(
            types, place_data.get("rating"), place_data.get("userRatingCount", 0)
        )

        return Landmark(
            place_id=place_data.get("id", ""),
            name=name,
            location=coords,
            types=types,
            description=description,
            historical_significance=significance,
            distance_meters=distance,
            rating=place_data.get("rating"),
            user_ratings_total=place_data.get("userRatingCount", 0),
        )

    def _parse_directions(
        self,
        route_data: dict[str, Any],
        origin: GPSCoordinates,
        destination: GPSCoordinates,
    ) -> Directions:
        """Parse a Directions API route into a Directions model."""
        leg = route_data.get("legs", [{}])[0]

        steps = []
        for step_data in leg.get("steps", []):
            start_loc = step_data.get("start_location", {})
            end_loc = step_data.get("end_location", {})

            step = DirectionStep(
                instruction=step_data.get("html_instructions", ""),
                distance_meters=step_data.get("distance", {}).get("value", 0.0),
                duration_seconds=step_data.get("duration", {}).get("value", 0.0),
                start_location=GPSCoordinates(
                    latitude=start_loc.get("lat", 0.0),
                    longitude=start_loc.get("lng", 0.0),
                    accuracy=0.0,
                    timestamp=time.time(),
                ),
                end_location=GPSCoordinates(
                    latitude=end_loc.get("lat", 0.0),
                    longitude=end_loc.get("lng", 0.0),
                    accuracy=0.0,
                    timestamp=time.time(),
                ),
                maneuver=step_data.get("maneuver"),
            )
            steps.append(step)

        return Directions(
            origin=origin,
            destination=destination,
            distance_meters=leg.get("distance", {}).get("value", 0.0),
            duration_seconds=leg.get("duration", {}).get("value", 0.0),
            steps=steps,
            polyline=route_data.get("overview_polyline", {}).get("points", ""),
        )

    @staticmethod
    def _calculate_distance(coord1: GPSCoordinates, coord2: GPSCoordinates) -> float:
        """Calculate distance between two GPS coordinates using Haversine formula.

        Returns distance in meters.
        """
        # Earth radius in meters
        R = 6371000.0

        lat1 = math.radians(coord1.latitude)
        lat2 = math.radians(coord2.latitude)
        dlat = math.radians(coord2.latitude - coord1.latitude)
        dlon = math.radians(coord2.longitude - coord1.longitude)

        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        )
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        return R * c

    @staticmethod
    def _calculate_bearing(
        from_coord: GPSCoordinates, to_coord: GPSCoordinates
    ) -> float:
        """Calculate compass bearing from one coordinate to another.

        Returns bearing in degrees (0-360, where 0 is North).
        """
        lat1 = math.radians(from_coord.latitude)
        lat2 = math.radians(to_coord.latitude)
        dlon = math.radians(to_coord.longitude - from_coord.longitude)

        y = math.sin(dlon) * math.cos(lat2)
        x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(
            lat2
        ) * math.cos(dlon)

        bearing = math.degrees(math.atan2(y, x))
        return (bearing + 360) % 360

    def _calculate_proximity_score(self, distance_meters: float) -> float:
        """Calculate proximity score (0-1) based on distance.

        Closer landmarks score higher.
        """
        if distance_meters <= 0:
            return 1.0
        if distance_meters >= self._trigger_radius:
            return 0.0

        # Linear decay from 1.0 at 0m to 0.0 at trigger_radius
        return 1.0 - (distance_meters / self._trigger_radius)

    @staticmethod
    def _calculate_interest_score(
        landmark: Landmark, user_history: UserHistory
    ) -> float:
        """Calculate interest score (0-1) based on user history.

        Higher score for landmark types the user has shown interest in.
        """
        if not landmark.types:
            return 0.5  # Neutral score

        # Check if user has visited this landmark before
        if landmark.place_id in user_history.visited_place_ids:
            return 0.2  # Lower score for already visited

        # Calculate average interest score for landmark types
        type_scores = []
        for place_type in landmark.types:
            score = user_history.location_type_interests.get(place_type, 0.5)
            type_scores.append(score)

        if type_scores:
            return sum(type_scores) / len(type_scores)

        return 0.5  # Neutral score

    def _calculate_recency_penalty(
        self, landmark: Landmark, user_history: UserHistory
    ) -> float:
        """Calculate recency penalty (0-1) based on last trigger time.

        Recently triggered landmarks score lower.
        """
        if landmark.place_id == user_history.last_triggered_place_id:
            time_since = time.time() - user_history.last_trigger_timestamp
            if time_since < self._min_trigger_interval:
                return 0.0  # Full penalty

            # Gradual recovery over 2x the minimum interval
            recovery_period = self._min_trigger_interval * 2
            if time_since < recovery_period:
                return time_since / recovery_period

        return 1.0  # No penalty

    @staticmethod
    def _estimate_historical_significance(
        types: list[str], rating: Optional[float], rating_count: int
    ) -> float:
        """Estimate historical significance (0-1) from place metadata."""
        significance = 0.5  # Base score

        # Boost for landmark types
        landmark_types = set(types) & _LANDMARK_TYPES
        if landmark_types:
            significance += min(len(landmark_types) * 0.1, 0.3)

        # Boost for high ratings with many reviews
        if rating and rating >= 4.0 and rating_count >= 100:
            significance += 0.2

        return min(significance, 1.0)

    @staticmethod
    def _build_trigger_reason(
        should_trigger: bool,
        proximity_score: float,
        interest_score: float,
        recency_penalty: float,
    ) -> str:
        """Build human-readable explanation of trigger decision."""
        if should_trigger:
            return (
                f"Triggered: proximity={proximity_score:.2f}, "
                f"interest={interest_score:.2f}, recency={recency_penalty:.2f}"
            )
        return (
            f"Not triggered: priority score too low "
            f"(proximity={proximity_score:.2f}, interest={interest_score:.2f}, "
            f"recency={recency_penalty:.2f})"
        )

    async def _get_session(self) -> aiohttp.ClientSession:
        """Return or lazily create the shared aiohttp session."""
        if self._http_session is None:
            self._http_session = aiohttp.ClientSession()
        return self._http_session
