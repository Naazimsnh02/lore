"""Data models for Location Recognizer service.

Design reference: LORE design.md, Section 8 – Location Recognizer.
Requirements: 2.2, 2.4.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class GPSCoordinates(BaseModel):
    """GPS coordinate pair with accuracy metadata."""

    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    accuracy: float = Field(default=0.0, ge=0.0, description="Accuracy in metres")
    timestamp: float = Field(default=0.0, description="Unix epoch seconds")


class PlaceDetails(BaseModel):
    """Detailed information about a recognised place from Google Places API."""

    place_id: str = Field(..., description="Google Places unique identifier")
    name: str = Field(..., description="Human-readable place name")
    location: GPSCoordinates = Field(..., description="Geographic coordinates")
    types: list[str] = Field(default_factory=list, description="Place type tags")
    description: str = Field(default="", description="Short editorial description")
    photos: list[str] = Field(
        default_factory=list, description="Signed photo reference URLs"
    )
    formatted_address: str = Field(default="", description="Full formatted address")
    rating: Optional[float] = Field(
        default=None, ge=1.0, le=5.0, description="Google rating 1-5"
    )
    website: Optional[str] = Field(default=None, description="Official website URL")
    editorial_summary: str = Field(
        default="", description="Google editorial summary text"
    )


class VisualFeatures(BaseModel):
    """Extracted visual features from a camera frame used for place matching."""

    description: str = Field(
        ..., description="Gemini-generated textual description of the scene"
    )
    landmark_name: Optional[str] = Field(
        default=None, description="Identified landmark name if present"
    )
    architectural_style: Optional[str] = Field(
        default=None, description="Detected architectural or environmental style"
    )
    text_detected: list[str] = Field(
        default_factory=list, description="Any text/signage visible in the frame"
    )
    location_hint: Optional[str] = Field(
        default=None,
        description="City, region or country hint extracted from visual cues",
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Gemini confidence in extraction"
    )
    raw_response: dict[str, Any] = Field(
        default_factory=dict, description="Full Gemini response for debugging"
    )


class LocationResult(BaseModel):
    """Result returned by LocationRecognizer.recognize_location."""

    recognized: bool = Field(..., description="Whether a location was identified")
    place: Optional[PlaceDetails] = Field(
        default=None, description="Identified place details (None if not recognised)"
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Overall match confidence 0-1"
    )
    processing_time: float = Field(
        ..., ge=0.0, description="End-to-end wall-clock time in seconds"
    )
    visual_features: Optional[VisualFeatures] = Field(
        default=None, description="Extracted visual features (for debugging)"
    )
    error: Optional[str] = Field(
        default=None, description="Error message if recognition failed"
    )
