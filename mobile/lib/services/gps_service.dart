/// GPS location monitoring service for GPS Walking Tour mode.
///
/// Requirements 9.1, 9.6, 24.4:
/// - Continuously track device location with < 10 m accuracy
/// - Emit location updates for the backend GPS Walker
/// - Handle GPS signal loss gracefully (Requirement 9.7)
library;

import 'dart:async';
import 'package:geolocator/geolocator.dart';
import 'package:logging/logging.dart';

/// Fired when the GPS signal is lost.
class GpsSignalLostEvent {}

/// Fired when the GPS signal is restored.
class GpsSignalRestoredEvent {}

/// Wraps geolocator to expose a stream of positions and signal-loss events.
class GpsService {
  final _log = Logger('GpsService');

  final _positionController = StreamController<Position>.broadcast();
  final _signalController = StreamController<Object>.broadcast();

  StreamSubscription<Position>? _positionSub;
  bool _signalLost = false;

  /// Stream of GPS position updates (~10 m accuracy, up to 1 Hz).
  Stream<Position> get positions => _positionController.stream;

  /// Stream of [GpsSignalLostEvent] / [GpsSignalRestoredEvent].
  Stream<Object> get signalEvents => _signalController.stream;

  // ── Lifecycle ────────────────────────────────────────────────────────────

  /// Request permission and begin streaming positions.
  ///
  /// Returns `false` if location permission is denied or GPS is disabled.
  Future<bool> startMonitoring() async {
    final permission = await _requestPermission();
    if (!permission) return false;

    const settings = LocationSettings(
      accuracy: LocationAccuracy.high, // < 10 m on good hardware
      distanceFilter: 5, // emit when moved ≥ 5 m
    );

    _positionSub = Geolocator.getPositionStream(locationSettings: settings)
        .listen(
      _onPosition,
      onError: _onError,
      cancelOnError: false,
    );
    _log.info('GPS monitoring started');
    return true;
  }

  /// Stop position streaming.
  Future<void> stopMonitoring() async {
    await _positionSub?.cancel();
    _positionSub = null;
    _log.info('GPS monitoring stopped');
  }

  /// Return the latest known position without waiting for a stream update.
  Future<Position?> getCurrentPosition() async {
    try {
      return await Geolocator.getCurrentPosition(
        locationSettings: const LocationSettings(
          accuracy: LocationAccuracy.high,
        ),
      );
    } catch (e) {
      _log.warning('Failed to get current position: $e');
      return null;
    }
  }

  /// Release all resources.
  Future<void> dispose() async {
    await stopMonitoring();
    await _positionController.close();
    await _signalController.close();
  }

  // ── Internal ─────────────────────────────────────────────────────────────

  void _onPosition(Position position) {
    if (_signalLost) {
      _signalLost = false;
      _signalController.add(GpsSignalRestoredEvent());
      _log.info('GPS signal restored');
    }
    _positionController.add(position);
  }

  void _onError(Object error, StackTrace stack) {
    _log.warning('GPS error: $error');
    if (!_signalLost) {
      _signalLost = true;
      _signalController.add(GpsSignalLostEvent());
    }
  }

  Future<bool> _requestPermission() async {
    bool serviceEnabled = await Geolocator.isLocationServiceEnabled();
    if (!serviceEnabled) {
      _log.warning('Location services are disabled');
      return false;
    }

    LocationPermission permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
    }

    if (permission == LocationPermission.denied ||
        permission == LocationPermission.deniedForever) {
      _log.warning('Location permission denied: $permission');
      return false;
    }

    return true;
  }
}
