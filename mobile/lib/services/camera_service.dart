/// Camera capture service for SightMode and LoreMode.
///
/// Requirements 2.1, 24.2:
/// - Capture frames at minimum 1 fps when active
/// - Maintain live camera preview
/// - Detect insufficient lighting and notify the caller
library;

import 'dart:async';
import 'dart:convert';
import 'package:camera/camera.dart';
import 'package:logging/logging.dart';

/// Result of a single frame capture, ready for transmission.
class CapturedFrame {
  /// Base-64-encoded JPEG image data.
  final String base64Image;
  final int timestamp;

  const CapturedFrame({required this.base64Image, required this.timestamp});
}

/// Indicates insufficient ambient lighting (Requirement 2.6).
class LowLightWarning {}

/// Encapsulates camera initialisation, preview, and 1-fps frame capture.
class CameraService {
  static const Duration _captureInterval = Duration(seconds: 1); // 1 fps min

  final _log = Logger('CameraService');
  final _frameController = StreamController<CapturedFrame>.broadcast();
  final _lightingController = StreamController<LowLightWarning>.broadcast();

  CameraController? _controller;
  Timer? _captureTimer;
  bool _isCapturing = false;
  List<CameraDescription> _cameras = [];

  /// Stream of captured frames (base64 JPEG, ~1 fps).
  Stream<CapturedFrame> get frames => _frameController.stream;

  /// Fires a [LowLightWarning] when the camera detects insufficient lighting.
  Stream<LowLightWarning> get lightingWarnings => _lightingController.stream;

  /// The Flutter [CameraController] for embedding a preview widget.
  CameraController? get controller => _controller;

  // ── Lifecycle ────────────────────────────────────────────────────────────

  /// Initialise the camera. Must be awaited before calling [startCapture].
  ///
  /// Selects the rear camera when available, falls back to front camera.
  Future<void> initialize() async {
    _cameras = await availableCameras();
    if (_cameras.isEmpty) {
      throw StateError('No cameras available on this device.');
    }

    // Prefer rear camera (SightMode is primarily used pointing outward)
    final camera = _cameras.firstWhere(
      (c) => c.lensDirection == CameraLensDirection.back,
      orElse: () => _cameras.first,
    );

    _controller = CameraController(
      camera,
      ResolutionPreset.high,
      enableAudio: false, // Audio is handled separately by MicrophoneService
      imageFormatGroup: ImageFormatGroup.jpeg,
    );

    await _controller!.initialize();
    _log.info('Camera initialised: ${camera.name}');
  }

  /// Begin capturing frames at 1 fps and emitting them on [frames].
  void startCapture() {
    if (_isCapturing || _controller == null) return;
    _isCapturing = true;
    _captureTimer = Timer.periodic(_captureInterval, (_) => _captureFrame());
    _log.info('Frame capture started');
  }

  /// Stop frame capture (camera preview remains active).
  void stopCapture() {
    _isCapturing = false;
    _captureTimer?.cancel();
    _captureTimer = null;
    _log.info('Frame capture stopped');
  }

  /// Release all camera resources.
  Future<void> dispose() async {
    stopCapture();
    await _controller?.dispose();
    _controller = null;
    await _frameController.close();
    await _lightingController.close();
  }

  // ── Internal ─────────────────────────────────────────────────────────────

  Future<void> _captureFrame() async {
    if (_controller == null || !_controller!.value.isInitialized) return;

    try {
      final file = await _controller!.takePicture();
      final bytes = await file.readAsBytes();

      // Basic luminance check — if the image is very dark, warn the user.
      if (_isLowLight(bytes)) {
        _lightingController.add(LowLightWarning());
      }

      final base64 = base64Encode(bytes);
      _frameController.add(CapturedFrame(
        base64Image: base64,
        timestamp: DateTime.now().millisecondsSinceEpoch,
      ));
    } catch (e) {
      _log.warning('Frame capture failed: $e');
    }
  }

  /// Very lightweight luminance heuristic based on average byte value.
  ///
  /// A JPEG with an average byte value below ~30 is likely very dark.
  /// This is intentionally approximate — proper luminance checking would
  /// decode the image, which is too expensive at 1 fps on a mobile device.
  bool _isLowLight(List<int> jpegBytes) {
    if (jpegBytes.isEmpty) return false;
    // Sample at most 1000 bytes spread across the file
    final step = (jpegBytes.length / 1000).ceil().clamp(1, jpegBytes.length);
    int sum = 0;
    int count = 0;
    for (int i = 0; i < jpegBytes.length; i += step) {
      sum += jpegBytes[i];
      count++;
    }
    return count > 0 && (sum / count) < 30;
  }
}
