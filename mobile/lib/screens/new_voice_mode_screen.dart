/// New VoiceMode screen — connects directly to the Gemini Live API proxy.
///
/// Architecture (mirrors the official Google demo):
///   Flutter mic → PCM 16kHz → proxy server → Gemini Live API
///   Gemini Live API → PCM 24kHz → proxy server → Flutter → FlutterPcmSound
///
/// Audio playback uses flutter_pcm_sound for gapless real-time PCM streaming,
/// matching the Web Audio API worklet approach in the official JS demo.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_pcm_sound/flutter_pcm_sound.dart';
import 'package:http/http.dart' as http;
import 'package:permission_handler/permission_handler.dart';
import 'package:record/record.dart';
import 'package:video_player/video_player.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

// ── Config ────────────────────────────────────────────────────────────────────

/// URL of the Gemini Live API proxy server.
/// If not explicitly set, derived from WEBSOCKET_GATEWAY_URL by replacing the
/// port with 8090 — so physical devices work with a single --dart-define.
const String _kExplicitProxyUrl = String.fromEnvironment(
  'GEMINI_PROXY_URL',
  defaultValue: '',
);
const String _kGatewayUrl = String.fromEnvironment(
  'WEBSOCKET_GATEWAY_URL',
  defaultValue: '',
);

String get _kDefaultProxyUrl {
  // Explicit override wins
  if (_kExplicitProxyUrl.isNotEmpty) return _kExplicitProxyUrl;
  // Derive from gateway URL: same host, port 8090
  if (_kGatewayUrl.isNotEmpty) {
    try {
      final uri = Uri.parse(_kGatewayUrl);
      return uri.replace(port: 8090, path: '').toString();
    } catch (_) {}
  }
  // Fallback: Android emulator loopback
  return 'ws://10.0.2.2:8090';
}

/// GCP project ID (required for Vertex AI mode).
const String _kProjectId = String.fromEnvironment(
  'GCP_PROJECT_ID',
  defaultValue: '',
);

/// Gemini Live model to use.
const String _kModel = 'gemini-2.5-flash-native-audio-preview-12-2025';

/// Vertex AI model URI (used when GCP_PROJECT_ID is set).
String get _modelUri {
  if (_kProjectId.isNotEmpty) {
    return 'projects/$_kProjectId/locations/us-central1/publishers/google/models/$_kModel';
  }
  return 'models/$_kModel';
}

// ── Message types ─────────────────────────────────────────────────────────────

/// Parsed response from the Gemini Live API.
enum _GeminiMsgType {
  setupComplete,
  audio,
  inputTranscription,
  outputTranscription,
  toolCall,
  turnComplete,
  interrupted,
  unknown,
}

class _GeminiMsg {
  final _GeminiMsgType type;
  final String? audioBase64;
  final String? text;
  final bool? textFinished;
  final String? role; // 'input' or 'output'

  const _GeminiMsg({
    required this.type,
    this.audioBase64,
    this.text,
    this.textFinished,
    this.role,
  });

  factory _GeminiMsg.parse(Map<String, dynamic> data) {
    try {
      if (data.containsKey('setupComplete')) {
        return const _GeminiMsg(type: _GeminiMsgType.setupComplete);
      }

      final sc = data['serverContent'] as Map<String, dynamic>?;
      if (sc != null) {
        if (sc['turnComplete'] == true) {
          return const _GeminiMsg(type: _GeminiMsgType.turnComplete);
        }
        if (sc['interrupted'] == true) {
          return const _GeminiMsg(type: _GeminiMsgType.interrupted);
        }

        // Input transcription (user speech)
        final inTrans = sc['inputTranscription'] as Map<String, dynamic>?;
        if (inTrans != null) {
          return _GeminiMsg(
            type: _GeminiMsgType.inputTranscription,
            text: inTrans['text'] as String? ?? '',
            textFinished: inTrans['finished'] as bool? ?? false,
            role: 'input',
          );
        }

        // Output transcription (model speech)
        final outTrans = sc['outputTranscription'] as Map<String, dynamic>?;
        if (outTrans != null) {
          return _GeminiMsg(
            type: _GeminiMsgType.outputTranscription,
            text: outTrans['text'] as String? ?? '',
            textFinished: outTrans['finished'] as bool? ?? false,
            role: 'output',
          );
        }

        // Audio — try modelTurn.parts first, then top-level parts
        // (native audio model may use either structure)
        List<dynamic>? parts;
        final modelTurn = sc['modelTurn'] as Map<String, dynamic>?;
        if (modelTurn != null) {
          parts = modelTurn['parts'] as List<dynamic>?;
        }
        // Fallback: some responses have parts directly under serverContent
        parts ??= sc['parts'] as List<dynamic>?;

        if (parts != null) {
          for (final part in parts) {
            final p = part as Map<String, dynamic>;
            final inlineData = p['inlineData'] as Map<String, dynamic>?;
            if (inlineData != null) {
              final audioData = inlineData['data'] as String?;
              if (audioData != null && audioData.isNotEmpty) {
                return _GeminiMsg(
                  type: _GeminiMsgType.audio,
                  audioBase64: audioData,
                );
              }
            }
            // Also check for text parts (transcription fallback)
            final textPart = p['text'] as String?;
            if (textPart != null && textPart.isNotEmpty) {
              return _GeminiMsg(
                type: _GeminiMsgType.outputTranscription,
                text: textPart,
                textFinished: false,
                role: 'output',
              );
            }
          }
        }
      }

      // Tool call
      if (data.containsKey('toolCall')) {
        return const _GeminiMsg(type: _GeminiMsgType.toolCall);
      }
    } catch (_) {}
    return const _GeminiMsg(type: _GeminiMsgType.unknown);
  }
}

// ── Chat message model ────────────────────────────────────────────────────────

class _ChatMsg {
  final String id;
  final bool isUser;
  String text;
  // For image tool results
  Uint8List? imageBytes;
  String? imageMime;
  // For video tool results
  String? videoUrl;

  _ChatMsg({
    required this.id,
    required this.isUser,
    required this.text,
    this.imageBytes,
    this.imageMime,
    this.videoUrl,
  });
}

// ── Screen ────────────────────────────────────────────────────────────────────

class NewVoiceModeScreen extends ConsumerStatefulWidget {
  const NewVoiceModeScreen({super.key});

  @override
  ConsumerState<NewVoiceModeScreen> createState() => _NewVoiceModeScreenState();
}

class _NewVoiceModeScreenState extends ConsumerState<NewVoiceModeScreen>
    with TickerProviderStateMixin {
  // Connection state
  WebSocketChannel? _ws;
  StreamSubscription? _wsSub;
  bool _connected = false;
  bool _connecting = false;
  bool _disposed = false; // guard for all async setState calls

  // Proxy URL — editable at runtime so physical devices can set the LAN IP
  late TextEditingController _urlCtrl;

  // Audio recording
  final AudioRecorder _recorder = AudioRecorder();
  bool _recording = false;
  StreamSubscription? _recordSub;

  // Audio playback via flutter_pcm_sound (gapless PCM streaming)
  bool _pcmReady = false;
  bool _playing = false;
  // Serialized feed queue — prevents concurrent platform channel calls
  // which cause the backing-up lag on long responses.
  final List<Uint8List> _feedQueue = [];
  bool _feeding = false;

  // Chat
  final List<_ChatMsg> _messages = [];
  final ScrollController _scrollCtrl = ScrollController();
  // Track whether the last user/assistant bubble is finalized
  bool _lastUserMsgFinished = true;
  bool _lastAssistantMsgFinished = true;

  // Animation
  late AnimationController _waveCtrl;
  late AnimationController _pulseCtrl;

  // Status
  String _status = 'Tap Connect to start';

  @override
  void initState() {
    super.initState();
    _urlCtrl = TextEditingController(text: _kDefaultProxyUrl);
    _waveCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1500),
    )..repeat();
    _pulseCtrl = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 1),
    )..repeat(reverse: true);

    _initPcm();
  }

  Future<void> _initPcm() async {
    try {
      await FlutterPcmSound.setup(sampleRate: 24000, channelCount: 1);
      await FlutterPcmSound.setLogLevel(LogLevel.none);
      FlutterPcmSound.setFeedThreshold(960);
      FlutterPcmSound.setFeedCallback((_) {});
      _pcmReady = true;
    } catch (_) {}
  }

  @override
  void dispose() {
    _disposed = true;
    _disconnectCleanup();
    _urlCtrl.dispose();
    _waveCtrl.dispose();
    _pulseCtrl.dispose();
    FlutterPcmSound.release();
    _scrollCtrl.dispose();
    super.dispose();
  }

  // ── Connection ──────────────────────────────────────────────────────────────

  Future<void> _connect() async {
    if (_disposed || _connecting || _connected) return;
    if (mounted) {
      setState(() {
        _connecting = true;
        _status = 'Connecting...';
      });
    }

    try {
      final ws = WebSocketChannel.connect(Uri.parse(_urlCtrl.text.trim()));
      await ws.ready;
      if (_disposed) {
        ws.sink.close();
        return;
      }

      _ws = ws;

      // Step 1: Send proxy setup — proxy resolves service_url from its own config
      _ws!.sink.add(json.encode({'service_url': ''}));

      // Step 2: Subscribe to messages
      _wsSub = _ws!.stream.listen(
        _onMessage,
        onError: (e) {
          _setStatus('Connection error: $e');
          _disconnectCleanup();
        },
        onDone: () {
          _setStatus('Disconnected');
          if (mounted && !_disposed) setState(() => _connected = false);
        },
      );

      // Step 3: Send Gemini session setup
      _sendSetup();

      if (mounted && !_disposed) {
        setState(() {
          _connected = true;
          _connecting = false;
          _status = 'Connected — waiting for setup...';
        });
      }
    } catch (e) {
      if (mounted && !_disposed) {
        setState(() {
          _connecting = false;
          _status = 'Failed to connect: $e';
        });
      }
    }
  }

  void _sendSetup() {
    final setup = {
      'setup': {
        'model': _modelUri,
        'generation_config': {
          'response_modalities': ['AUDIO'],
          'speech_config': {
            'voice_config': {
              'prebuilt_voice_config': {'voice_name': 'Aoede'},
            },
            'language_code': 'en-US',
          },
          'thinking_config': {'include_thoughts': false, 'thinking_budget': 0},
        },
        'system_instruction': {
          'parts': [
            {
              'text':
                  'You are LORE — an immersive AI documentary narrator. '
                  'LORE turns the world into a living documentary. '
                  'Users speak any topic — a landmark, historical event, scientific concept, '
                  'culture, nature, architecture — and you deliver rich, cinematic documentary '
                  'narration as if they are watching a high-quality BBC or National Geographic film. '
                  'Be authoritative, vivid, and engaging. Use evocative language. '
                  'Build narrative momentum — open with a compelling hook, develop the story, '
                  'and leave the listener wanting more. '
                  'Always respond in English regardless of the language spoken to you. '
                  '\n\n'
                  'TOOL USE RULES — follow these exactly:\n'
                  '1. generate_image: You MUST call this function whenever the user says '
                  '"show", "image", "picture", "draw", "illustrate", "what does it look like", '
                  'or any similar visual request. Do NOT just describe — CALL THE FUNCTION.\n'
                  '2. generate_video: You MUST call this function whenever the user says '
                  '"video", "animate", "motion", "footage", "clip", "bring it to life", '
                  '"show me a video", or any similar motion request. '
                  'Before calling, say out loud: "Generating your video now — this takes about 60 to 90 seconds." '
                  'Then CALL THE FUNCTION immediately.\n\n'
                  'CRITICAL: When a tool is needed, call it — do not just narrate instead. '
                  'Do NOT output <think>, <thinking>, or <tool_use> tags.',
            },
          ],
        },
        'tools': [
          {
            'function_declarations': [
              {
                'name': 'generate_image',
                'description':
                    'Generates a documentary-style illustration. '
                    'Call when the user asks to see, show, draw, or visualise something, '
                    'or when a still image would enhance the narration.',
                'parameters': {
                  'type': 'object',
                  'properties': {
                    'prompt': {
                      'type': 'string',
                      'description':
                          'Detailed image generation prompt. Include subject, style '
                          '(photorealistic / historical painting / illustrated), '
                          'lighting, and mood.',
                    },
                  },
                  'required': ['prompt'],
                },
              },
              {
                'name': 'generate_video',
                'description':
                    'Generates a short cinematic video clip (8 seconds). '
                    'Call when the user asks for a video, animation, or wants to see '
                    'something in motion. Takes 60-90 seconds to generate.',
                'parameters': {
                  'type': 'object',
                  'properties': {
                    'prompt': {
                      'type': 'string',
                      'description':
                          'Detailed video generation prompt. Include subject, camera movement '
                          '(aerial pan, slow zoom, tracking shot), lighting, and documentary style.',
                    },
                  },
                  'required': ['prompt'],
                },
              },
            ],
          },
        ],

        'input_audio_transcription': {},
        'output_audio_transcription': {},
        'realtime_input_config': {
          'automatic_activity_detection': {
            'disabled': false,
            'silence_duration_ms': 1000,
            'prefix_padding_ms': 500,
          },
          'activity_handling': 'START_OF_ACTIVITY_INTERRUPTS',
        },
      },
    };
    _wsSend(setup);
  }

  void _disconnect() {
    _stopRecording();
    _recordSub?.cancel();
    _wsSub?.cancel();
    _ws?.sink.close();
    _ws = null;
    if (mounted) {
      setState(() {
        _connected = false;
        _recording = false;
        _status = 'Disconnected';
      });
    }
  }

  /// Called from dispose and error paths — no setState.
  void _disconnectCleanup() {
    _recorder.stop();
    _recordSub?.cancel();
    _recordSub = null;
    _wsSub?.cancel();
    _wsSub = null;
    _ws?.sink.close();
    _ws = null;
    _connected = false;
    _recording = false;
    _feedQueue.clear();
    FlutterPcmSound.release();
  }

  void _onMessage(dynamic raw) {
    try {
      final String text;
      if (raw is Uint8List) {
        text = utf8.decode(raw);
      } else if (raw is String) {
        text = raw;
      } else {
        return;
      }

      final data = json.decode(text) as Map<String, dynamic>;

      // Tool calls come as top-level {"toolCall": {...}} — handle before parse
      // so they are never silently dropped.
      if (data.containsKey('toolCall')) {
        _handleToolCall(data);
        return;
      }

      final msg = _GeminiMsg.parse(data);

      switch (msg.type) {
        case _GeminiMsgType.setupComplete:
          _setStatus('Ready — tap mic to speak');
          _addSystemMsg('Ready');

        case _GeminiMsgType.audio:
          if (msg.audioBase64 != null && msg.audioBase64!.isNotEmpty) {
            final bytes = base64Decode(msg.audioBase64!);
            _playPcmChunk(bytes);
          }

        case _GeminiMsgType.inputTranscription:
          // Append all deltas (mirrors official demo's "append" mode).
          // We still only create a new bubble on the first delta, then
          // accumulate — this gives us the full sentence by the time
          // finished=true arrives, avoiding the empty-bubble bug.
          if (msg.text != null && msg.text!.isNotEmpty) {
            _appendTranscript(
              msg.text!,
              isUser: true,
              finished: msg.textFinished ?? false,
            );
          }

        case _GeminiMsgType.outputTranscription:
          if (msg.text != null && msg.text!.isNotEmpty) {
            _appendTranscript(
              msg.text!,
              isUser: false,
              finished: msg.textFinished ?? false,
            );
          }

        case _GeminiMsgType.turnComplete:
          if (mounted && !_disposed) {
            setState(() {
              _playing = false;
              _lastUserMsgFinished = true;
              _lastAssistantMsgFinished = true;
            });
          }

        case _GeminiMsgType.interrupted:
          _stopPlayback();
          if (mounted && !_disposed) {
            setState(() {
              _lastUserMsgFinished = true;
              _lastAssistantMsgFinished = true;
            });
          }
          _addSystemMsg('[Interrupted]');

        case _GeminiMsgType.toolCall:
          _handleToolCall(data);

        case _GeminiMsgType.unknown:
          break;
      }
    } catch (_) {}
  }

  // ── Tool calls ──────────────────────────────────────────────────────────────

  void _handleToolCall(Map<String, dynamic> data) {
    final toolCall = data['toolCall'] as Map<String, dynamic>?;
    if (toolCall == null) return;
    final calls = toolCall['functionCalls'] as List<dynamic>? ?? [];
    for (final call in calls) {
      final c = call as Map<String, dynamic>;
      final name = c['name'] as String? ?? '';
      final id = c['id'] as String? ?? '';
      final args = c['args'] as Map<String, dynamic>? ?? {};
      if (name == 'generate_image') {
        final prompt = args['prompt'] as String? ?? '';
        _addSystemMsg('Generating image...');
        _runGenerateImage(id, prompt);
      } else if (name == 'generate_video') {
        final prompt = args['prompt'] as String? ?? '';
        _addSystemMsg('Generating video — this takes ~60-90s...');
        _runGenerateVideo(id, prompt);
      }
    }
  }

  Future<void> _runGenerateImage(String callId, String prompt) async {
    // Derive image gen endpoint: same host as proxy, port 8091
    String imageEndpoint;
    try {
      final proxyUri = Uri.parse(_urlCtrl.text.trim());
      final host = proxyUri.host;
      imageEndpoint = 'http://$host:8091/generate';
    } catch (_) {
      imageEndpoint = 'http://10.0.2.2:8091/generate';
    }

    try {
      final resp = await http
          .post(
            Uri.parse(imageEndpoint),
            headers: {'Content-Type': 'application/json'},
            body: json.encode({'prompt': prompt}),
          )
          .timeout(const Duration(seconds: 30));

      if (resp.statusCode == 200) {
        final body = json.decode(resp.body) as Map<String, dynamic>;
        final imageBase64 = body['image_base64'] as String?;
        final mime = body['mime_type'] as String? ?? 'image/png';

        if (imageBase64 != null && imageBase64.isNotEmpty) {
          final imageBytes = base64Decode(imageBase64);
          // Show image in chat
          if (mounted && !_disposed) {
            setState(() {
              _messages.add(_ChatMsg(
                id: '${DateTime.now().microsecondsSinceEpoch}',
                isUser: false,
                text: '',
                imageBytes: imageBytes,
                imageMime: mime,
              ));
            });
            _scrollToBottom();
          }
          // Send success response back to Gemini
          _wsSend({
            'tool_response': {
              'function_responses': [
                {
                  'id': callId,
                  'name': 'generate_image',
                  'response': {'result': 'Image generated successfully.'},
                },
              ],
            },
          });
          return;
        }
      }
      throw Exception('HTTP ${resp.statusCode}: ${resp.body}');
    } catch (e) {
      _addSystemMsg('Image error: $e');
      // Send error response so Gemini can continue
      _wsSend({
        'tool_response': {
          'function_responses': [
            {
              'id': callId,
              'name': 'generate_image',
              'response': {'error': e.toString()},
            },
          ],
        },
      });
    }
  }

  Future<void> _runGenerateVideo(String callId, String prompt) async {
    String videoEndpoint;
    try {
      final proxyUri = Uri.parse(_urlCtrl.text.trim());
      videoEndpoint = 'http://${proxyUri.host}:8092/generate';
    } catch (_) {
      videoEndpoint = 'http://10.0.2.2:8092/generate';
    }

    try {
      final resp = await http
          .post(
            Uri.parse(videoEndpoint),
            headers: {'Content-Type': 'application/json'},
            body: json.encode({'prompt': prompt}),
          )
          .timeout(const Duration(minutes: 4));

      if (resp.statusCode == 200) {
        final body = json.decode(resp.body) as Map<String, dynamic>;
        final videoUrl = body['video_url'] as String?;

        if (videoUrl != null && videoUrl.isNotEmpty) {
          if (mounted && !_disposed) {
            setState(() {
              _messages.add(_ChatMsg(
                id: '${DateTime.now().microsecondsSinceEpoch}',
                isUser: false,
                text: '',
                videoUrl: videoUrl,
              ));
            });
            _scrollToBottom();
          }
          _wsSend({
            'tool_response': {
              'function_responses': [
                {
                  'id': callId,
                  'name': 'generate_video',
                  'response': {'result': 'Video generated successfully.'},
                },
              ],
            },
          });
          return;
        }
      }
      throw Exception('HTTP ${resp.statusCode}: ${resp.body}');
    } catch (e) {
      _addSystemMsg('Video error: $e');
      _wsSend({
        'tool_response': {
          'function_responses': [
            {
              'id': callId,
              'name': 'generate_video',
              'response': {'error': e.toString()},
            },
          ],
        },
      });
    }
  }

  // ── Audio recording ─────────────────────────────────────────────────────────

  Future<void> _toggleMic() async {
    if (_disposed) return;
    if (!_connected) {
      await _connect();
      return;
    }
    if (_recording) {
      await _stopRecording();
      // Send audioStreamEnd to flush VAD
      _wsSend({
        'realtime_input': {'audio_stream_end': true},
      });
    } else {
      await _startRecording();
    }
  }

  Future<void> _startRecording() async {
    final status = await Permission.microphone.request();
    if (!status.isGranted) {
      _setStatus('Microphone permission denied');
      return;
    }

    try {
      // Record as PCM 16kHz mono — Gemini Live API requirement
      // echoCancellation prevents Gemini's speaker output from being picked up
      // by the mic and causing self-interruption (echo feedback loop).
      final stream = await _recorder.startStream(
        const RecordConfig(
          encoder: AudioEncoder.pcm16bits,
          sampleRate: 16000,
          numChannels: 1,
          noiseSuppress: true,
          echoCancel: true,
          autoGain: true,
        ),
      );

      _recordSub = stream.listen((chunk) {
        if (!_connected || chunk.isEmpty) return;
        // Send as realtime_input with audio/pcm mime type
        _wsSend({
          'realtime_input': {
            'media_chunks': [
              {
                'mime_type': 'audio/pcm;rate=16000',
                'data': base64Encode(chunk),
              },
            ],
          },
        });
      });

      if (mounted && !_disposed) {
        setState(() {
          _recording = true;
          _status = 'Listening...';
        });
      }
    } catch (e) {
      _setStatus('Mic error: $e');
    }
  }

  Future<void> _stopRecording() async {
    await _recordSub?.cancel();
    _recordSub = null;
    await _recorder.stop();
    if (mounted && !_disposed) {
      setState(() {
        _recording = false;
        _status = _connected ? 'Ready — tap mic to speak' : 'Disconnected';
      });
    }
  }

  // ── Audio playback ──────────────────────────────────────────────────────────

  /// Enqueue a PCM chunk for playback. Returns immediately — feeding is
  /// serialized in the background so the WebSocket message handler never blocks.
  void _playPcmChunk(Uint8List pcmBytes) {
    if (_disposed || !_pcmReady) return;
    _feedQueue.add(pcmBytes);
    if (!_feeding) {
      _drainFeedQueue();
    }
    if (mounted && !_disposed && !_playing) {
      setState(() => _playing = true);
    }
  }

  /// Drain the feed queue one chunk at a time without blocking the UI thread.
  Future<void> _drainFeedQueue() async {
    if (_feeding) return;
    _feeding = true;
    while (_feedQueue.isNotEmpty && !_disposed && _pcmReady) {
      final chunk = _feedQueue.removeAt(0);
      try {
        final byteData = chunk.buffer.asByteData(
          chunk.offsetInBytes,
          chunk.lengthInBytes,
        );
        await FlutterPcmSound.feed(PcmArrayInt16(bytes: byteData));
      } catch (_) {}
    }
    _feeding = false;
  }

  Future<void> _stopPlayback() async {
    _feedQueue.clear();
    try {
      await FlutterPcmSound.release();
      await FlutterPcmSound.setup(sampleRate: 24000, channelCount: 1);
      await FlutterPcmSound.setLogLevel(LogLevel.none);
      FlutterPcmSound.setFeedThreshold(960);
      FlutterPcmSound.setFeedCallback((_) {});
    } catch (_) {}
    if (mounted && !_disposed) setState(() => _playing = false);
  }

  // ── Chat helpers ────────────────────────────────────────────────────────────

  /// Mirrors the official demo's addMessage(text, type, mode="append", isFinished).
  /// Always appends to the last bubble of the same role while it's unfinished.
  void _appendTranscript(
    String text, {
    required bool isUser,
    required bool finished,
  }) {
    if (!mounted || _disposed) return;
    if (text.trim().isEmpty && !finished) return;

    setState(() {
      final lastFinished = isUser
          ? _lastUserMsgFinished
          : _lastAssistantMsgFinished;

      if (!lastFinished &&
          _messages.isNotEmpty &&
          _messages.last.isUser == isUser) {
        // Append to the existing in-progress bubble
        _messages.last.text += text;
        if (finished) {
          if (isUser) {
            _lastUserMsgFinished = true;
          } else {
            _lastAssistantMsgFinished = true;
          }
        }
      } else {
        // Start a new bubble
        if (text.trim().isNotEmpty) {
          _messages.add(
            _ChatMsg(
              id: '${DateTime.now().microsecondsSinceEpoch}',
              isUser: isUser,
              text: text,
            ),
          );
          if (isUser) {
            _lastUserMsgFinished = finished;
          } else {
            _lastAssistantMsgFinished = finished;
          }
        }
      }
    });
    _scrollToBottom();
  }

  void _addSystemMsg(String text) {
    if (!mounted || _disposed) return;
    setState(() {
      _messages.add(
        _ChatMsg(
          id: '${DateTime.now().microsecondsSinceEpoch}',
          isUser: false,
          text: '[$text]',
        ),
      );
    });
    _scrollToBottom();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_disposed && _scrollCtrl.hasClients) {
        _scrollCtrl.animateTo(
          _scrollCtrl.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  void _setStatus(String s) {
    if (mounted && !_disposed) setState(() => _status = s);
  }

  void _wsSend(Map<String, dynamic> msg) {
    try {
      _ws?.sink.add(json.encode(msg));
    } catch (_) {}
  }

  // ── Build ───────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0A1A0A),
      appBar: AppBar(
        backgroundColor: const Color(0xFF0A1A0A),
        foregroundColor: Colors.white,
        title: const Text(
          'Voice Mode (Live)',
          style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
        ),
        actions: [
          // Connection toggle
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: TextButton(
              onPressed: _connected ? _disconnect : _connect,
              child: Text(
                _connected ? 'Disconnect' : 'Connect',
                style: TextStyle(
                  color: _connected ? Colors.redAccent : Colors.greenAccent,
                  fontWeight: FontWeight.bold,
                ),
              ),
            ),
          ),
        ],
      ),
      body: Column(
        children: [
          // Proxy URL input — only shown when disconnected
          if (!_connected && !_connecting) _ProxyUrlField(controller: _urlCtrl),

          // Status bar
          _StatusBar(status: _status, connected: _connected, playing: _playing),

          // Chat messages
          Expanded(
            child: _messages.isEmpty
                ? const _EmptyState()
                : ListView.builder(
                    controller: _scrollCtrl,
                    padding: const EdgeInsets.symmetric(
                      horizontal: 16,
                      vertical: 8,
                    ),
                    itemCount: _messages.length,
                    itemBuilder: (_, i) => _ChatBubble(msg: _messages[i]),
                  ),
          ),

          // Waveform
          _WaveformBar(active: _recording, animation: _waveCtrl),

          // Mic button
          _MicButton(
            recording: _recording,
            connected: _connected,
            connecting: _connecting,
            pulse: _pulseCtrl,
            onTap: _toggleMic,
          ),

          const SizedBox(height: 24),
        ],
      ),
    );
  }
}

// ── Widgets ───────────────────────────────────────────────────────────────────

class _StatusBar extends StatelessWidget {
  final String status;
  final bool connected;
  final bool playing;

  const _StatusBar({
    required this.status,
    required this.connected,
    required this.playing,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      color: Colors.white.withAlpha(8),
      child: Row(
        children: [
          Container(
            width: 8,
            height: 8,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: connected ? Colors.greenAccent : Colors.grey,
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              status,
              style: const TextStyle(color: Colors.white70, fontSize: 12),
            ),
          ),
          if (playing)
            const Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(Icons.volume_up, color: Colors.greenAccent, size: 14),
                SizedBox(width: 4),
                Text(
                  'Speaking',
                  style: TextStyle(color: Colors.greenAccent, fontSize: 11),
                ),
              ],
            ),
        ],
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState();

  @override
  Widget build(BuildContext context) {
    return const Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.mic_none, color: Colors.white24, size: 64),
          SizedBox(height: 16),
          Text(
            'Connect and tap the mic to start\na live conversation with LORE',
            textAlign: TextAlign.center,
            style: TextStyle(color: Colors.white38, fontSize: 14),
          ),
        ],
      ),
    );
  }
}

class _ChatBubble extends StatelessWidget {
  final _ChatMsg msg;
  const _ChatBubble({required this.msg});

  @override
  Widget build(BuildContext context) {
    // Video bubble
    if (msg.videoUrl != null) {
      return _VideoBubble(url: msg.videoUrl!);
    }

    // Image bubble
    if (msg.imageBytes != null) {
      return Align(
        alignment: Alignment.centerLeft,
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 6),
          constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * 0.85,
          ),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: Colors.white.withAlpha(20)),
          ),
          clipBehavior: Clip.antiAlias,
          child: Image.memory(msg.imageBytes!, fit: BoxFit.contain),
        ),
      );
    }

    final isSystem = msg.text.startsWith('[') && msg.text.endsWith(']');
    if (isSystem) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Center(
          child: Text(
            msg.text,
            style: const TextStyle(color: Colors.white38, fontSize: 11),
          ),
        ),
      );
    }

    return Align(
      alignment: msg.isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * 0.78,
        ),
        decoration: BoxDecoration(
          color: msg.isUser
              ? Colors.greenAccent.withAlpha(40)
              : Colors.white.withAlpha(12),
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(16),
            topRight: const Radius.circular(16),
            bottomLeft: Radius.circular(msg.isUser ? 16 : 4),
            bottomRight: Radius.circular(msg.isUser ? 4 : 16),
          ),
          border: Border.all(
            color: msg.isUser
                ? Colors.greenAccent.withAlpha(60)
                : Colors.white.withAlpha(15),
          ),
        ),
        child: Text(
          msg.text,
          style: TextStyle(
            color: msg.isUser ? Colors.greenAccent : Colors.white,
            fontSize: 14,
          ),
        ),
      ),
    );
  }
}

// ── Video bubble ──────────────────────────────────────────────────────────────

class _VideoBubble extends StatefulWidget {
  final String url;
  const _VideoBubble({required this.url});

  @override
  State<_VideoBubble> createState() => _VideoBubbleState();
}

class _VideoBubbleState extends State<_VideoBubble> {
  late VideoPlayerController _ctrl;
  bool _initialized = false;
  bool _error = false;

  @override
  void initState() {
    super.initState();
    _ctrl = VideoPlayerController.networkUrl(Uri.parse(widget.url))
      ..initialize().then((_) {
        if (mounted) setState(() => _initialized = true);
      }).catchError((_) {
        if (mounted) setState(() => _error = true);
      });
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final width = MediaQuery.of(context).size.width * 0.85;
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 6),
        width: width,
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: Colors.white.withAlpha(20)),
          color: Colors.black,
        ),
        clipBehavior: Clip.antiAlias,
        child: _error
            ? const Padding(
                padding: EdgeInsets.all(16),
                child: Text(
                  'Video unavailable',
                  style: TextStyle(color: Colors.white38, fontSize: 12),
                ),
              )
            : !_initialized
                ? SizedBox(
                    height: width * 9 / 16,
                    child: const Center(
                      child: CircularProgressIndicator(
                        color: Colors.greenAccent,
                        strokeWidth: 2,
                      ),
                    ),
                  )
                : Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      AspectRatio(
                        aspectRatio: _ctrl.value.aspectRatio,
                        child: VideoPlayer(_ctrl),
                      ),
                      // Controls row
                      Padding(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 8, vertical: 4),
                        child: Row(
                          children: [
                            IconButton(
                              icon: Icon(
                                _ctrl.value.isPlaying
                                    ? Icons.pause_rounded
                                    : Icons.play_arrow_rounded,
                                color: Colors.greenAccent,
                                size: 28,
                              ),
                              onPressed: () => setState(() {
                                _ctrl.value.isPlaying
                                    ? _ctrl.pause()
                                    : _ctrl.play();
                              }),
                            ),
                            Expanded(
                              child: VideoProgressIndicator(
                                _ctrl,
                                allowScrubbing: true,
                                colors: const VideoProgressColors(
                                  playedColor: Colors.greenAccent,
                                  bufferedColor: Colors.white24,
                                  backgroundColor: Colors.white12,
                                ),
                              ),
                            ),
                            const SizedBox(width: 8),
                          ],
                        ),
                      ),
                    ],
                  ),
      ),
    );
  }
}

class _WaveformBar extends StatelessWidget {
  final bool active;
  final AnimationController animation;

  const _WaveformBar({required this.active, required this.animation});

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 48,
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      decoration: BoxDecoration(
        color: Colors.white.withAlpha(6),
        borderRadius: BorderRadius.circular(10),
      ),
      child: AnimatedBuilder(
        animation: animation,
        builder: (context2, child2) => CustomPaint(
          size: Size.infinite,
          painter: _WavePainter(t: animation.value, active: active),
        ),
      ),
    );
  }
}

class _WavePainter extends CustomPainter {
  final double t;
  final bool active;
  const _WavePainter({required this.t, required this.active});

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()..style = PaintingStyle.fill;
    const bars = 36;
    final bw = size.width / bars;
    final cy = size.height / 2;

    for (int i = 0; i < bars; i++) {
      final x = i * bw + bw / 2;
      final n = i / bars;
      final amp = active
          ? (math.sin((n * math.pi * 4) + t * math.pi * 2) * 0.5 +
                    math.sin((n * math.pi * 6) + t * math.pi * 3) * 0.3)
                .abs()
          : 0.05;
      final h = math.max(2.0, amp * size.height * 0.7);
      paint.color = Color.lerp(
        Colors.greenAccent.withAlpha(30),
        Colors.greenAccent,
        active ? amp.clamp(0.0, 1.0) : 0.1,
      )!;
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromCenter(center: Offset(x, cy), width: bw * 0.5, height: h),
          const Radius.circular(2),
        ),
        paint,
      );
    }
  }

  @override
  bool shouldRepaint(_WavePainter old) => old.t != t || old.active != active;
}

class _MicButton extends StatelessWidget {
  final bool recording;
  final bool connected;
  final bool connecting;
  final AnimationController pulse;
  final VoidCallback onTap;

  const _MicButton({
    required this.recording,
    required this.connected,
    required this.connecting,
    required this.pulse,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final color = recording
        ? Colors.redAccent
        : connected
        ? Colors.greenAccent
        : Colors.white38;

    return GestureDetector(
      onTap: connecting ? null : onTap,
      child: AnimatedBuilder(
        animation: pulse,
        builder: (_, child) {
          final scale = recording ? (1.0 + pulse.value * 0.08) : 1.0;
          return Transform.scale(scale: scale, child: child);
        },
        child: Container(
          width: 72,
          height: 72,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: color.withAlpha(30),
            border: Border.all(color: color, width: 2),
            boxShadow: [
              BoxShadow(
                color: color.withAlpha(60),
                blurRadius: 20,
                spreadRadius: 2,
              ),
            ],
          ),
          child: connecting
              ? const Center(
                  child: SizedBox(
                    width: 24,
                    height: 24,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Colors.white54,
                    ),
                  ),
                )
              : Icon(
                  recording ? Icons.stop_rounded : Icons.mic_rounded,
                  color: color,
                  size: 32,
                ),
        ),
      ),
    );
  }
}

class _ProxyUrlField extends StatelessWidget {
  final TextEditingController controller;
  const _ProxyUrlField({required this.controller});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 10, 16, 6),
      color: Colors.white.withAlpha(6),
      child: Row(
        children: [
          const Icon(Icons.dns_outlined, color: Colors.white38, size: 16),
          const SizedBox(width: 8),
          Expanded(
            child: TextField(
              controller: controller,
              style: const TextStyle(color: Colors.white70, fontSize: 13),
              decoration: const InputDecoration(
                hintText: 'ws://192.168.x.x:8090',
                hintStyle: TextStyle(color: Colors.white24, fontSize: 13),
                isDense: true,
                border: InputBorder.none,
                contentPadding: EdgeInsets.zero,
              ),
              keyboardType: TextInputType.url,
              autocorrect: false,
            ),
          ),
        ],
      ),
    );
  }
}
