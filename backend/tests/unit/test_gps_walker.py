"""Unit tests for GPS Walker service.

Tests cover:
- Landmark detection and parsing
- Trigger decision logic
- Proximity scoring
- Interest scoring
- Recency penalties
- Landmark prioritization
- Directions API integration
- GPS signal loss handling
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.gps_walker.manager import GPSWalkingTourManager
from backend.services.gps_walker.models import (
    GPSCoordinates,
    GPSStatus,
    GPSUpdate,
    Landmark,
    UserHistory,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_http_session():
    """Mock aiohttp.ClientSession for testing."""
    session = MagicMock()
    return session


@pytest.fixture
def gps_manager(mock_http_session):
    """Create GPSWalkingTourManager with mocked HTTP session."""
    return GPSWalkingTourManager(
        places_api_key="test_places_key",
        maps_api_key="test_maps_key",
        http_session=mock_http_session,
        trigger_radius=50.0,
        min_trigger_interval=300.0,
        max_accuracy=10.0,
    )


@pytest.fixture
def sample_coordinates():
    """Sample GPS coordinates (Eiffel Tower)."""
    return GPSCoordinates(
        latitude=48.8584,
        longitude=2.2945,
        accuracy=5.0,
        timestamp=time.time(),
    )


@pytest.fixture
def sample_landmark(sample_coordinates):
    """Sample landmark for testing."""
    return Landmark(
        place_id="ChIJLU7jZClu5kcR4PcOOO6p3I0",
        name="Eiffel Tower",
        location=sample_coordinates,
        types=["tourist_attraction", "monument"],
        description="Iconic iron lattice tower",
        historical_significance=0.9,
        distance_meters=25.0,
        rating=4.7,
        user_ratings_total=150000,
    )


@pytest.fixture
def sample_user_history():
    """Sample user history for testing."""
    return UserHistory(
        user_id="test_user",
        visited_place_ids=set(),
        topic_interests={},
        location_type_interests={"museum": 0.8, "monument": 0.7},
    )


# ── Distance calculation tests ────────────────────────────────────────────────


def test_calculate_distance_same_location(gps_manager, sample_coordinates):
    """Test distance calculation for same location."""
    distance = gps_manager._calculate_distance(sample_coordinates, sample_coordinates)
    assert distance == 0.0


def test_calculate_distance_known_locations(gps_manager):
    """Test distance calculation between known locations."""
    # Eiffel Tower to Arc de Triomphe (approximately 1.7 km)
    eiffel = GPSCoordinates(latitude=48.8584, longitude=2.2945, accuracy=0, timestamp=0)
    arc = GPSCoordinates(latitude=48.8738, longitude=2.2950, accuracy=0, timestamp=0)

    distance = gps_manager._calculate_distance(eiffel, arc)

    # Should be approximately 1700 meters (allow 10% tolerance)
    assert 1530 <= distance <= 1870


def test_calculate_distance_equator_crossing(gps_manager):
    """Test distance calculation across equator."""
    north = GPSCoordinates(latitude=1.0, longitude=0.0, accuracy=0, timestamp=0)
    south = GPSCoordinates(latitude=-1.0, longitude=0.0, accuracy=0, timestamp=0)

    distance = gps_manager._calculate_distance(north, south)

    # Should be approximately 222 km
    assert 220000 <= distance <= 224000


# ── Bearing calculation tests ─────────────────────────────────────────────────


def test_calculate_bearing_north(gps_manager):
    """Test bearing calculation for northward direction."""
    start = GPSCoordinates(latitude=0.0, longitude=0.0, accuracy=0, timestamp=0)
    end = GPSCoordinates(latitude=1.0, longitude=0.0, accuracy=0, timestamp=0)

    bearing = gps_manager._calculate_bearing(start, end)

    # Should be approximately 0 degrees (North)
    assert -5 <= bearing <= 5


def test_calculate_bearing_east(gps_manager):
    """Test bearing calculation for eastward direction."""
    start = GPSCoordinates(latitude=0.0, longitude=0.0, accuracy=0, timestamp=0)
    end = GPSCoordinates(latitude=0.0, longitude=1.0, accuracy=0, timestamp=0)

    bearing = gps_manager._calculate_bearing(start, end)

    # Should be approximately 90 degrees (East)
    assert 85 <= bearing <= 95


def test_calculate_bearing_south(gps_manager):
    """Test bearing calculation for southward direction."""
    start = GPSCoordinates(latitude=1.0, longitude=0.0, accuracy=0, timestamp=0)
    end = GPSCoordinates(latitude=0.0, longitude=0.0, accuracy=0, timestamp=0)

    bearing = gps_manager._calculate_bearing(start, end)

    # Should be approximately 180 degrees (South)
    assert 175 <= bearing <= 185


def test_calculate_bearing_west(gps_manager):
    """Test bearing calculation for westward direction."""
    start = GPSCoordinates(latitude=0.0, longitude=1.0, accuracy=0, timestamp=0)
    end = GPSCoordinates(latitude=0.0, longitude=0.0, accuracy=0, timestamp=0)

    bearing = gps_manager._calculate_bearing(start, end)

    # Should be approximately 270 degrees (West)
    assert 265 <= bearing <= 275


# ── Proximity scoring tests ───────────────────────────────────────────────────


def test_proximity_score_zero_distance(gps_manager):
    """Test proximity score at zero distance."""
    score = gps_manager._calculate_proximity_score(0.0)
    assert score == 1.0


def test_proximity_score_max_distance(gps_manager):
    """Test proximity score at trigger radius."""
    score = gps_manager._calculate_proximity_score(50.0)
    assert score == 0.0


def test_proximity_score_mid_distance(gps_manager):
    """Test proximity score at mid-range distance."""
    score = gps_manager._calculate_proximity_score(25.0)
    assert score == 0.5


def test_proximity_score_beyond_radius(gps_manager):
    """Test proximity score beyond trigger radius."""
    score = gps_manager._calculate_proximity_score(100.0)
    assert score == 0.0


# ── Interest scoring tests ────────────────────────────────────────────────────


def test_interest_score_matching_type(gps_manager, sample_landmark, sample_user_history):
    """Test interest score for landmark with matching user interests."""
    score = gps_manager._calculate_interest_score(sample_landmark, sample_user_history)

    # User has interest in "monument" (0.7), should reflect in score
    assert 0.6 <= score <= 0.8


def test_interest_score_no_types(gps_manager, sample_user_history):
    """Test interest score for landmark with no types."""
    landmark = Landmark(
        place_id="test",
        name="Test",
        location=GPSCoordinates(latitude=0, longitude=0, accuracy=0, timestamp=0),
        types=[],
        description="",
        historical_significance=0.5,
        distance_meters=10.0,
    )

    score = gps_manager._calculate_interest_score(landmark, sample_user_history)
    assert score == 0.5  # Neutral score


def test_interest_score_already_visited(gps_manager, sample_landmark, sample_user_history):
    """Test interest score for already visited landmark."""
    sample_user_history.visited_place_ids.add(sample_landmark.place_id)

    score = gps_manager._calculate_interest_score(sample_landmark, sample_user_history)
    assert score == 0.2  # Lower score for visited


def test_interest_score_no_history(gps_manager, sample_landmark):
    """Test interest score with no user history."""
    empty_history = UserHistory(user_id="test")

    score = gps_manager._calculate_interest_score(sample_landmark, empty_history)
    assert score == 0.5  # Neutral score


# ── Recency penalty tests ─────────────────────────────────────────────────────


def test_recency_penalty_no_recent_trigger(gps_manager, sample_landmark, sample_user_history):
    """Test recency penalty with no recent trigger."""
    penalty = gps_manager._calculate_recency_penalty(sample_landmark, sample_user_history)
    assert penalty == 1.0  # No penalty


def test_recency_penalty_recent_trigger(gps_manager, sample_landmark, sample_user_history):
    """Test recency penalty for recently triggered landmark."""
    sample_user_history.last_triggered_place_id = sample_landmark.place_id
    sample_user_history.last_trigger_timestamp = time.time() - 60.0  # 1 minute ago

    penalty = gps_manager._calculate_recency_penalty(sample_landmark, sample_user_history)
    assert penalty == 0.0  # Full penalty within min interval


def test_recency_penalty_partial_recovery(gps_manager, sample_landmark, sample_user_history):
    """Test recency penalty during recovery period."""
    sample_user_history.last_triggered_place_id = sample_landmark.place_id
    sample_user_history.last_trigger_timestamp = time.time() - 450.0  # 7.5 minutes ago

    penalty = gps_manager._calculate_recency_penalty(sample_landmark, sample_user_history)

    # Should be in recovery period (300-600s)
    assert 0.0 < penalty < 1.0


def test_recency_penalty_full_recovery(gps_manager, sample_landmark, sample_user_history):
    """Test recency penalty after full recovery."""
    sample_user_history.last_triggered_place_id = sample_landmark.place_id
    sample_user_history.last_trigger_timestamp = time.time() - 700.0  # 11.7 minutes ago

    penalty = gps_manager._calculate_recency_penalty(sample_landmark, sample_user_history)
    assert penalty == 1.0  # Full recovery


# ── Historical significance tests ─────────────────────────────────────────────


def test_estimate_significance_landmark_types(gps_manager):
    """Test significance estimation for landmark types."""
    types = ["tourist_attraction", "monument", "museum"]
    significance = gps_manager._estimate_historical_significance(types, None, 0)

    # Base 0.5 + 0.3 for landmark types
    assert significance >= 0.8


def test_estimate_significance_high_rating(gps_manager):
    """Test significance estimation with high rating."""
    types = ["tourist_attraction"]
    significance = gps_manager._estimate_historical_significance(types, 4.5, 500)

    # Base 0.5 + 0.1 for type + 0.2 for rating
    assert significance >= 0.8


def test_estimate_significance_no_data(gps_manager):
    """Test significance estimation with no data."""
    significance = gps_manager._estimate_historical_significance([], None, 0)
    assert significance == 0.5  # Base score


def test_estimate_significance_capped(gps_manager):
    """Test significance estimation is capped at 1.0."""
    types = ["tourist_attraction", "monument", "museum", "church"]
    significance = gps_manager._estimate_historical_significance(types, 5.0, 10000)

    assert significance <= 1.0


# ── Trigger decision tests ────────────────────────────────────────────────────


def test_should_trigger_close_landmark(gps_manager, sample_user_history):
    """Test trigger decision for close landmark."""
    landmark = Landmark(
        place_id="test",
        name="Test Landmark",
        location=GPSCoordinates(latitude=0, longitude=0, accuracy=0, timestamp=0),
        types=["monument"],
        description="Test",
        historical_significance=0.8,
        distance_meters=10.0,  # Very close
    )

    decision = gps_manager.should_trigger_documentary(landmark, sample_user_history)

    assert decision.should_trigger is True
    assert decision.proximity_score >= 0.8
    assert decision.priority_score >= 0.4


def test_should_trigger_far_landmark(gps_manager, sample_user_history):
    """Test trigger decision for far landmark."""
    landmark = Landmark(
        place_id="test",
        name="Test Landmark",
        location=GPSCoordinates(latitude=0, longitude=0, accuracy=0, timestamp=0),
        types=["monument"],
        description="Test",
        historical_significance=0.8,
        distance_meters=48.0,  # Near trigger radius
    )

    decision = gps_manager.should_trigger_documentary(landmark, sample_user_history)

    # Low proximity score should result in no trigger
    assert decision.proximity_score < 0.1


def test_should_trigger_recent_trigger(gps_manager, sample_landmark, sample_user_history):
    """Test trigger decision for recently triggered landmark."""
    # Simulate recent trigger
    gps_manager._last_trigger_times[sample_landmark.place_id] = time.time() - 60.0

    decision = gps_manager.should_trigger_documentary(sample_landmark, sample_user_history)

    assert decision.should_trigger is False
    assert "Recently triggered" in decision.reason


def test_should_trigger_after_interval(gps_manager, sample_landmark, sample_user_history):
    """Test trigger decision after minimum interval."""
    # Simulate old trigger
    gps_manager._last_trigger_times[sample_landmark.place_id] = time.time() - 400.0

    decision = gps_manager.should_trigger_documentary(sample_landmark, sample_user_history)

    # Should be allowed to trigger again
    assert decision.should_trigger is True


# ── Landmark prioritization tests ─────────────────────────────────────────────


def test_prioritize_landmarks_by_distance(gps_manager, sample_user_history):
    """Test landmark prioritization by distance."""
    landmarks = [
        Landmark(
            place_id="far",
            name="Far Landmark",
            location=GPSCoordinates(latitude=0, longitude=0, accuracy=0, timestamp=0),
            types=["monument"],
            description="",
            historical_significance=0.5,
            distance_meters=40.0,
        ),
        Landmark(
            place_id="close",
            name="Close Landmark",
            location=GPSCoordinates(latitude=0, longitude=0, accuracy=0, timestamp=0),
            types=["monument"],
            description="",
            historical_significance=0.5,
            distance_meters=10.0,
        ),
    ]

    prioritized = gps_manager.prioritize_landmarks(landmarks, sample_user_history)

    # Closer landmark should be first
    assert prioritized[0].place_id == "close"
    assert prioritized[1].place_id == "far"


def test_prioritize_landmarks_by_interest(gps_manager):
    """Test landmark prioritization by user interest."""
    user_history = UserHistory(
        user_id="test",
        location_type_interests={"museum": 0.9, "park": 0.3},
    )

    landmarks = [
        Landmark(
            place_id="park",
            name="Park",
            location=GPSCoordinates(latitude=0, longitude=0, accuracy=0, timestamp=0),
            types=["park"],
            description="",
            historical_significance=0.5,
            distance_meters=20.0,
        ),
        Landmark(
            place_id="museum",
            name="Museum",
            location=GPSCoordinates(latitude=0, longitude=0, accuracy=0, timestamp=0),
            types=["museum"],
            description="",
            historical_significance=0.5,
            distance_meters=20.0,  # Same distance
        ),
    ]

    prioritized = gps_manager.prioritize_landmarks(landmarks, user_history)

    # Museum should be first due to higher interest
    assert prioritized[0].place_id == "museum"


def test_prioritize_landmarks_empty_list(gps_manager, sample_user_history):
    """Test landmark prioritization with empty list."""
    prioritized = gps_manager.prioritize_landmarks([], sample_user_history)
    assert prioritized == []


# ── Places API integration tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_detect_nearby_landmarks_success(gps_manager, mock_http_session, sample_coordinates):
    """Test successful landmark detection."""
    # Mock Places API response
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(
        return_value={
            "places": [
                {
                    "id": "test_place_1",
                    "displayName": {"text": "Test Landmark"},
                    "location": {"latitude": 48.8584, "longitude": 2.2945},
                    "types": ["tourist_attraction"],
                    "editorialSummary": {"text": "A test landmark"},
                    "rating": 4.5,
                    "userRatingCount": 1000,
                }
            ]
        }
    )

    # Create a proper async context manager mock
    mock_context = AsyncMock()
    mock_context.__aenter__.return_value = mock_response
    mock_context.__aexit__.return_value = None
    mock_http_session.post.return_value = mock_context

    landmarks = await gps_manager.detect_nearby_landmarks(sample_coordinates, 50.0)

    assert len(landmarks) == 1
    assert landmarks[0].name == "Test Landmark"
    assert landmarks[0].place_id == "test_place_1"


@pytest.mark.asyncio
async def test_detect_nearby_landmarks_no_results(gps_manager, mock_http_session, sample_coordinates):
    """Test landmark detection with no results."""
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"places": []})

    mock_context = AsyncMock()
    mock_context.__aenter__.return_value = mock_response
    mock_context.__aexit__.return_value = None
    mock_http_session.post.return_value = mock_context

    landmarks = await gps_manager.detect_nearby_landmarks(sample_coordinates, 50.0)

    assert landmarks == []


@pytest.mark.asyncio
async def test_detect_nearby_landmarks_api_error(gps_manager, mock_http_session, sample_coordinates):
    """Test landmark detection with API error."""
    mock_response = AsyncMock()
    mock_response.status = 500
    mock_response.text = AsyncMock(return_value="Internal Server Error")

    mock_context = AsyncMock()
    mock_context.__aenter__.return_value = mock_response
    mock_context.__aexit__.return_value = None
    mock_http_session.post.return_value = mock_context

    landmarks = await gps_manager.detect_nearby_landmarks(sample_coordinates, 50.0)

    assert landmarks == []


@pytest.mark.asyncio
async def test_detect_nearby_landmarks_network_error(gps_manager, mock_http_session, sample_coordinates):
    """Test landmark detection with network error."""
    import aiohttp

    mock_http_session.post.side_effect = aiohttp.ClientError("Network error")

    landmarks = await gps_manager.detect_nearby_landmarks(sample_coordinates, 50.0)

    assert landmarks == []


# ── Directions API integration tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_directions_success(gps_manager, mock_http_session, sample_coordinates, sample_landmark):
    """Test successful directions retrieval."""
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(
        return_value={
            "status": "OK",
            "routes": [
                {
                    "legs": [
                        {
                            "distance": {"value": 500},
                            "duration": {"value": 360},
                            "steps": [
                                {
                                    "html_instructions": "Head north",
                                    "distance": {"value": 100},
                                    "duration": {"value": 72},
                                    "start_location": {"lat": 48.8584, "lng": 2.2945},
                                    "end_location": {"lat": 48.8594, "lng": 2.2945},
                                    "maneuver": "straight",
                                }
                            ],
                        }
                    ],
                    "overview_polyline": {"points": "encoded_polyline"},
                }
            ],
        }
    )

    mock_context = AsyncMock()
    mock_context.__aenter__.return_value = mock_response
    mock_context.__aexit__.return_value = None
    mock_http_session.get.return_value = mock_context

    directions = await gps_manager.get_directions(sample_coordinates, sample_landmark)

    assert directions is not None
    assert directions.distance_meters == 500
    assert directions.duration_seconds == 360
    assert len(directions.steps) == 1
    assert directions.steps[0].instruction == "Head north"


@pytest.mark.asyncio
async def test_get_directions_no_route(gps_manager, mock_http_session, sample_coordinates, sample_landmark):
    """Test directions retrieval with no route found."""
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"status": "ZERO_RESULTS", "routes": []})

    mock_context = AsyncMock()
    mock_context.__aenter__.return_value = mock_response
    mock_context.__aexit__.return_value = None
    mock_http_session.get.return_value = mock_context

    directions = await gps_manager.get_directions(sample_coordinates, sample_landmark)

    assert directions is None


@pytest.mark.asyncio
async def test_get_directions_api_error(gps_manager, mock_http_session, sample_coordinates, sample_landmark):
    """Test directions retrieval with API error."""
    mock_response = AsyncMock()
    mock_response.status = 500
    mock_response.text = AsyncMock(return_value="Internal Server Error")

    mock_context = AsyncMock()
    mock_context.__aenter__.return_value = mock_response
    mock_context.__aexit__.return_value = None
    mock_http_session.get.return_value = mock_context

    directions = await gps_manager.get_directions(sample_coordinates, sample_landmark)

    assert directions is None


# ── POI finding tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_nearby_pois(gps_manager, mock_http_session, sample_coordinates):
    """Test finding nearby points of interest."""
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(
        return_value={
            "places": [
                {
                    "id": "poi_1",
                    "displayName": {"text": "POI 1"},
                    "location": {"latitude": 48.8594, "longitude": 2.2945},
                    "types": ["museum"],
                    "editorialSummary": {"text": "A museum"},
                    "rating": 4.0,
                    "userRatingCount": 500,
                }
            ]
        }
    )

    mock_context = AsyncMock()
    mock_context.__aenter__.return_value = mock_response
    mock_context.__aexit__.return_value = None
    mock_http_session.post.return_value = mock_context

    pois = await gps_manager.find_nearby_pois(sample_coordinates, 500.0)

    assert len(pois) == 1
    assert pois[0].name == "POI 1"
    assert pois[0].bearing_degrees >= 0
    assert pois[0].bearing_degrees < 360
    assert pois[0].estimated_walk_time_minutes > 0


# ── GPS monitoring tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_monitor_location_trigger(gps_manager, mock_http_session):
    """Test GPS monitoring with landmark trigger."""
    # Mock Places API response
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(
        return_value={
            "places": [
                {
                    "id": "test_place",
                    "displayName": {"text": "Test Landmark"},
                    "location": {"latitude": 48.8584, "longitude": 2.2945},
                    "types": ["tourist_attraction"],
                    "editorialSummary": {"text": "Test"},
                    "rating": 4.5,
                    "userRatingCount": 1000,
                }
            ]
        }
    )

    mock_context = AsyncMock()
    mock_context.__aenter__.return_value = mock_response
    mock_context.__aexit__.return_value = None
    mock_http_session.post.return_value = mock_context

    # Create GPS update stream
    async def gps_stream():
        yield GPSUpdate(
            coordinates=GPSCoordinates(
                latitude=48.8584, longitude=2.2945, accuracy=5.0, timestamp=time.time()
            ),
            status=GPSStatus.AVAILABLE,
            timestamp=time.time(),
        )

    decisions = []
    async for decision in gps_manager.monitor_location("test_user", gps_stream()):
        decisions.append(decision)

    assert len(decisions) >= 1
    assert decisions[0].landmark.name == "Test Landmark"


@pytest.mark.asyncio
async def test_monitor_location_gps_unavailable(gps_manager):
    """Test GPS monitoring with unavailable signal."""

    async def gps_stream():
        yield GPSUpdate(
            coordinates=None,
            status=GPSStatus.UNAVAILABLE,
            error_message="GPS signal lost",
            timestamp=time.time(),
        )

    decisions = []
    async for decision in gps_manager.monitor_location("test_user", gps_stream()):
        decisions.append(decision)

    # Should not yield any decisions when GPS unavailable
    assert len(decisions) == 0


@pytest.mark.asyncio
async def test_monitor_location_poor_accuracy(gps_manager, mock_http_session):
    """Test GPS monitoring with poor accuracy."""

    async def gps_stream():
        yield GPSUpdate(
            coordinates=GPSCoordinates(
                latitude=48.8584,
                longitude=2.2945,
                accuracy=50.0,  # Poor accuracy
                timestamp=time.time(),
            ),
            status=GPSStatus.DEGRADED,
            timestamp=time.time(),
        )

    # Should still process but log warning
    decisions = []
    async for decision in gps_manager.monitor_location("test_user", gps_stream()):
        decisions.append(decision)
        break  # Just test one iteration


# ── Documentary trigger tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_documentary(gps_manager, sample_landmark):
    """Test documentary trigger request generation."""
    request = await gps_manager.trigger_documentary(sample_landmark)

    assert request["trigger_type"] == "gps_walker"
    assert request["place_id"] == sample_landmark.place_id
    assert request["place_name"] == sample_landmark.name
    assert request["distance_meters"] == sample_landmark.distance_meters


@pytest.mark.asyncio
async def test_trigger_documentary_with_callback(gps_manager, sample_landmark):
    """Test documentary trigger with callback."""
    callback_called = False
    callback_request = None

    async def callback(request):
        nonlocal callback_called, callback_request
        callback_called = True
        callback_request = request

    await gps_manager.trigger_documentary(sample_landmark, callback=callback)

    assert callback_called is True
    assert callback_request["place_id"] == sample_landmark.place_id


@pytest.mark.asyncio
async def test_trigger_documentary_callback_error(gps_manager, sample_landmark):
    """Test documentary trigger with callback error."""

    async def failing_callback(request):
        raise ValueError("Callback error")

    # Should not raise exception
    request = await gps_manager.trigger_documentary(
        sample_landmark, callback=failing_callback
    )

    assert request["place_id"] == sample_landmark.place_id


# ── Resource management tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_context_manager(mock_http_session):
    """Test GPSWalkingTourManager as async context manager."""
    async with GPSWalkingTourManager(
        places_api_key="test",
        maps_api_key="test",
        http_session=mock_http_session,
    ) as manager:
        assert manager is not None

    # Session should not be closed (not owned)
    assert not mock_http_session.close.called


@pytest.mark.asyncio
async def test_close_owned_session():
    """Test closing owned HTTP session."""
    manager = GPSWalkingTourManager(
        places_api_key="test",
        maps_api_key="test",
        # No session provided, will create own
    )

    # Trigger session creation
    await manager._get_session()

    await manager.close()

    # Session should be closed
    assert manager._http_session is None
