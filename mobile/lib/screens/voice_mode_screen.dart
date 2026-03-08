/// VoiceMode screen — microphone input + documentary stream.
///
/// Requirements 3.1–3.6, 24.3:
/// - Continuously listen for voice input
/// - Show waveform / listening indicator
/// - Display documentary content stream
/// - Support barge-in (interrupting playback)
library;

import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:permission_handler/permission_handler.dart';

import '../models/models.dart';
import '../providers/app_providers.dart';
import '../services/microphone_service.dart';
import '../services/websocket_service.dart';
import '../widgets/documentary_stream_widget.dart';
import '../widgets/mode_switcher_widget.dart';

class VoiceModeScreen extends ConsumerStatefulWidget {
  const VoiceModeScreen({super.key});

  @override
  ConsumerState<VoiceModeScreen> createState() => _VoiceModeScreenState();
}

class _VoiceModeScreenState extends ConsumerState<VoiceModeScreen>
    with SingleTickerProviderStateMixin {
  late final MicrophoneService _micService;
  late final WebSocketService _wsService;

  StreamSubscription? _audioSub;
  StreamSubscription? _noiseSub;
  StreamSubscription? _wsSub;
  late AnimationController _pulseController;

  bool _listening = false;
  bool _highNoise = false;

  @override
  void initState() {
    super.initState();
    _micService = ref.read(microphoneServiceProvider);
    _wsService = ref.read(webSocketServiceProvider);

    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 1),
    )..repeat(reverse: true);

    _listenToWebSocket();
    _requestMicAndStart();
  }

  Future<void> _requestMicAndStart() async {
    final status = await Permission.microphone.request();
    if (!status.isGranted) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
              content: Text('Microphone permission is required for VoiceMode.')),
        );
      }
      return;
    }
    await _startListening();
  }

  Future<void> _startListening() async {
    await _micService.startRecording();
    setState(() => _listening = true);

    // Forward audio chunks to the WebSocket
    _audioSub = _micService.audioChunks.listen((chunk) {
      _wsService.send(VoiceInputMessage(
        audioData: chunk.base64Audio,
        sampleRate: chunk.sampleRate,
        timestamp: chunk.timestamp,
      ));
    });

    // High-noise warning
    _noiseSub = _micService.noiseWarnings.listen((w) {
      if (mounted) setState(() => _highNoise = true);
      Future.delayed(const Duration(seconds: 3), () {
        if (mounted) setState(() => _highNoise = false);
      });
    });
  }

  void _listenToWebSocket() {
    _wsSub = _wsService.events.listen((event) {
      switch (event) {
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
    _audioSub?.cancel();
    _noiseSub?.cancel();
    _wsSub?.cancel();
    _micService.stopRecording();
    _pulseController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0D1B0D),
      body: SafeArea(
        child: Column(
          children: [
            // Top bar
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Row(
                children: [
                  IconButton(
                    icon: const Icon(Icons.arrow_back, color: Colors.white),
                    onPressed: () => Navigator.pop(context),
                  ),
                  const Spacer(),
                  ModeSwitcherWidget(currentMode: LoreMode.voice),
                ],
              ),
            ),

            // Central listening indicator
            Expanded(
              child: Center(
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    AnimatedBuilder(
                      animation: _pulseController,
                      builder: (context, child) {
                        final scale = _listening
                            ? 1.0 + _pulseController.value * 0.3
                            : 1.0;
                        return Transform.scale(
                          scale: scale,
                          child: Container(
                            width: 100,
                            height: 100,
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              color: _listening
                                  ? Colors.green.withAlpha(51)
                                  : Colors.grey.withAlpha(51),
                              border: Border.all(
                                color: _listening
                                    ? Colors.greenAccent
                                    : Colors.grey,
                                width: 3,
                              ),
                            ),
                            child: Icon(
                              _listening ? Icons.mic : Icons.mic_off,
                              color: _listening
                                  ? Colors.greenAccent
                                  : Colors.grey,
                              size: 48,
                            ),
                          ),
                        );
                      },
                    ),
                    const SizedBox(height: 24),
                    Text(
                      _listening ? 'Listening…' : 'Tap to start',
                      style: const TextStyle(
                        color: Colors.white70,
                        fontSize: 18,
                        letterSpacing: 1,
                      ),
                    ),
                    if (_highNoise) ...[
                      const SizedBox(height: 12),
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 16, vertical: 8),
                        decoration: BoxDecoration(
                          color: Colors.orange.withAlpha(51),
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(color: Colors.orange),
                        ),
                        child: const Text(
                          'High ambient noise — applying noise cancellation',
                          style:
                              TextStyle(color: Colors.orange, fontSize: 12),
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ),

            // Documentary stream panel
            DocumentaryStreamWidget(),
          ],
        ),
      ),
      floatingActionButton: FloatingActionButton(
        backgroundColor: _listening ? Colors.red : Colors.greenAccent,
        onPressed: () async {
          if (_listening) {
            await _micService.stopRecording();
            _audioSub?.cancel();
            setState(() => _listening = false);
          } else {
            await _startListening();
          }
        },
        child: Icon(
          _listening ? Icons.stop : Icons.mic,
          color: Colors.white,
        ),
      ),
    );
  }
}
