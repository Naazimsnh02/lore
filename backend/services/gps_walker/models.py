"""Data models for GPS Walker service.

Design reference: LORE design.md, Section 7 – GPS Walker.
Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class GPSCoordinates(BaseModel):
    """GPS coordinate pair with accuracy metadata.
    
    Requirement 9.6: Accuracy within 10 meters.
    """

    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    accuracy: float = Field(default=0.0, ge=0.0, description="Accuracy in metres")
    timestamp: float = Field(default=0.0, description="Unix epoch seconds")


class Landmark(BaseModel):
    """Detailed information about a landmark or point of interest.
    
    Requirement 9.2: Landmarks detected within 50 meters trigger content.
    """

    place_id: str = Field(..., description="Google Places unique identifier")
    name: str = Field(..., description="Human-readable landmark name")
    location: GPSCoordinates = Field(..., description="Geographic coordinates")
    types: list[str] = Field(default_factory=list, description="Place type tags")
    description: str = Field(default="", description="Short description")
    historical_significance: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Historical importance score 0-1",
    )
    distance_meters: float = Field(
        default=0.0, ge=0.0, description="Distance from user in meters"
    )
    rating: Optional[float] = Field(
        default=None, ge=1.0, le=5.0, description="Google rating 1-5"
    )
    user_ratings_total: int = Field(
        default=0, ge=0, description="Number of user ratings"
    )


class PointOfInterest(BaseModel):
    """A nearby point of interest with navigation information.
    
    Requirement 9.4: Provide directional guidance to POIs.
    """

    place_id: str
    name: str
    location: GPSCoordinates
    types: list[str] = Field(default_factory=list)
    distance_meters: float = Field(ge=0.0)
    bearing_degrees: float = Field(
        ge=0.0, lt=360.0, description="Compass bearing from user"
    )
    estimated_walk_time_minutes: float = Field(
        ge=0.0, description="Estimated walking time"
    )


class Directions(BaseModel):
    """Turn-by-turn directions from one location to another.
    
    Requirement 9.4: Provide directional guidance.
    """

    origin: GPSCoordinates
    destination: GPSCoordinates
    distance_meters: float = Field(ge=0.0)
    duration_seconds: float = Field(ge=0.0)
    steps: list[DirectionStep] = Field(default_factory=list)
    polyline: str = Field(default="", description="Encoded polyline for map display")


class DirectionStep(BaseModel):
    """A single step in turn-by-turn directions."""

    instruction: str = Field(..., description="Human-readable instruction")
    distance_meters: float = Field(ge=0.0)
    duration_seconds: float = Field(ge=0.0)
    start_location: GPSCoordinates
    end_location: GPSCoordinates
    maneuver: Optional[str] = Field(
        default=None, description="Maneuver type (turn-left, turn-right, etc.)"
    )


class UserHistory(BaseModel):
    """User interest history for landmark prioritization.
    
    Requirement 9.5: Prioritize by proximity and user interest history.
    """

    user_id: str
    visited_place_ids: set[str] = Field(default_factory=set)
    topic_interests: dict[str, float] = Field(
        default_factory=dict, description="Topic -> interest score (0-1)"
    )
    location_type_interests: dict[str, float] = Field(
        default_factory=dict, description="Place type -> interest score (0-1)"
    )
    last_triggered_place_id: Optional[str] = None
    last_trigger_timestamp: float = 0.0


class TriggerDecision(BaseModel):
    """Decision about whether to trigger documentary content for a landmark.
    
    Requirement 9.2: Auto-trigger within 50 meters.
    Requirement 9.5: Prioritize by proximity and user interest.
    """

    should_trigger: bool
    landmark: Landmark
    reason: str = Field(description="Human-readable explanation of decision")
    priority_score: float = Field(
        ge=0.0, le=1.0, description="Combined priority score"
    )
    proximity_score: float = Field(ge=0.0, le=1.0)
    interest_score: float = Field(ge=0.0, le=1.0)
    recency_penalty: float = Field(ge=0.0, le=1.0)


class GPSStatus(str, Enum):
    """GPS signal status.
    
    Requirement 9.7: Handle GPS signal loss gracefully.
    """

    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class GPSUpdate(BaseModel):
    """GPS location update with status information."""

    coordinates: Optional[GPSCoordinates] = None
    status: GPSStatus
    error_message: Optional[str] = None
    timestamp: float = Field(default=0.0)
