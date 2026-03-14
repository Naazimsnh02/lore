/// VoiceMode screen — microphone input + documentary stream.
///
/// Requirements 1.2, 1.4, 3.1-3.6, 24.3, 24.5, 24.7:
/// - Continuously listen for voice input
/// - Show waveform visualization while listening
/// - Display conversation history (user queries + assistant responses)
/// - Display documentary content stream (narration, illustrations, facts)
/// - Audio playback with controls for narration
/// - Branch depth navigation indicator
/// - Support barge-in (interrupting playback)
/// - Mode switching UI
library;

import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:uuid/uuid.dart';

import '../models/models.dart';
import '../providers/app_providers.dart';
import '../services/audio_playback_service.dart';
import '../services/microphone_service.dart';
import '../services/websocket_service.dart';
import '../widgets/audio_player_controls_widget.dart';
import '../widgets/conversation_history_widget.dart';
import '../widgets/documentary_stream_widget.dart';
import '../widgets/mode_switcher_widget.dart';

class VoiceModeScreen extends ConsumerStatefulWidget {
  const VoiceModeScreen({super.key});

  @override
  ConsumerState<VoiceModeScreen> createState() => _VoiceModeScreenState();
}

class _VoiceModeScreenState extends ConsumerState<VoiceModeScreen>
    with TickerProviderStateMixin {
  late final MicrophoneService _micService;
  late final WebSocketService _wsService;
  late final AudioPlaybackService _audioService;

  StreamSubscription? _audioSub;
  StreamSubscription? _noiseSub;
  StreamSubscription? _wsSub;
  StreamSubscription? _playbackStatusSub;

  late AnimationController _pulseController;
  late AnimationController _waveformController;

  bool _listening = false;
  bool _highNoise = false;
  bool _showStream = false; // Toggle between conversation and stream panel

  static const _uuid = Uuid();

  @override
  void initState() {
    super.initState();
    _micService = ref.read(microphoneServiceProvider);
    _wsService = ref.read(webSocketServiceProvider);
    _audioService = ref.read(audioPlaybackServiceProvider);

    _pulseController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 1),
    )..repeat(reverse: true);

    _waveformController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1500),
    )..repeat();

    _listenToWebSocket();
    _listenToPlaybackStatus();
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
    // Open the persistent Live API session before starting the mic.
    // Mirrors AudioLoop.run() → client.aio.live.connect() from the reference script.
    final session = ref.read(sessionProvider);
    _wsService.send(VoiceSessionStartMessage(
      language: session.language,
      timestamp: DateTime.now().millisecondsSinceEpoch,
    ));
    await _startListening();
  }

  Future<void> _startListening() async {
    await _micService.startRecording();
    setState(() => _listening = true);

    // Stream each PCM chunk to the backend as a voice_chunk message.
    // Mirrors AudioLoop.listen_audio() → out_queue.put({"data": ..., "mime_type": "audio/pcm"})
    _audioSub = _micService.audioChunks.listen((chunk) {
      _wsService.send(VoiceChunkMessage(
        data: chunk.base64Audio,
        timestamp: chunk.timestamp,
      ));
    });

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
          _handleDocumentaryContent(element);
        case WsErrorEvent(:final error):
          if (mounted) {
            ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(content: Text('Error: ${error.message}')),
            );
          }
        case WsRawEvent(:final json):
          _handleRawEvent(json);
        default:
          break;
      }
    });
  }
  /// Handle incoming documentary content — auto-play narration audio and
  /// add assistant messages to the conversation.
  void _handleDocumentaryContent(DocumentaryStreamElement element) {
    if (element.contentType == ContentType.narration) {
      final text = element.content['text'] as String?;
      final audioData = element.content['audioData'] as String?;
      final topic = element.content['topic'] as String?;
      final branchDepth = element.content['branchDepth'] as int? ?? 0;

      // Add narration text to conversation history
      if (text != null && text.isNotEmpty) {
        ref.read(sessionProvider.notifier).addConversationMessage(
              ConversationMessage(
                id: _uuid.v4(),
                role: ConversationRole.assistant,
                text: text,
                timestamp: DateTime.now(),
                topic: topic,
                branchDepth: branchDepth,
              ),
            );
      }

      // Queue narration audio for playback
      if (audioData != null && audioData.isNotEmpty) {
        _audioService.addToQueue(audioData, label: topic);
      }
    } else if (element.contentType == ContentType.fact) {
      final text = element.content['text'] as String?;
      final source = element.content['source'] as String?;
      if (text != null && text.isNotEmpty) {
        final factText = source != null ? '$text\n— $source' : text;
        ref.read(sessionProvider.notifier).addConversationMessage(
              ConversationMessage(
                id: _uuid.v4(),
                role: ConversationRole.assistant,
                text: factText,
                timestamp: DateTime.now(),
                topic: 'Fact',
              ),
            );
      }
    }
  }

  /// Handle raw WebSocket events for transcription, branch updates, and
  /// live_audio PCM from the model's spoken response.
  void _handleRawEvent(Map<String, dynamic> json) {
    final type = json['type'] as String?;

    // live_audio: raw PCM chunk from the model's Live API audio stream.
    // Play each chunk immediately as it arrives — mirrors AudioLoop.play_audio()
    // which reads from audio_in_queue without waiting for turn_complete.
    // When final=true (turn_complete), flush any remaining buffered bytes.
    if (type == 'live_audio') {
      final payload = json['payload'] as Map<String, dynamic>?;
      if (payload != null) {
        final data = payload['data'] as String?;
        final isFinal = payload['final'] as bool? ?? false;
        if (data != null && data.isNotEmpty) {
          _audioService.addLiveChunk(base64Decode(data));
        }
        if (isFinal) {
          _audioService.flushLiveAudio();
        }
      }
      return;
    }

    if (type == 'transcription') {
      final payload = json['payload'] as Map<String, dynamic>?;
      if (payload != null) {
        final text = payload['text'] as String?;
        final topic = payload['topic'] as String?;
        final branchDepth = payload['branchDepth'] as int? ?? 0;
        final role = payload['role'] as String? ?? 'user';
        // partial=true → update the last assistant bubble in place (word-by-word).
        // partial=false or absent → the turn is complete, message is finalised.
        final isPartial = payload['partial'] as bool? ?? false;

        if (text != null && text.isNotEmpty) {
          if (role == 'assistant' && isPartial) {
            // Update the last assistant message in place so the user sees one
            // text box that grows as the model speaks — mirrors the reference
            // script's print(text, end="") behaviour.
            ref.read(sessionProvider.notifier).appendToLastAssistantMessage(
                  text,
                  topic: topic,
                  branchDepth: branchDepth,
                );
          } else if (role == 'assistant' && !isPartial) {
            // Final consolidated text — only add a new message if we haven't
            // been streaming partials (i.e. the last message isn't already this).
            // If partials were streamed, the message is already complete.
            final session = ref.read(sessionProvider);
            final lastMsg = session.conversationHistory.isNotEmpty
                ? session.conversationHistory.last
                : null;
            final alreadyStreamed = lastMsg != null &&
                lastMsg.role == ConversationRole.assistant &&
                lastMsg.text == text;
            if (!alreadyStreamed) {
              ref.read(sessionProvider.notifier).addConversationMessage(
                    ConversationMessage(
                      id: _uuid.v4(),
                      role: ConversationRole.assistant,
                      text: text,
                      timestamp: DateTime.now(),
                      topic: topic,
                      branchDepth: branchDepth,
                    ),
                  );
            }
          } else {
            // User transcript — always a new message
            ref.read(sessionProvider.notifier).addConversationMessage(
                  ConversationMessage(
                    id: _uuid.v4(),
                    role: ConversationRole.user,
                    text: text,
                    timestamp: DateTime.now(),
                    topic: topic,
                    branchDepth: branchDepth,
                  ),
                );
          }
          ref.read(sessionProvider.notifier).setBranchDepth(branchDepth);
        }
      }
    } else if (type == 'branch_update') {
      final payload = json['payload'] as Map<String, dynamic>?;
      if (payload != null) {
        final depth = payload['depth'] as int? ?? 0;
        ref.read(sessionProvider.notifier).setBranchDepth(depth);
      }
    }
  }

  void _listenToPlaybackStatus() {
    _playbackStatusSub = _audioService.status.listen((status) {
      if (!mounted) return;
      final notifier = ref.read(sessionProvider.notifier);
      switch (status) {
        case PlaybackStatus.playing:
          notifier.setNarrationPlaying(true);
        case PlaybackStatus.paused:
        case PlaybackStatus.completed:
        case PlaybackStatus.idle:
        case PlaybackStatus.error:
          notifier.setNarrationPlaying(false);
        case PlaybackStatus.loading:
          break;
      }
    });
  }

  @override
  void dispose() {
    _audioSub?.cancel();
    _noiseSub?.cancel();
    _wsSub?.cancel();
    _playbackStatusSub?.cancel();
    _micService.stopRecording();
    // Close the persistent Live API session when leaving VoiceMode.
    _wsService.send(VoiceSessionEndMessage(
      timestamp: DateTime.now().millisecondsSinceEpoch,
    ));
    _pulseController.dispose();
    _waveformController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final session = ref.watch(sessionProvider);

    return Scaffold(
      backgroundColor: const Color(0xFF0D1B0D),
      body: SafeArea(
        child: Column(
          children: [
            // ── Top bar: back, settings, mode switcher ────────────────
            _TopBar(
              session: session,
              onLanguageChanged: (lang) =>
                  ref.read(sessionProvider.notifier).setLanguage(lang),
              onDepthDialChanged: (dial) =>
                  ref.read(sessionProvider.notifier).setDepthDial(dial),
            ),

            // ── High-noise warning ────────────────────────────────────
            if (_highNoise) const _HighNoiseBanner(),

            // ── Branch depth indicator ────────────────────────────────
            if (session.branchDepth > 0)
              _BranchDepthBar(depth: session.branchDepth),

            // ── Main content area ─────────────────────────────────────
            Expanded(
              child: _showStream
                  ? DocumentaryStreamWidget()
                  : const ConversationHistoryWidget(),
            ),

            // ── Waveform visualization ────────────────────────────────
            _WaveformSection(
              listening: _listening,
              animation: _waveformController,
            ),

            // ── Audio playback controls ───────────────────────────────
            if (session.streamElements.any(
                (e) => e.contentType == ContentType.narration))
              const Padding(
                padding: EdgeInsets.symmetric(horizontal: 16),
                child: AudioPlayerControlsWidget(),
              ),

            // ── Bottom controls bar ───────────────────────────────────
            _BottomControlsBar(
              listening: _listening,
              showStream: _showStream,
              onMicToggle: () async {
                if (_listening) {
                  // Stop streaming chunks first, then signal the backend
                  // to flush VAD — mirrors audioStreamEnd in the reference script.
                  await _micService.stopRecording();
                  _audioSub?.cancel();
                  _audioSub = null;
                  _noiseSub?.cancel();
                  _noiseSub = null;
                  // Send audioStreamEnd signal so the Live API VAD fires
                  // and delivers the input_transcription.
                  _wsService.send(VoiceMicStopMessage(
                    timestamp: DateTime.now().millisecondsSinceEpoch,
                  ));
                  setState(() => _listening = false);
                } else {
                  await _startListening();
                }
              },
              onToggleView: () => setState(() => _showStream = !_showStream),
            ),
          ],
        ),
      ),
    );
  }
}

// ─── Top bar ─────────────────────────────────────────────────────────────────

class _TopBar extends StatelessWidget {
  final SessionState session;
  final ValueChanged<String> onLanguageChanged;
  final ValueChanged<DepthDial> onDepthDialChanged;

  const _TopBar({
    required this.session,
    required this.onLanguageChanged,
    required this.onDepthDialChanged,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      child: Row(
        children: [
          IconButton(
            icon: const Icon(Icons.arrow_back, color: Colors.white),
            onPressed: () => Navigator.pop(context),
          ),
          const Spacer(),
          // Settings button
          IconButton(
            icon: const Icon(Icons.tune, color: Colors.white54, size: 22),
            onPressed: () => _showSettingsSheet(context),
          ),
          const SizedBox(width: 4),
          ModeSwitcherWidget(currentMode: LoreMode.voice),
        ],
      ),
    );
  }

  void _showSettingsSheet(BuildContext context) {
    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF1A2A1A),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (_) => Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'VoiceMode Settings',
              style: TextStyle(
                color: Colors.white,
                fontSize: 18,
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: 20),

            // Language selector
            const Text('Language',
                style: TextStyle(color: Colors.white54, fontSize: 13)),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: _languages.entries.map((entry) {
                final selected = session.language == entry.key;
                return GestureDetector(
                  onTap: () {
                    onLanguageChanged(entry.key);
                    Navigator.pop(context);
                  },
                  child: Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                    decoration: BoxDecoration(
                      color: selected
                          ? Colors.greenAccent.withAlpha(40)
                          : Colors.white.withAlpha(10),
                      borderRadius: BorderRadius.circular(16),
                      border: Border.all(
                        color: selected
                            ? Colors.greenAccent.withAlpha(100)
                            : Colors.white.withAlpha(15),
                      ),
                    ),
                    child: Text(
                      entry.value,
                      style: TextStyle(
                        color: selected ? Colors.greenAccent : Colors.white54,
                        fontSize: 12,
                      ),
                    ),
                  ),
                );
              }).toList(),
            ),

            const SizedBox(height: 20),

            // Depth dial
            const Text('Depth Dial',
                style: TextStyle(color: Colors.white54, fontSize: 13)),
            const SizedBox(height: 8),
            Row(
              children: DepthDial.values.map((dial) {
                final selected = session.depthDial == dial;
                return Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: GestureDetector(
                    onTap: () {
                      onDepthDialChanged(dial);
                      Navigator.pop(context);
                    },
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 14, vertical: 8),
                      decoration: BoxDecoration(
                        color: selected
                            ? Colors.greenAccent.withAlpha(40)
                            : Colors.white.withAlpha(10),
                        borderRadius: BorderRadius.circular(20),
                        border: Border.all(
                          color: selected
                              ? Colors.greenAccent.withAlpha(100)
                              : Colors.white.withAlpha(15),
                        ),
                      ),
                      child: Text(
                        dial.name[0].toUpperCase() + dial.name.substring(1),
                        style: TextStyle(
                          color: selected ? Colors.greenAccent : Colors.white54,
                          fontSize: 13,
                          fontWeight:
                              selected ? FontWeight.bold : FontWeight.normal,
                        ),
                      ),
                    ),
                  ),
                );
              }).toList(),
            ),
            const SizedBox(height: 12),
          ],
        ),
      ),
    );
  }

  static const _languages = {
    'en': 'English',
    'es': 'Spanish',
    'fr': 'French',
    'de': 'German',
    'it': 'Italian',
    'pt': 'Portuguese',
    'ja': 'Japanese',
    'ko': 'Korean',
    'zh': 'Chinese',
    'ar': 'Arabic',
    'hi': 'Hindi',
    'ru': 'Russian',
  };
}

// ─── High-noise banner ───────────────────────────────────────────────────────

class _HighNoiseBanner extends StatelessWidget {
  const _HighNoiseBanner();

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
      decoration: BoxDecoration(
        color: Colors.orange.withAlpha(40),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: Colors.orange.withAlpha(100)),
      ),
      child: const Row(
        children: [
          Icon(Icons.volume_up, color: Colors.orange, size: 16),
          SizedBox(width: 8),
          Expanded(
            child: Text(
              'High ambient noise — applying noise cancellation',
              style: TextStyle(color: Colors.orange, fontSize: 12),
            ),
          ),
        ],
      ),
    );
  }
}

// ─── Branch depth bar ────────────────────────────────────────────────────────

class _BranchDepthBar extends StatelessWidget {
  final int depth;
  const _BranchDepthBar({required this.depth});

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
      decoration: BoxDecoration(
        color: Colors.deepPurpleAccent.withAlpha(25),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: Colors.deepPurpleAccent.withAlpha(60)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.account_tree_outlined,
              color: Colors.deepPurpleAccent, size: 14),
          const SizedBox(width: 6),
          Text(
            'Branch Depth: $depth / 3',
            style: const TextStyle(
              color: Colors.deepPurpleAccent,
              fontSize: 12,
              fontWeight: FontWeight.w500,
            ),
          ),
          const SizedBox(width: 8),
          // Depth dots
          ...List.generate(
            3,
            (i) => Container(
              width: 6,
              height: 6,
              margin: const EdgeInsets.only(right: 3),
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: i < depth
                    ? Colors.deepPurpleAccent
                    : Colors.deepPurpleAccent.withAlpha(40),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ─── Waveform visualization ──────────────────────────────────────────────────

class _WaveformSection extends StatelessWidget {
  final bool listening;
  final AnimationController animation;

  const _WaveformSection({
    required this.listening,
    required this.animation,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 60,
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      decoration: BoxDecoration(
        color: Colors.white.withAlpha(8),
        borderRadius: BorderRadius.circular(12),
      ),
      child: AnimatedBuilder(
        animation: animation,
        builder: (context, _) {
          return CustomPaint(
            size: Size.infinite,
            painter: _WaveformPainter(
              animationValue: animation.value,
              isActive: listening,
            ),
          );
        },
      ),
    );
  }
}

/// Draws an animated waveform using sine waves.
class _WaveformPainter extends CustomPainter {
  final double animationValue;
  final bool isActive;

  const _WaveformPainter({
    required this.animationValue,
    required this.isActive,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final centerY = size.height / 2;
    final barCount = 40;
    final barWidth = size.width / barCount;

    final paint = Paint()
      ..style = PaintingStyle.fill
      ..strokeCap = StrokeCap.round;

    for (int i = 0; i < barCount; i++) {
      final x = i * barWidth + barWidth / 2;
      final normalizedX = i / barCount;

      // Create a wave pattern using multiple sine waves
      final wave1 = math.sin(
              (normalizedX * math.pi * 4) + (animationValue * math.pi * 2)) *
          0.6;
      final wave2 = math.sin(
              (normalizedX * math.pi * 6) + (animationValue * math.pi * 3)) *
          0.3;
      final wave3 = math.sin(
              (normalizedX * math.pi * 2) + (animationValue * math.pi * 1)) *
          0.1;

      final amplitude = isActive ? (wave1 + wave2 + wave3).abs() : 0.05;

      // Scale bar height
      final maxBarHeight = size.height * 0.7;
      final barHeight = math.max(2.0, amplitude * maxBarHeight);

      // Gradient colour from green to greenAccent based on amplitude
      final colorValue = isActive ? amplitude.clamp(0.0, 1.0) : 0.15;
      paint.color = Color.lerp(
        Colors.greenAccent.withAlpha(40),
        Colors.greenAccent,
        colorValue,
      )!;

      final rect = RRect.fromRectAndRadius(
        Rect.fromCenter(
          center: Offset(x, centerY),
          width: barWidth * 0.5,
          height: barHeight,
        ),
        const Radius.circular(2),
      );
      canvas.drawRRect(rect, paint);
    }
  }

  @override
  bool shouldRepaint(_WaveformPainter oldDelegate) =>
      oldDelegate.animationValue != animationValue ||
      oldDelegate.isActive != isActive;
}

// ─── Bottom controls bar ─────────────────────────────────────────────────────

class _BottomControlsBar extends StatelessWidget {
  final bool listening;
  final bool showStream;
  final VoidCallback onMicToggle;
  final VoidCallback onToggleView;

  const _BottomControlsBar({
    required this.listening,
    required this.showStream,
    required this.onMicToggle,
    required this.onToggleView,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceEvenly,
        children: [
          // View toggle: conversation <-> stream
          _ControlButton(
            icon: showStream ? Icons.chat_outlined : Icons.view_stream_outlined,
            label: showStream ? 'Chat' : 'Stream',
            onTap: onToggleView,
          ),

          // Central mic button
          GestureDetector(
            onTap: onMicToggle,
            child: Container(
              width: 64,
              height: 64,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: listening ? Colors.red : Colors.greenAccent,
                boxShadow: [
                  BoxShadow(
                    color: (listening ? Colors.red : Colors.greenAccent)
                        .withAlpha(60),
                    blurRadius: 16,
                    spreadRadius: 2,
                  ),
                ],
              ),
              child: Icon(
                listening ? Icons.stop_rounded : Icons.mic_rounded,
                color: Colors.white,
                size: 30,
              ),
            ),
          ),

          // Placeholder for symmetry — could be used for additional actions
          _ControlButton(
            icon: Icons.more_horiz,
            label: 'More',
            onTap: () {},
          ),
        ],
      ),
    );
  }
}

class _ControlButton extends StatelessWidget {
  final IconData icon;
  final String label;
  final VoidCallback onTap;

  const _ControlButton({
    required this.icon,
    required this.label,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, color: Colors.white54, size: 24),
          const SizedBox(height: 4),
          Text(
            label,
            style: const TextStyle(color: Colors.white38, fontSize: 10),
          ),
        ],
      ),
    );
  }
}
