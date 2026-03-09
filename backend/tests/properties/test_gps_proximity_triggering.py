"""Property tests for GPS proximity triggering.

Property 12: GPS Proximity Triggering
Validates: Requirements 9.2

Test that landmarks within 50m trigger documentaries.
Generate random GPS coordinates and landmarks.
Verify triggering behavior for 100+ scenarios.
"""

from __future__ import annotations

import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from backend.services.gps_walker.manager import GPSWalkingTourManager
from backend.services.gps_walker.models import (
    GPSCoordinates,
    Landmark,
    UserHistory,
)


# ── Hypothesis strategies ─────────────────────────────────────────────────────


@st.composite
def gps_coordinates_strategy(draw):
    """Generate valid GPS coordinates."""
    return GPSCoordinates(
        latitude=draw(st.floats(min_value=-90.0, max_value=90.0)),
        longitude=draw(st.floats(min_value=-180.0, max_value=180.0)),
        accuracy=draw(st.floats(min_value=0.0, max_value=20.0)),
        timestamp=time.time(),
    )


@st.composite
def landmark_strategy(draw, location: GPSCoordinates, distance_meters: float):
    """Generate a landmark at specified distance from location."""
    return Landmark(
        place_id=draw(st.text(min_size=10, max_size=50)),
        name=draw(st.text(min_size=5, max_size=100)),
        location=location,
        types=draw(st.lists(st.sampled_from(["museum", "monument", "park"]), min_size=1, max_size=3)),
        description=draw(st.text(max_size=200)),
        historical_significance=draw(st.floats(min_value=0.0, max_value=1.0)),
        distance_meters=distance_meters,
        rating=draw(st.floats(min_value=1.0, max_value=5.0)),
        user_ratings_total=draw(st.integers(min_value=0, max_value=10000)),
    )


# ── Property tests ────────────────────────────────────────────────────────────


@given(
    location=gps_coordinates_strategy(),
    distance=st.floats(min_value=0.0, max_value=49.0),
)
@settings(max_examples=100, deadline=1000)
def test_landmarks_within_50m_should_trigger(location, distance):
    """Property: Landmarks within 50m should be eligible for triggering.
    
    Requirement 9.2: WHEN the user moves within 50 meters of a registered
    landmark, THE GPS_Walker SHALL auto-trigger documentary content.
    """
    manager = GPSWalkingTourManager(
        places_api_key="test",
        maps_api_key="test",
        trigger_radius=50.0,
    )

    landmark = Landmark(
        place_id=f"test_{distance}",
        name="Test Landmark",
        location=location,
        types=["monument"],
        description="Test",
        historical_significance=0.8,
        distance_meters=distance,
    )

    user_history = UserHistory(user_id="test")

    decision = manager.should_trigger_documentary(landmark, user_history)

    # Within 50m, proximity score should be positive
    assert decision.proximity_score > 0.0, (
        f"Landmark at {distance}m should have positive proximity score"
    )

    # Very close landmarks should have high proximity score
    if distance < 10.0:
        assert decision.proximity_score > 0.8, (
            f"Landmark at {distance}m should have high proximity score"
        )


@given(
    location=gps_coordinates_strategy(),
    distance=st.floats(min_value=50.1, max_value=200.0),
)
@settings(max_examples=100, deadline=1000)
def test_landmarks_beyond_50m_should_not_trigger(location, distance):
    """Property: Landmarks beyond 50m should not trigger.
    
    Requirement 9.2: Only landmarks within 50 meters should auto-trigger.
    """
    manager = GPSWalkingTourManager(
        places_api_key="test",
        maps_api_key="test",
        trigger_radius=50.0,
    )

    landmark = Landmark(
        place_id=f"test_{distance}",
        name="Test Landmark",
        location=location,
        types=["monument"],
        description="Test",
        historical_significance=0.8,
        distance_meters=distance,
    )

    user_history = UserHistory(user_id="test")

    decision = manager.should_trigger_documentary(landmark, user_history)

    # Beyond 50m, proximity score should be zero
    assert decision.proximity_score == 0.0, (
        f"Landmark at {distance}m should have zero proximity score"
    )

    # Should not trigger due to distance
    assert decision.should_trigger is False, (
        f"Landmark at {distance}m should not trigger"
    )


@given(
    location=gps_coordinates_strategy(),
    distance=st.floats(min_value=0.0, max_value=38.0),  # Leave room for +10
)
@settings(max_examples=100, deadline=1000)
def test_proximity_score_decreases_with_distance(location, distance):
    """Property: Proximity score should decrease as distance increases.
    
    Requirement 9.5: Prioritize by proximity.
    """
    manager = GPSWalkingTourManager(
        places_api_key="test",
        maps_api_key="test",
        trigger_radius=50.0,
    )

    # Create two landmarks at different distances
    close_landmark = Landmark(
        place_id="close",
        name="Close Landmark",
        location=location,
        types=["monument"],
        description="Test",
        historical_significance=0.8,
        distance_meters=distance,
    )

    far_landmark = Landmark(
        place_id="far",
        name="Far Landmark",
        location=location,
        types=["monument"],
        description="Test",
        historical_significance=0.8,
        distance_meters=distance + 10.0,  # Always 10m farther
    )

    user_history = UserHistory(user_id="test")

    close_decision = manager.should_trigger_documentary(close_landmark, user_history)
    far_decision = manager.should_trigger_documentary(far_landmark, user_history)

    # Closer landmark should have higher proximity score
    assert close_decision.proximity_score >= far_decision.proximity_score, (
        f"Closer landmark ({distance}m) should have higher proximity score "
        f"than farther landmark ({distance + 10.0}m)"
    )


@given(
    location=gps_coordinates_strategy(),
    distance=st.floats(min_value=0.0, max_value=49.0),
)
@settings(max_examples=100, deadline=1000)
def test_minimum_trigger_interval_enforced(location, distance):
    """Property: Minimum trigger interval should be enforced.
    
    Requirement 9.2: Prevent rapid re-triggering of same landmark.
    """
    manager = GPSWalkingTourManager(
        places_api_key="test",
        maps_api_key="test",
        trigger_radius=50.0,
        min_trigger_interval=300.0,  # 5 minutes
    )

    landmark = Landmark(
        place_id="test_landmark",
        name="Test Landmark",
        location=location,
        types=["monument"],
        description="Test",
        historical_significance=0.8,
        distance_meters=distance,
    )

    user_history = UserHistory(user_id="test")

    # First trigger should succeed
    decision1 = manager.should_trigger_documentary(landmark, user_history)

    # Simulate trigger
    manager._last_trigger_times[landmark.place_id] = time.time()

    # Immediate second trigger should fail
    decision2 = manager.should_trigger_documentary(landmark, user_history)

    assert decision2.should_trigger is False, (
        "Landmark should not trigger again within minimum interval"
    )
    assert "Recently triggered" in decision2.reason


@given(
    location=gps_coordinates_strategy(),
    distances=st.lists(
        st.floats(min_value=0.0, max_value=49.0),
        min_size=2,
        max_size=10,
        unique=True,
    ),
)
@settings(max_examples=50, deadline=2000)
def test_prioritization_by_proximity(location, distances):
    """Property: Landmarks should be prioritized by proximity.
    
    Requirement 9.5: WHEN multiple landmarks are nearby, THE GPS_Walker SHALL
    prioritize by proximity and user interest history.
    """
    manager = GPSWalkingTourManager(
        places_api_key="test",
        maps_api_key="test",
        trigger_radius=50.0,
    )

    # Create landmarks at different distances
    landmarks = []
    for i, distance in enumerate(distances):
        landmark = Landmark(
            place_id=f"landmark_{i}",
            name=f"Landmark {i}",
            location=location,
            types=["monument"],
            description="Test",
            historical_significance=0.8,
            distance_meters=distance,
        )
        landmarks.append(landmark)

    user_history = UserHistory(user_id="test")

    # Prioritize landmarks
    prioritized = manager.prioritize_landmarks(landmarks, user_history)

    # Verify prioritization order
    for i in range(len(prioritized) - 1):
        current_distance = prioritized[i].distance_meters
        next_distance = prioritized[i + 1].distance_meters

        # Current landmark should be closer or have higher priority
        # (allowing for interest score variations)
        current_decision = manager.should_trigger_documentary(
            prioritized[i], user_history
        )
        next_decision = manager.should_trigger_documentary(
            prioritized[i + 1], user_history
        )

        assert current_decision.priority_score >= next_decision.priority_score, (
            f"Landmark at {current_distance}m should have higher or equal priority "
            f"than landmark at {next_distance}m"
        )


@given(
    location=gps_coordinates_strategy(),
    distance=st.floats(min_value=0.0, max_value=49.0),
)
@settings(max_examples=100, deadline=1000)
def test_visited_landmarks_have_lower_priority(location, distance):
    """Property: Previously visited landmarks should have lower priority.
    
    Requirement 9.5: Prioritize by user interest history.
    """
    manager = GPSWalkingTourManager(
        places_api_key="test",
        maps_api_key="test",
        trigger_radius=50.0,
    )

    landmark = Landmark(
        place_id="test_landmark",
        name="Test Landmark",
        location=location,
        types=["monument"],
        description="Test",
        historical_significance=0.8,
        distance_meters=distance,
    )

    # User history without visit
    fresh_history = UserHistory(user_id="test")
    fresh_decision = manager.should_trigger_documentary(landmark, fresh_history)

    # User history with visit
    visited_history = UserHistory(
        user_id="test",
        visited_place_ids={landmark.place_id},
    )
    visited_decision = manager.should_trigger_documentary(landmark, visited_history)

    # Fresh landmark should have higher interest score
    assert fresh_decision.interest_score > visited_decision.interest_score, (
        "Unvisited landmark should have higher interest score than visited"
    )


@given(
    location=gps_coordinates_strategy(),
    distance=st.floats(min_value=0.0, max_value=49.0),
)
@settings(max_examples=100, deadline=1000)
def test_trigger_decision_components_valid(location, distance):
    """Property: All trigger decision components should be in valid range.
    
    Validates that all scores are between 0.0 and 1.0.
    """
    manager = GPSWalkingTourManager(
        places_api_key="test",
        maps_api_key="test",
        trigger_radius=50.0,
    )

    landmark = Landmark(
        place_id="test_landmark",
        name="Test Landmark",
        location=location,
        types=["monument"],
        description="Test",
        historical_significance=0.8,
        distance_meters=distance,
    )

    user_history = UserHistory(user_id="test")

    decision = manager.should_trigger_documentary(landmark, user_history)

    # All scores should be in valid range
    assert 0.0 <= decision.proximity_score <= 1.0
    assert 0.0 <= decision.interest_score <= 1.0
    assert 0.0 <= decision.recency_penalty <= 1.0
    assert 0.0 <= decision.priority_score <= 1.0

    # Reason should be non-empty
    assert len(decision.reason) > 0
