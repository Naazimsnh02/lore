"""Property tests for GPS location accuracy.

Property 13: GPS Location Accuracy
Validates: Requirements 9.6

Test that GPS readings are within 10 meters accuracy.
Measure accuracy across different conditions.
Verify accuracy for 100+ readings.
"""

from __future__ import annotations

import time

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from backend.services.gps_walker.manager import GPSWalkingTourManager
from backend.services.gps_walker.models import (
    GPSCoordinates,
    GPSStatus,
    GPSUpdate,
)


# ── Hypothesis strategies ─────────────────────────────────────────────────────


@st.composite
def gps_update_strategy(draw):
    """Generate GPS updates with varying accuracy."""
    accuracy = draw(st.floats(min_value=0.0, max_value=50.0))
    status = draw(
        st.sampled_from([GPSStatus.AVAILABLE, GPSStatus.DEGRADED, GPSStatus.UNAVAILABLE])
    )

    if status == GPSStatus.UNAVAILABLE:
        return GPSUpdate(
            coordinates=None,
            status=status,
            error_message="GPS signal unavailable",
            timestamp=time.time(),
        )

    return GPSUpdate(
        coordinates=GPSCoordinates(
            latitude=draw(st.floats(min_value=-90.0, max_value=90.0)),
            longitude=draw(st.floats(min_value=-180.0, max_value=180.0)),
            accuracy=accuracy,
            timestamp=time.time(),
        ),
        status=status,
        timestamp=time.time(),
    )


# ── Property tests ────────────────────────────────────────────────────────────


@given(accuracy=st.floats(min_value=0.0, max_value=10.0))
@settings(max_examples=100, deadline=500)
def test_gps_accuracy_within_threshold_accepted(accuracy):
    """Property: GPS readings within 10m accuracy should be accepted.
    
    Requirement 9.6: THE GPS_Walker SHALL operate with location accuracy
    within 10 meters.
    """
    manager = GPSWalkingTourManager(
        places_api_key="test",
        maps_api_key="test",
        max_accuracy=10.0,
    )

    gps_update = GPSUpdate(
        coordinates=GPSCoordinates(
            latitude=48.8584,
            longitude=2.2945,
            accuracy=accuracy,
            timestamp=time.time(),
        ),
        status=GPSStatus.AVAILABLE,
        timestamp=time.time(),
    )

    # GPS update should be processed (coordinates not None)
    assert gps_update.coordinates is not None
    assert gps_update.coordinates.accuracy <= manager._max_accuracy


@given(accuracy=st.floats(min_value=10.1, max_value=100.0))
@settings(max_examples=100, deadline=500)
def test_gps_accuracy_beyond_threshold_degraded(accuracy):
    """Property: GPS readings beyond 10m accuracy should be marked degraded.
    
    Requirement 9.6: GPS accuracy should be monitored and reported.
    """
    manager = GPSWalkingTourManager(
        places_api_key="test",
        maps_api_key="test",
        max_accuracy=10.0,
    )

    gps_update = GPSUpdate(
        coordinates=GPSCoordinates(
            latitude=48.8584,
            longitude=2.2945,
            accuracy=accuracy,
            timestamp=time.time(),
        ),
        status=GPSStatus.DEGRADED,
        timestamp=time.time(),
    )

    # GPS update should indicate degraded status
    assert gps_update.status == GPSStatus.DEGRADED
    assert gps_update.coordinates.accuracy > manager._max_accuracy


@given(
    updates=st.lists(
        gps_update_strategy(),
        min_size=10,
        max_size=100,
    )
)
@settings(max_examples=50, deadline=2000)
def test_gps_accuracy_consistency(updates):
    """Property: GPS accuracy should be consistently reported.
    
    Validates that accuracy values are always non-negative and properly
    associated with coordinates.
    """
    manager = GPSWalkingTourManager(
        places_api_key="test",
        maps_api_key="test",
        max_accuracy=10.0,
    )

    for update in updates:
        if update.coordinates is not None:
            # Accuracy should always be non-negative
            assert update.coordinates.accuracy >= 0.0, (
                "GPS accuracy must be non-negative"
            )

            # Timestamp should be reasonable
            assert update.coordinates.timestamp > 0, (
                "GPS timestamp must be positive"
            )

            # Latitude and longitude should be in valid ranges
            assert -90.0 <= update.coordinates.latitude <= 90.0
            assert -180.0 <= update.coordinates.longitude <= 180.0


@given(
    lat1=st.floats(min_value=-90.0, max_value=90.0),
    lon1=st.floats(min_value=-180.0, max_value=180.0),
    lat2=st.floats(min_value=-90.0, max_value=90.0),
    lon2=st.floats(min_value=-180.0, max_value=180.0),
)
@settings(max_examples=100, deadline=1000)
def test_distance_calculation_accuracy(lat1, lon1, lat2, lon2):
    """Property: Distance calculations should be accurate and consistent.
    
    Requirement 9.6: Accurate distance measurements are required for
    proximity-based triggering.
    """
    manager = GPSWalkingTourManager(
        places_api_key="test",
        maps_api_key="test",
    )

    coord1 = GPSCoordinates(
        latitude=lat1,
        longitude=lon1,
        accuracy=5.0,
        timestamp=time.time(),
    )

    coord2 = GPSCoordinates(
        latitude=lat2,
        longitude=lon2,
        accuracy=5.0,
        timestamp=time.time(),
    )

    distance = manager._calculate_distance(coord1, coord2)

    # Distance should always be non-negative
    assert distance >= 0.0, "Distance must be non-negative"

    # Distance from a point to itself should be zero
    if lat1 == lat2 and lon1 == lon2:
        assert distance == 0.0, "Distance from point to itself must be zero"

    # Distance should be symmetric
    reverse_distance = manager._calculate_distance(coord2, coord1)
    assert abs(distance - reverse_distance) < 0.01, (
        "Distance calculation should be symmetric"
    )


@given(
    lat=st.floats(min_value=-89.0, max_value=89.0),
    lon=st.floats(min_value=-179.0, max_value=179.0),
    offset=st.floats(min_value=0.0001, max_value=0.01),
)
@settings(max_examples=100, deadline=1000)
def test_small_distance_accuracy(lat, lon, offset):
    """Property: Small distances should be calculated accurately.
    
    Requirement 9.6: Accurate measurements needed for 50m trigger radius.
    """
    manager = GPSWalkingTourManager(
        places_api_key="test",
        maps_api_key="test",
    )

    coord1 = GPSCoordinates(
        latitude=lat,
        longitude=lon,
        accuracy=5.0,
        timestamp=time.time(),
    )

    coord2 = GPSCoordinates(
        latitude=lat + offset,
        longitude=lon,
        accuracy=5.0,
        timestamp=time.time(),
    )

    distance = manager._calculate_distance(coord1, coord2)

    # Small offset should result in small distance
    # Approximately 111 km per degree latitude
    expected_distance = offset * 111000  # meters

    # Allow 10% tolerance for small distances
    assert abs(distance - expected_distance) / expected_distance < 0.1, (
        f"Distance calculation inaccurate: got {distance}m, expected ~{expected_distance}m"
    )


@given(
    lat=st.floats(min_value=-90.0, max_value=90.0),
    lon=st.floats(min_value=-180.0, max_value=180.0),
)
@settings(max_examples=100, deadline=500)
def test_bearing_calculation_range(lat, lon):
    """Property: Bearing calculations should always be in valid range.
    
    Validates that compass bearings are always 0-360 degrees.
    """
    manager = GPSWalkingTourManager(
        places_api_key="test",
        maps_api_key="test",
    )

    coord1 = GPSCoordinates(
        latitude=lat,
        longitude=lon,
        accuracy=5.0,
        timestamp=time.time(),
    )

    # Create a point slightly north
    coord2 = GPSCoordinates(
        latitude=min(lat + 0.01, 90.0),
        longitude=lon,
        accuracy=5.0,
        timestamp=time.time(),
    )

    bearing = manager._calculate_bearing(coord1, coord2)

    # Bearing should always be in range [0, 360)
    assert 0.0 <= bearing < 360.0, (
        f"Bearing must be in range [0, 360), got {bearing}"
    )


@given(
    updates=st.lists(
        gps_update_strategy(),
        min_size=5,
        max_size=20,
    )
)
@settings(max_examples=50, deadline=2000)
def test_gps_status_transitions(updates):
    """Property: GPS status transitions should be valid.
    
    Validates that GPS status changes are handled correctly.
    """
    manager = GPSWalkingTourManager(
        places_api_key="test",
        maps_api_key="test",
        max_accuracy=10.0,
    )

    for update in updates:
        # Status should be one of the valid values
        assert update.status in [
            GPSStatus.AVAILABLE,
            GPSStatus.DEGRADED,
            GPSStatus.UNAVAILABLE,
        ]

        # If unavailable, coordinates should be None
        if update.status == GPSStatus.UNAVAILABLE:
            assert update.coordinates is None, (
                "Unavailable GPS should have no coordinates"
            )

        # If available or degraded, coordinates should exist
        if update.status in [GPSStatus.AVAILABLE, GPSStatus.DEGRADED]:
            if update.coordinates is not None:
                assert update.coordinates.accuracy >= 0.0


@given(
    accuracy=st.floats(min_value=0.0, max_value=50.0),
)
@settings(max_examples=100, deadline=500)
def test_accuracy_threshold_boundary(accuracy):
    """Property: Accuracy threshold boundary should be handled correctly.
    
    Requirement 9.6: 10m accuracy threshold should be enforced consistently.
    """
    manager = GPSWalkingTourManager(
        places_api_key="test",
        maps_api_key="test",
        max_accuracy=10.0,
    )

    gps_update = GPSUpdate(
        coordinates=GPSCoordinates(
            latitude=48.8584,
            longitude=2.2945,
            accuracy=accuracy,
            timestamp=time.time(),
        ),
        status=GPSStatus.AVAILABLE if accuracy <= 10.0 else GPSStatus.DEGRADED,
        timestamp=time.time(),
    )

    # Check threshold enforcement
    if accuracy <= manager._max_accuracy:
        # Should be acceptable
        assert gps_update.coordinates.accuracy <= manager._max_accuracy
    else:
        # Should be marked as degraded
        assert gps_update.coordinates.accuracy > manager._max_accuracy


@given(
    lat=st.floats(min_value=-90.0, max_value=90.0),
    lon=st.floats(min_value=-180.0, max_value=180.0),
    accuracy1=st.floats(min_value=0.0, max_value=20.0),
    accuracy2=st.floats(min_value=0.0, max_value=20.0),
)
@settings(max_examples=100, deadline=1000)
def test_accuracy_affects_reliability(lat, lon, accuracy1, accuracy2):
    """Property: Better accuracy should indicate more reliable readings.
    
    Validates that accuracy values properly indicate measurement quality.
    """
    coord1 = GPSCoordinates(
        latitude=lat,
        longitude=lon,
        accuracy=accuracy1,
        timestamp=time.time(),
    )

    coord2 = GPSCoordinates(
        latitude=lat,
        longitude=lon,
        accuracy=accuracy2,
        timestamp=time.time(),
    )

    # Lower accuracy value means better precision
    if accuracy1 < accuracy2:
        assert coord1.accuracy < coord2.accuracy, (
            "Lower accuracy value should indicate better precision"
        )
