# GPS Walker Service

Location-based walking tour manager for the LORE multimodal documentary application.

## Overview

The GPS Walker service provides automatic location-based documentary triggering as users walk near landmarks and points of interest. It continuously monitors GPS location, detects nearby landmarks using Google Places API, and intelligently decides when to trigger documentary content based on proximity, user interest history, and recency.

## Features

- **Continuous GPS Monitoring**: Tracks user location with 10m accuracy requirement (Req 9.6)
- **Landmark Detection**: Detects landmarks within 50m radius using Google Places API (Req 9.2)
- **Smart Triggering**: Auto-triggers documentary content with intelligent prioritization (Req 9.5)
- **Directional Guidance**: Provides turn-by-turn directions to nearby POIs (Req 9.4)
- **Graceful Degradation**: Handles GPS signal loss and API failures (Req 9.7, 29.4)

## Architecture

### Core Components

1. **GPSWalkingTourManager**: Main service class
   - `monitor_location()`: Continuous GPS monitoring with trigger decisions
   - `detect_nearby_landmarks()`: Places API integration for landmark detection
   - `should_trigger_documentary()`: Intelligent trigger decision logic
   - `trigger_documentary()`: Documentary request generation
   - `get_directions()`: Turn-by-turn navigation
   - `find_nearby_pois()`: POI discovery with navigation info
   - `prioritize_landmarks()`: Multi-factor landmark ranking

2. **Data Models** (models.py):
   - `GPSCoordinates`: GPS location with accuracy metadata
   - `Landmark`: Detected landmark with metadata
   - `PointOfInterest`: POI with navigation information
   - `Directions`: Turn-by-turn directions
   - `TriggerDecision`: Trigger decision with reasoning
   - `UserHistory`: User interest tracking
   - `GPSUpdate`: GPS status updates

### Trigger Decision Algorithm

Priority score combines three factors:
- **Proximity (50%)**: Linear decay from 1.0 at 0m to 0.0 at 50m
- **Interest (30%)**: Based on user's historical preferences for landmark types
- **Recency (20%)**: Penalty for recently triggered landmarks (5-minute minimum interval)

Trigger threshold: priority_score >= 0.4

### API Integration

- **Google Places API v1**: Nearby Search for landmark detection
- **Google Maps Directions API**: Turn-by-turn walking directions
- **Distance Matrix API**: Travel time estimates

### Distance Calculations

- **Haversine Formula**: Accurate distance calculation between GPS coordinates
- **Compass Bearing**: Cardinal direction calculation for navigation
- **Accuracy**: Within 10 meters (Requirement 9.6)

## Usage

```python
from backend.services.gps_walker import GPSWalkingTourManager, GPSUpdate, GPSStatus

# Initialize manager
manager = GPSWalkingTourManager(
    places_api_key="YOUR_PLACES_API_KEY",
    maps_api_key="YOUR_MAPS_API_KEY",
    trigger_radius=50.0,  # meters
    min_trigger_interval=300.0,  # 5 minutes
    max_accuracy=10.0,  # meters
)

# Monitor GPS location stream
async def gps_stream():
    while True:
        # Get GPS update from device
        yield GPSUpdate(
            coordinates=GPSCoordinates(...),
            status=GPSStatus.AVAILABLE,
            timestamp=time.time(),
        )

# Process trigger decisions
async for decision in manager.monitor_location("user_id", gps_stream()):
    if decision.should_trigger:
        # Trigger documentary content
        await manager.trigger_documentary(
            decision.landmark,
            callback=orchestrator.process_gps_trigger
        )
```

## Testing

### Unit Tests (46 tests)
- Distance and bearing calculations
- Proximity scoring
- Interest scoring
- Recency penalties
- Trigger decision logic
- Landmark prioritization
- API integration (mocked)
- GPS signal handling
- Resource management

Run: `pytest tests/unit/test_gps_walker.py -v`

### Property Tests (16 tests, 100+ iterations each)
- **Property 12**: GPS Proximity Triggering (Req 9.2)
  - Landmarks within 50m trigger
  - Landmarks beyond 50m don't trigger
  - Proximity score decreases with distance
  - Minimum trigger interval enforced
  - Prioritization by proximity
  - Visited landmarks have lower priority

- **Property 13**: GPS Location Accuracy (Req 9.6)
  - Accuracy within 10m threshold
  - Degraded status for >10m accuracy
  - Distance calculation accuracy
  - Bearing calculation range
  - GPS status transitions

Run: `pytest tests/properties/test_gps_*.py -v`

## Requirements Satisfied

- **Req 9.1**: Continuous GPS location monitoring
- **Req 9.2**: Auto-trigger within 50 meters of landmarks
- **Req 9.3**: Google Maps Platform and Places API integration
- **Req 9.4**: Directional guidance to nearby POIs
- **Req 9.5**: Prioritization by proximity and user interest history
- **Req 9.6**: Location accuracy within 10 meters
- **Req 9.7**: GPS signal loss handling
- **Req 29.4**: Graceful degradation

## Dependencies

- `aiohttp>=3.9.0`: HTTP client for Google APIs
- `pydantic>=2.0.0`: Data validation

## Configuration

Environment variables:
- `GOOGLE_PLACES_API_KEY`: Google Places API key
- `GOOGLE_MAPS_API_KEY`: Google Maps API key

## Performance

- Landmark detection: < 3 seconds (Places API timeout)
- Directions retrieval: < 3 seconds (Directions API timeout)
- GPS accuracy threshold: 10 meters
- Trigger radius: 50 meters
- Minimum trigger interval: 5 minutes (300 seconds)

## Error Handling

- **GPS Unavailable**: Continues monitoring, skips processing
- **Poor Accuracy**: Logs warning, continues with degraded status
- **API Errors**: Returns empty results, logs error
- **Network Failures**: Graceful degradation, no crashes

## Integration Points

- **Orchestrator**: Receives documentary trigger requests
- **Session Memory Manager**: Loads user interest history
- **Location Recognizer**: Complementary visual landmark recognition
- **WebSocket Gateway**: Streams GPS updates from mobile clients

## Future Enhancements

- Offline landmark database for areas without connectivity
- Machine learning for personalized interest prediction
- Route planning for multi-landmark tours
- Augmented reality landmark overlay
- Social features (shared tours, recommendations)
