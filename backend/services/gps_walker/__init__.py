"""GPS Walker service – location-based walking tour manager.

Design reference: LORE design.md, Section 7 – GPS Walker.
Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 29.4.

Public exports
--------------
GPSWalkingTourManager:
    Main service class for GPS-based walking tours.
GPSCoordinates, Landmark, PointOfInterest, Directions:
    Data models for GPS operations.
"""

from .manager import GPSWalkingTourManager
from .models import (
    Directions,
    GPSCoordinates,
    Landmark,
    PointOfInterest,
    TriggerDecision,
    UserHistory,
)

__all__ = [
    "GPSWalkingTourManager",
    "GPSCoordinates",
    "Landmark",
    "PointOfInterest",
    "Directions",
    "TriggerDecision",
    "UserHistory",
]
