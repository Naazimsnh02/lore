/// SightMode screen — camera preview + documentary stream.
///
/// Requirements 2.1–2.6, 24.2:
/// - Show live camera preview
/// - Capture frames at 1 fps and send to backend
/// - Display recognised location and documentary content
/// - Warn user about low light conditions
library;

import 'dart:async';
import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:permission_handler/permission_handler.dart';

import '../models/models.dart';
import '../providers/app_providers.dart';
import '../services/camera_service.dart';
import '../services/websocket_service.dart';
import '../widgets/documentary_stream_widget.dart';
import '../widgets/mode_switcher_widget.dart';

class SightModeScreen extends ConsumerStatefulWidget {
  const SightModeScreen({super.key});

  @override
  ConsumerState<SightModeScreen> createState() => _SightModeScreenState();
}

class _SightModeScreenState extends ConsumerState<SightModeScreen> {
  late final CameraService _cameraService;
  late final WebSocketService _wsService;

  StreamSubscription? _frameSub;
  StreamSubscription? _lightingSub;
  StreamSubscription? _wsSub;

  bool _cameraReady = false;
  bool _lowLight = false;
  String? _recognisedLocation;

  @override
  void initState() {
    super.initState();
    _cameraService = ref.read(cameraServiceProvider);
    _wsService = ref.read(webSocketServiceProvider);
    _initCamera();
    _listenToWebSocket();
  }

  Future<void> _initCamera() async {
    final status = await Permission.camera.request();
    if (!status.isGranted) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Camera permission is required for SightMode.')),
        );
      }
      return;
    }

    try {
      await _cameraService.initialize();
      _cameraService.startCapture();
      setState(() => _cameraReady = true);

      // Forward captured frames to the WebSocket
      _frameSub = _cameraService.frames.listen((frame) {
        _wsService.send(CameraFrameMessage(
          imageData: frame.base64Image,
          timestamp: frame.timestamp,
        ));

        // Send GPS alongside the frame if available
        // (GPS integration wired in LoreMode; here we send without GPS)
      });

      // Listen for low-light warnings
      _lightingSub = _cameraService.lightingWarnings.listen((_) {
        if (mounted) setState(() => _lowLight = true);
        Future.delayed(const Duration(seconds: 5), () {
          if (mounted) setState(() => _lowLight = false);
        });
      });
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Camera initialisation failed: $e')),
        );
      }
    }
  }

  void _listenToWebSocket() {
    _wsSub = _wsService.events.listen((event) {
      switch (event) {
        case WsLocationRecognizedEvent(:final location):
          if (mounted) {
            setState(() =>
                _recognisedLocation = location.place['name'] as String? ?? 'Unknown');
          }
        case WsDocumentaryContentEvent(:final element):
          ref.read(sessionProvider.notifier).addStreamElement(element);
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
    _lightingSub?.cancel();
    _wsSub?.cancel();
    _cameraService.stopCapture();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(
        children: [
          // Camera preview fills the entire screen
          if (_cameraReady && _cameraService.controller != null)
            Positioned.fill(
              child: CameraPreview(_cameraService.controller!),
            )
          else
            const Center(child: CircularProgressIndicator(color: Colors.white)),

          // Top bar with back button and mode switcher
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
                  ModeSwitcherWidget(currentMode: LoreMode.sight),
                ],
              ),
            ),
          ),

          // Low-light warning banner
          if (_lowLight)
            Positioned(
              top: 80,
              left: 16,
              right: 16,
              child: _LowLightBanner(),
            ),

          // Recognised location chip
          if (_recognisedLocation != null)
            Positioned(
              top: _lowLight ? 140 : 80,
              left: 16,
              right: 16,
              child: _LocationChip(name: _recognisedLocation!),
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

class _LowLightBanner extends StatelessWidget {
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
          Icon(Icons.wb_sunny_outlined, color: Colors.white),
          SizedBox(width: 8),
          Expanded(
            child: Text(
              'Low lighting detected — consider enabling flash.',
              style: TextStyle(color: Colors.white, fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }
}

class _LocationChip extends StatelessWidget {
  final String name;
  const _LocationChip({required this.name});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      decoration: BoxDecoration(
        color: Colors.black87,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: Colors.white24),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.location_on, color: Colors.blueAccent, size: 16),
          const SizedBox(width: 8),
          Text(
            name,
            style: const TextStyle(color: Colors.white, fontSize: 14),
          ),
        ],
      ),
    );
  }
}
