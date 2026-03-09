/// GPS Walking Tour screen — map view with auto-triggered landmarks.
///
/// Requirements 9.1–9.7, 24.4:
/// - Display map with user location and nearby landmarks
/// - Auto-trigger documentary content within 50m of landmarks
/// - Show directional guidance to POIs
/// - Handle GPS signal loss gracefully
library;

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:geolocator/geolocator.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart';
import 'package:permission_handler/permission_handler.dart';

import '../models/models.dart';
import '../providers/app_providers.dart';
import '../services/gps_service.dart';
import '../services/websocket_service.dart';
import '../widgets/documentary_stream_widget.dart';
import '../widgets/mode_switcher_widget.dart';

class GpsWalkingTourScreen extends ConsumerStatefulWidget {
  final LoreMode mode; // sight or lore

  const GpsWalkingTourScreen({
    super.key,
    this.mode = LoreMode.sight,
  });

  @override
  ConsumerState<GpsWalkingTourScreen> createState() =>
      _GpsWalkingTourScreenState();
}

class _GpsWalkingTourScreenState extends ConsumerState<GpsWalkingTourScreen> {
  late final GpsService _gpsService;
  late final WebSocketService _wsService;

  GoogleMapController? _mapController;
  StreamSubscription? _positionSub;
  StreamSubscription? _signalSub;
  StreamSubscription? _wsSub;

  Position? _currentPosition;
  bool _gpsReady = false;
  bool _gpsSignalLost = false;
  final Set<Marker> _markers = {};
  final Set<Polyline> _polylines = {};
  List<LandmarkInfo> _nearbyLandmarks = [];
  LandmarkInfo? _selectedLandmark;
  DirectionsResponse? _currentDirections;

  @override
  void initState() {
    super.initState();
    _gpsService = ref.read(gpsServiceProvider);
    _wsService = ref.read(webSocketServiceProvider);
    _initGps();
    _listenToWebSocket();
  }

  Future<void> _initGps() async {
    final status = await Permission.location.request();
    if (!status.isGranted) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
              content: Text('Location permission is required for GPS Walking Tour.')),
        );
      }
      return;
    }

    try {
      final started = await _gpsService.startMonitoring();
      if (!started) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('GPS service unavailable.')),
          );
        }
        return;
      }

      setState(() => _gpsReady = true);

      // Listen to position updates
      _positionSub = _gpsService.positions.listen((position) {
        setState(() => _currentPosition = position);

        // Send GPS update to backend
        _wsService.send(GpsUpdateMessage(
          latitude: position.latitude,
          longitude: position.longitude,
          accuracy: position.accuracy,
          timestamp: position.timestamp.millisecondsSinceEpoch,
        ));

        // Update camera position on map
        _mapController?.animateCamera(
          CameraUpdate.newLatLng(
            LatLng(position.latitude, position.longitude),
          ),
        );
      });

      // Listen to signal events
      _signalSub = _gpsService.signalEvents.listen((event) {
        if (event is GpsSignalLostEvent) {
          setState(() => _gpsSignalLost = true);
          if (mounted) {
            ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(
                content: Text('GPS signal lost. Switching to manual mode.'),
                backgroundColor: Colors.orange,
              ),
            );
          }
        } else if (event is GpsSignalRestoredEvent) {
          setState(() => _gpsSignalLost = false);
          if (mounted) {
            ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(
                content: Text('GPS signal restored.'),
                backgroundColor: Colors.green,
              ),
            );
          }
        }
      });

      // Get initial position
      final initialPos = await _gpsService.getCurrentPosition();
      if (initialPos != null && mounted) {
        setState(() => _currentPosition = initialPos);
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('GPS initialisation failed: $e')),
        );
      }
    }
  }

  void _listenToWebSocket() {
    _wsSub = _wsService.events.listen((event) {
      switch (event) {
        case WsLandmarkDetectedEvent(:final landmark):
          _handleLandmarkDetected(landmark);
        case WsDocumentaryContentEvent(:final element):
          ref.read(sessionProvider.notifier).addStreamElement(element);
        case WsDirectionsEvent(:final directions):
          _handleDirections(directions);
        case WsErrorEvent(:final error):
          if (mounted) {
            ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(content: Text('Error: ${error.message}')),
            );
          }
        default:
          break;
      }
    });
  }

  void _handleLandmarkDetected(LandmarkDetected landmark) {
    final landmarkData = landmark.landmark;
    final info = LandmarkInfo(
      placeId: landmarkData['place_id'] as String? ?? '',
      name: landmarkData['name'] as String? ?? 'Unknown',
      latitude: (landmarkData['location']?['latitude'] as num?)?.toDouble() ?? 0.0,
      longitude: (landmarkData['location']?['longitude'] as num?)?.toDouble() ?? 0.0,
      distance: landmark.distance,
      autoTriggered: landmark.autoTrigger,
    );

    setState(() {
      // Add to nearby landmarks if not already present
      if (!_nearbyLandmarks.any((l) => l.placeId == info.placeId)) {
        _nearbyLandmarks.add(info);
      }

      // Add marker to map
      _markers.add(
        Marker(
          markerId: MarkerId(info.placeId),
          position: LatLng(info.latitude, info.longitude),
          infoWindow: InfoWindow(
            title: info.name,
            snippet: '${info.distance.toStringAsFixed(0)}m away',
          ),
          icon: info.autoTriggered
              ? BitmapDescriptor.defaultMarkerWithHue(BitmapDescriptor.hueGreen)
              : BitmapDescriptor.defaultMarkerWithHue(BitmapDescriptor.hueBlue),
          onTap: () => _onLandmarkTap(info),
        ),
      );
    });

    // Show notification for auto-triggered landmarks
    if (landmark.autoTrigger && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('📍 Discovered: ${info.name}'),
          backgroundColor: Colors.green,
          duration: const Duration(seconds: 3),
        ),
      );
    }
  }

  void _handleDirections(DirectionsResponse directions) {
    setState(() {
      _currentDirections = directions;

      // Add polyline to map
      if (directions.polyline.isNotEmpty) {
        _polylines.add(
          Polyline(
            polylineId: const PolylineId('route'),
            points: _decodePolyline(directions.polyline),
            color: Colors.blue,
            width: 5,
          ),
        );
      }
    });
  }

  List<LatLng> _decodePolyline(String encoded) {
    // Simple polyline decoder (Google's encoded polyline format)
    List<LatLng> points = [];
    int index = 0, len = encoded.length;
    int lat = 0, lng = 0;

    while (index < len) {
      int b, shift = 0, result = 0;
      do {
        b = encoded.codeUnitAt(index++) - 63;
        result |= (b & 0x1f) << shift;
        shift += 5;
      } while (b >= 0x20);
      int dlat = ((result & 1) != 0 ? ~(result >> 1) : (result >> 1));
      lat += dlat;

      shift = 0;
      result = 0;
      do {
        b = encoded.codeUnitAt(index++) - 63;
        result |= (b & 0x1f) << shift;
        shift += 5;
      } while (b >= 0x20);
      int dlng = ((result & 1) != 0 ? ~(result >> 1) : (result >> 1));
      lng += dlng;

      points.add(LatLng(lat / 1E5, lng / 1E5));
    }
    return points;
  }

  void _onLandmarkTap(LandmarkInfo landmark) {
    setState(() => _selectedLandmark = landmark);
    // Request directions from backend
    // (Backend would handle this via GPS Walker service)
  }

  void _onMapCreated(GoogleMapController controller) {
    _mapController = controller;
    if (_currentPosition != null) {
      controller.animateCamera(
        CameraUpdate.newLatLngZoom(
          LatLng(_currentPosition!.latitude, _currentPosition!.longitude),
          15.0,
        ),
      );
    }
  }

  @override
  void dispose() {
    _positionSub?.cancel();
    _signalSub?.cancel();
    _wsSub?.cancel();
    _mapController?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Stack(
        children: [
          // Map view
          if (_gpsReady && _currentPosition != null)
            GoogleMap(
              onMapCreated: _onMapCreated,
              initialCameraPosition: CameraPosition(
                target: LatLng(
                  _currentPosition!.latitude,
                  _currentPosition!.longitude,
                ),
                zoom: 15.0,
              ),
              myLocationEnabled: true,
              myLocationButtonEnabled: true,
              markers: _markers,
              polylines: _polylines,
              mapType: MapType.normal,
            )
          else
            Container(
              color: Colors.grey[900],
              child: const Center(
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    CircularProgressIndicator(color: Colors.white),
                    SizedBox(height: 16),
                    Text(
                      'Acquiring GPS signal...',
                      style: TextStyle(color: Colors.white70),
                    ),
                  ],
                ),
              ),
            ),

          // Top bar
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Row(
                children: [
                  IconButton(
                    icon: const Icon(Icons.arrow_back, color: Colors.white),
                    onPressed: () => Navigator.pop(context),
                  ),
                  const Spacer(),
                  ModeSwitcherWidget(currentMode: widget.mode),
                ],
              ),
            ),
          ),

          // GPS signal lost warning
          if (_gpsSignalLost)
            Positioned(
              top: 80,
              left: 16,
              right: 16,
              child: _GpsWarningBanner(),
            ),

          // Nearby landmarks list
          if (_nearbyLandmarks.isNotEmpty)
            Positioned(
              top: _gpsSignalLost ? 140 : 80,
              left: 16,
              right: 16,
              child: _NearbyLandmarksList(
                landmarks: _nearbyLandmarks,
                onTap: _onLandmarkTap,
              ),
            ),

          // Selected landmark details
          if (_selectedLandmark != null)
            Positioned(
              left: 16,
              right: 16,
              bottom: 100,
              child: _LandmarkDetailsCard(
                landmark: _selectedLandmark!,
                directions: _currentDirections,
                onClose: () => setState(() => _selectedLandmark = null),
              ),
            ),

          // Documentary stream at the bottom
          Positioned(
            left: 0,
            right: 0,
            bottom: 0,
            child: DocumentaryStreamWidget(),
          ),
        ],
      ),
    );
  }
}

// ─── Supporting Models ───────────────────────────────────────────────────────

class LandmarkInfo {
  final String placeId;
  final String name;
  final double latitude;
  final double longitude;
  final double distance;
  final bool autoTriggered;

  const LandmarkInfo({
    required this.placeId,
    required this.name,
    required this.latitude,
    required this.longitude,
    required this.distance,
    required this.autoTriggered,
  });
}

// ─── UI Widgets ──────────────────────────────────────────────────────────────

class _GpsWarningBanner extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      decoration: BoxDecoration(
        color: Colors.orange.withAlpha(204),
        borderRadius: BorderRadius.circular(8),
      ),
      child: const Row(
        children: [
          Icon(Icons.gps_off, color: Colors.white),
          SizedBox(width: 8),
          Expanded(
            child: Text(
              'GPS signal lost. Switched to manual mode.',
              style: TextStyle(color: Colors.white, fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }
}

class _NearbyLandmarksList extends StatelessWidget {
  final List<LandmarkInfo> landmarks;
  final Function(LandmarkInfo) onTap;

  const _NearbyLandmarksList({
    required this.landmarks,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      constraints: const BoxConstraints(maxHeight: 120),
      child: ListView.builder(
        scrollDirection: Axis.horizontal,
        itemCount: landmarks.length,
        itemBuilder: (context, index) {
          final landmark = landmarks[index];
          return GestureDetector(
            onTap: () => onTap(landmark),
            child: Container(
              width: 160,
              margin: const EdgeInsets.only(right: 8),
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: Colors.black87,
                borderRadius: BorderRadius.circular(8),
                border: Border.all(
                  color: landmark.autoTriggered
                      ? Colors.greenAccent
                      : Colors.white24,
                  width: 2,
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Row(
                    children: [
                      Icon(
                        landmark.autoTriggered
                            ? Icons.location_on
                            : Icons.location_on_outlined,
                        color: landmark.autoTriggered
                            ? Colors.greenAccent
                            : Colors.blueAccent,
                        size: 16,
                      ),
                      const SizedBox(width: 4),
                      Expanded(
                        child: Text(
                          landmark.name,
                          style: const TextStyle(
                            color: Colors.white,
                            fontSize: 13,
                            fontWeight: FontWeight.bold,
                          ),
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(
                    '${landmark.distance.toStringAsFixed(0)}m away',
                    style: const TextStyle(
                      color: Colors.white70,
                      fontSize: 11,
                    ),
                  ),
                  if (landmark.autoTriggered)
                    const Padding(
                      padding: EdgeInsets.only(top: 4),
                      child: Text(
                        '✓ Auto-triggered',
                        style: TextStyle(
                          color: Colors.greenAccent,
                          fontSize: 10,
                        ),
                      ),
                    ),
                ],
              ),
            ),
          );
        },
      ),
    );
  }
}

class _LandmarkDetailsCard extends StatelessWidget {
  final LandmarkInfo landmark;
  final DirectionsResponse? directions;
  final VoidCallback onClose;

  const _LandmarkDetailsCard({
    required this.landmark,
    required this.directions,
    required this.onClose,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.black87,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.white24),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              const Icon(Icons.location_on, color: Colors.blueAccent, size: 20),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  landmark.name,
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 16,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
              IconButton(
                icon: const Icon(Icons.close, color: Colors.white70),
                onPressed: onClose,
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            '${landmark.distance.toStringAsFixed(0)}m away',
            style: const TextStyle(color: Colors.white70, fontSize: 13),
          ),
          if (directions != null) ...[
            const SizedBox(height: 12),
            const Divider(color: Colors.white24),
            const SizedBox(height: 8),
            Row(
              children: [
                const Icon(Icons.directions_walk, color: Colors.white70, size: 16),
                const SizedBox(width: 8),
                Text(
                  '${(directions!.distanceMeters / 1000).toStringAsFixed(1)} km',
                  style: const TextStyle(color: Colors.white, fontSize: 13),
                ),
                const SizedBox(width: 16),
                const Icon(Icons.access_time, color: Colors.white70, size: 16),
                const SizedBox(width: 8),
                Text(
                  '${(directions!.durationSeconds / 60).toStringAsFixed(0)} min',
                  style: const TextStyle(color: Colors.white, fontSize: 13),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}
