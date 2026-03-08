/// LoreMode screen — simultaneous camera + voice input.
///
/// Requirements 4.1–4.6, 24.2, 24.3, 24.4:
/// - Process camera frames and voice audio concurrently
/// - Attach GPS coordinates to camera frames
/// - Enable Alternate History Engine on the server side
library;

import 'dart:async';
import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:geolocator/geolocator.dart';
import 'package:permission_handler/permission_handler.dart';

import '../models/models.dart';
import '../providers/app_providers.dart';
import '../services/camera_service.dart';
import '../services/gps_service.dart';
import '../services/microphone_service.dart';
import '../services/websocket_service.dart';
import '../widgets/documentary_stream_widget.dart';
import '../widgets/mode_switcher_widget.dart';

class LoreModeScreen extends ConsumerStatefulWidget {
  const LoreModeScreen({super.key});

  @override
  ConsumerState<LoreModeScreen> createState() => _LoreModeScreenState();
}

class _LoreModeScreenState extends ConsumerState<LoreModeScreen> {
  late final CameraService _cameraService;
  late final MicrophoneService _micService;
  late final GpsService _gpsService;
  late final WebSocketService _wsService;

  StreamSubscription? _frameSub;
  StreamSubscription? _audioSub;
  StreamSubscription? _gpsSub;
  StreamSubscription? _gpsSignalSub;
  StreamSubscription? _wsSub;

  bool _cameraReady = false;
  Position? _lastPosition;
  bool _gpsLost = false;

  @override
  void initState() {
    super.initState();
    _cameraService = ref.read(cameraServiceProvider);
    _micService = ref.read(microphoneServiceProvider);
    _gpsService = ref.read(gpsServiceProvider);
    _wsService = ref.read(webSocketServiceProvider);

    _initAll();
    _listenToWebSocket();
  }

  Future<void> _initAll() async {
    await Future.wait([
      _initCamera(),
      _initMicrophone(),
      _initGps(),
    ]);
  }

  Future<void> _initCamera() async {
    final status = await Permission.camera.request();
    if (!status.isGranted) return;

    try {
      await _cameraService.initialize();
      _cameraService.startCapture();
      setState(() => _cameraReady = true);

      _frameSub = _cameraService.frames.listen((frame) {
        _wsService.send(CameraFrameMessage(
          imageData: frame.base64Image,
          timestamp: frame.timestamp,
          latitude: _lastPosition?.latitude,
          longitude: _lastPosition?.longitude,
        ));
      });
    } catch (_) {}
  }

  Future<void> _initMicrophone() async {
    final status = await Permission.microphone.request();
    if (!status.isGranted) return;

    await _micService.startRecording();
    _audioSub = _micService.audioChunks.listen((chunk) {
      _wsService.send(VoiceInputMessage(
        audioData: chunk.base64Audio,
        sampleRate: chunk.sampleRate,
        timestamp: chunk.timestamp,
      ));
    });
  }

  Future<void> _initGps() async {
    final started = await _gpsService.startMonitoring();
    if (!started) return;

    _gpsSub = _gpsService.positions.listen((pos) {
      _lastPosition = pos;
      _wsService.send(GpsUpdateMessage(
        latitude: pos.latitude,
        longitude: pos.longitude,
        accuracy: pos.accuracy,
        timestamp: DateTime.now().millisecondsSinceEpoch,
      ));
    });

    _gpsSignalSub = _gpsService.signalEvents.listen((event) {
      if (mounted) {
        setState(() => _gpsLost = event is GpsSignalLostEvent);
      }
    });
  }

  void _listenToWebSocket() {
    _wsSub = _wsService.events.listen((event) {
      switch (event) {
        case WsDocumentaryContentEvent(:final element):
          ref.read(sessionProvider.notifier).addStreamElement(element);
        case WsLandmarkDetectedEvent(:final landmark):
          if (mounted) {
            ScaffoldMessenger.of(context).showSnackBar(SnackBar(
              content: Text(
                  'Landmark detected: ${landmark.landmark['name']} (${landmark.distance.toStringAsFixed(0)} m away)'),
            ));
          }
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

  @override
  void dispose() {
    _frameSub?.cancel();
    _audioSub?.cancel();
    _gpsSub?.cancel();
    _gpsSignalSub?.cancel();
    _wsSub?.cancel();
    _cameraService.stopCapture();
    _micService.stopRecording();
    _gpsService.stopMonitoring();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(
        children: [
          // Camera preview
          if (_cameraReady && _cameraService.controller != null)
            Positioned.fill(
              child: CameraPreview(_cameraService.controller!),
            )
          else
            const Center(child: CircularProgressIndicator(color: Colors.white)),

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
                  ModeSwitcherWidget(currentMode: LoreMode.lore),
                ],
              ),
            ),
          ),

          // GPS lost banner
          if (_gpsLost)
            Positioned(
              top: 80,
              left: 16,
              right: 16,
              child: Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: Colors.red.withAlpha(204),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: const Text(
                  'GPS signal lost — describe your location verbally.',
                  style: TextStyle(color: Colors.white),
                ),
              ),
            ),

          // Active-input indicators (mic + camera)
          Positioned(
            top: 80,
            right: 16,
            child: Column(
              children: [
                _InputIndicator(icon: Icons.mic, label: 'MIC', active: true),
                const SizedBox(height: 8),
                _InputIndicator(
                    icon: Icons.camera_alt,
                    label: 'CAM',
                    active: _cameraReady),
                const SizedBox(height: 8),
                _InputIndicator(
                    icon: Icons.gps_fixed,
                    label: 'GPS',
                    active: !_gpsLost && _lastPosition != null),
              ],
            ),
          ),

          // Documentary stream
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

class _InputIndicator extends StatelessWidget {
  final IconData icon;
  final String label;
  final bool active;

  const _InputIndicator({
    required this.icon,
    required this.label,
    required this.active,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: active ? Colors.green.withAlpha(179) : Colors.black54,
        borderRadius: BorderRadius.circular(6),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, color: Colors.white, size: 14),
          const SizedBox(width: 4),
          Text(label,
              style: const TextStyle(color: Colors.white, fontSize: 11)),
        ],
      ),
    );
  }
}
