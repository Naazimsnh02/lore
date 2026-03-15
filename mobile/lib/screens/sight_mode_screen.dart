/// SightMode — live camera + audio streaming to LORE.
///
/// Opens directly into streaming mode: video frames (1fps) and mic audio
/// are sent to Gemini as soon as the screen loads. No capture step needed.
/// The user simply speaks to ask questions about what the camera sees.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_pcm_sound/flutter_pcm_sound.dart';
import 'package:gal/gal.dart';
import 'package:geolocator/geolocator.dart';
import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:record/record.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:video_player/video_player.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../services/camera_service.dart';

const String _kMapsApiKey =
    String.fromEnvironment('GOOGLE_MAPS_API_KEY', defaultValue: '');

// ── Config ────────────────────────────────────────────────────────────────────

const String _kExplicitProxyUrl =
    String.fromEnvironment('GEMINI_PROXY_URL', defaultValue: '');
const String _kGatewayUrl =
    String.fromEnvironment('WEBSOCKET_GATEWAY_URL', defaultValue: '');

String get _kDefaultProxyUrl {
  if (_kExplicitProxyUrl.isNotEmpty) return _kExplicitProxyUrl;
  if (_kGatewayUrl.isNotEmpty) {
    try {
      final uri = Uri.parse(_kGatewayUrl);
      return uri.replace(port: 8090, path: '').toString();
    } catch (_) {}
  }
  return 'ws://10.0.2.2:8090';
}

const String _kProjectId =
    String.fromEnvironment('GCP_PROJECT_ID', defaultValue: '');
const bool _kUseVertexAI = String.fromEnvironment('GOOGLE_GENAI_USE_VERTEXAI', defaultValue: 'false') == 'true';

const String _kModelVertex = 'gemini-live-2.5-flash-native-audio';
const String _kModelAIStudio = 'gemini-2.5-flash-native-audio-preview-12-2025';
String get _kModel => _kUseVertexAI ? _kModelVertex : _kModelAIStudio;

String get _modelUri {
  if (_kUseVertexAI && _kProjectId.isNotEmpty) {
    return 'projects/$_kProjectId/locations/us-central1/publishers/google/models/$_kModel';
  }
  return 'models/$_kModel';
}

// Prefs keys
const _kPrefSubtitles = 'lore_sight_subtitles';

// ── Location tag parser ───────────────────────────────────────────────────────

final _locationTagRe = RegExp(r'\[LOCATION:\s*([^\]]+)\]');

String? _extractLocation(String text) =>
    _locationTagRe.firstMatch(text)?.group(1)?.trim();

String _stripLocationTag(String text) =>
    text.replaceAll(_locationTagRe, '').trim();

// ── Gemini message parsing ────────────────────────────────────────────────────

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

  const _GeminiMsg(
      {required this.type, this.audioBase64, this.text, this.textFinished});

  factory _GeminiMsg.parse(Map<String, dynamic> data) {
    try {
      if (data.containsKey('setupComplete')) {
        return const _GeminiMsg(type: _GeminiMsgType.setupComplete);
      }
      if (data.containsKey('toolCall')) {
        return const _GeminiMsg(type: _GeminiMsgType.toolCall);
      }
      final sc = data['serverContent'] as Map<String, dynamic>?;
      if (sc != null) {
        if (sc['turnComplete'] == true) {
          return const _GeminiMsg(type: _GeminiMsgType.turnComplete);
        }
        if (sc['interrupted'] == true) {
          return const _GeminiMsg(type: _GeminiMsgType.interrupted);
        }
        final inTrans = sc['inputTranscription'] as Map<String, dynamic>?;
        if (inTrans != null) {
          return _GeminiMsg(
            type: _GeminiMsgType.inputTranscription,
            text: inTrans['text'] as String? ?? '',
            textFinished: inTrans['finished'] as bool? ?? false,
          );
        }
        final outTrans = sc['outputTranscription'] as Map<String, dynamic>?;
        if (outTrans != null) {
          final text = outTrans['text'] as String? ?? '';
          if (text.isNotEmpty) {
            return _GeminiMsg(
              type: _GeminiMsgType.outputTranscription,
              text: text,
              textFinished: outTrans['finished'] as bool? ?? false,
            );
          }
        }
        List<dynamic>? parts =
            (sc['modelTurn'] as Map<String, dynamic>?)?['parts']
                as List<dynamic>?;
        parts ??= sc['parts'] as List<dynamic>?;
        if (parts != null) {
          for (final part in parts) {
            final p = part as Map<String, dynamic>;
            final audioData =
                (p['inlineData'] as Map<String, dynamic>?)?['data'] as String?;
            if (audioData != null && audioData.isNotEmpty) {
              return _GeminiMsg(
                  type: _GeminiMsgType.audio, audioBase64: audioData);
            }
            final textPart = p['text'] as String?;
            if (textPart != null && textPart.isNotEmpty) {
              return _GeminiMsg(
                  type: _GeminiMsgType.outputTranscription,
                  text: textPart,
                  textFinished: false);
            }
          }
        }
      }
    } catch (_) {}
    return const _GeminiMsg(type: _GeminiMsgType.unknown);
  }
}

// ── Chat message model ────────────────────────────────────────────────────────

enum _ChatMsgKind { text, image, video, loading }

class _ChatMsg {
  final String id;
  final bool isUser;
  String text;
  Uint8List? imageBytes;
  String? imageMime;
  String? videoUrl;
  final _ChatMsgKind kind;
  final DateTime timestamp;

  _ChatMsg({
    required this.id,
    required this.isUser,
    required this.text,
    this.imageBytes,
    this.imageMime,
    this.videoUrl,
    required this.kind,
    DateTime? timestamp,
  }) : timestamp = timestamp ?? DateTime.now();

  Map<String, dynamic> toJson() => {
        'id': id,
        'isUser': isUser,
        'text': text,
        'imageBase64': imageBytes != null ? base64Encode(imageBytes!) : null,
        'imageMime': imageMime,
        'videoUrl': videoUrl,
        'kind': kind.name,
        'timestamp': timestamp.millisecondsSinceEpoch,
      };

  factory _ChatMsg.fromJson(Map<String, dynamic> j) {
    final b64 = j['imageBase64'] as String?;
    return _ChatMsg(
      id: j['id'] as String,
      isUser: j['isUser'] as bool,
      text: j['text'] as String? ?? '',
      imageBytes: b64 != null && b64.isNotEmpty ? base64Decode(b64) : null,
      imageMime: j['imageMime'] as String?,
      videoUrl: j['videoUrl'] as String?,
      kind: _ChatMsgKind.values.firstWhere(
        (e) => e.name == j['kind'],
        orElse: () => _ChatMsgKind.text,
      ),
      timestamp:
          DateTime.fromMillisecondsSinceEpoch(j['timestamp'] as int? ?? 0),
    );
  }
}

// ── Persistence ───────────────────────────────────────────────────────────────

class _Store {
  static const _currentKey = 'lore_sight_current_session';
  static const _sessionsKey = 'lore_sight_sessions';

  static Future<String> currentSessionId() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_currentKey) ?? await newSession();
  }

  static Future<String> newSession() async {
    final prefs = await SharedPreferences.getInstance();
    final id = 'sight_${DateTime.now().millisecondsSinceEpoch}';
    await prefs.setString(_currentKey, id);
    return id;
  }

  static Future<void> save(String sessionId, List<_ChatMsg> messages) async {
    final prefs = await SharedPreferences.getInstance();
    final toSave = messages
        .where((m) =>
            m.kind != _ChatMsgKind.loading &&
            (m.text.isNotEmpty || m.imageBytes != null || m.videoUrl != null))
        .toList();
    await prefs.setString('lore_session_$sessionId',
        json.encode(toSave.map((m) => m.toJson()).toList()));
    final sessions = prefs.getStringList(_sessionsKey) ?? [];
    if (!sessions.contains(sessionId)) {
      sessions.add(sessionId);
      await prefs.setStringList(_sessionsKey, sessions);
    }
  }

  static Future<List<_ChatMsg>> load(String sessionId) async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString('lore_session_$sessionId');
    if (raw == null) return [];
    try {
      return (json.decode(raw) as List)
          .map((e) => _ChatMsg.fromJson(e as Map<String, dynamic>))
          .toList();
    } catch (_) {
      return [];
    }
  }

  static Future<bool> loadSubtitlePref() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getBool(_kPrefSubtitles) ?? false;
  }

  static Future<void> saveSubtitlePref(bool value) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_kPrefSubtitles, value);
  }
}

// ── Screen ────────────────────────────────────────────────────────────────────

class SightModeScreen extends ConsumerStatefulWidget {
  const SightModeScreen({super.key});

  @override
  ConsumerState<SightModeScreen> createState() => _SightModeScreenState();
}

class _SightModeScreenState extends ConsumerState<SightModeScreen>
    with TickerProviderStateMixin {
  // ── WebSocket ──────────────────────────────────────────────────────────────
  WebSocketChannel? _ws;
  StreamSubscription? _wsSub;
  bool _connected = false;
  bool _connecting = false;
  bool _disposed = false;
  Completer<void>? _setupCompleter;

  // ── Camera ─────────────────────────────────────────────────────────────────
  final _cameraService = CameraService();
  bool _cameraReady = false;
  bool _lowLight = false;
  Timer? _lowLightTimer;
  StreamSubscription? _lightingSub;

  // ── Live frame streaming (1fps) ────────────────────────────────────────────
  Timer? _frameTimer;

  // ── Mic ────────────────────────────────────────────────────────────────────
  final AudioRecorder _recorder = AudioRecorder();
  bool _recording = false;
  StreamSubscription? _recordSub;

  // ── PCM playback ───────────────────────────────────────────────────────────
  bool _pcmReady = false;
  bool _playing = false;
  final List<Uint8List> _feedQueue = [];
  bool _feeding = false;

  // ── Transcript overlay ─────────────────────────────────────────────────────
  final List<_ChatMsg> _messages = [];
  final ScrollController _scrollCtrl = ScrollController();
  bool _lastMsgFinished = true;
  bool _lastUserMsgFinished = true;
  bool _showTranscript = false;

  // ── GPS ────────────────────────────────────────────────────────────────────
  StreamSubscription<Position>? _gpsSub;
  Position? _lastPosition;

  // ── Animation ─────────────────────────────────────────────────────────────
  late AnimationController _pulseCtrl;

  // ── Session ────────────────────────────────────────────────────────────────
  String _sessionId = '';

  @override
  void initState() {
    super.initState();
    _pulseCtrl = AnimationController(
        vsync: this, duration: const Duration(seconds: 1))
      ..repeat(reverse: true);
    _loadAndStart();
  }

  Future<void> _loadAndStart() async {
    final subtitlePref = await _Store.loadSubtitlePref();
    if (mounted && !_disposed) setState(() => _showTranscript = subtitlePref);

    await _initPcm();
    _sessionId = await _Store.newSession();
    _initGps();
    await _initCameraAndStream();
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

  Future<void> _initCameraAndStream() async {
    // Request both permissions upfront
    final camStatus = await Permission.camera.request();
    final micStatus = await Permission.microphone.request();

    if (!camStatus.isGranted) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
              content: Text('Camera permission is required for SightMode.')),
        );
      }
      return;
    }

    try {
      await _cameraService.initialize();
      if (mounted && !_disposed) setState(() => _cameraReady = true);

      _lightingSub = _cameraService.lightingWarnings.listen((_) {
        if (!mounted || _disposed) return;
        setState(() => _lowLight = true);
        _lowLightTimer?.cancel();
        _lowLightTimer = Timer(const Duration(seconds: 5), () {
          if (mounted && !_disposed) setState(() => _lowLight = false);
        });
      });
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Camera failed: $e')));
      }
      return;
    }

    // Connect WebSocket and start streaming immediately
    await _connect();

    // Wait for setupComplete then start video + audio streams
    try {
      await _setupCompleter?.future.timeout(const Duration(seconds: 10));
    } catch (_) {}
    if (_disposed) return;

    _startVideoStream();
    if (micStatus.isGranted) {
      await _startRecording();
    }
  }

  // ── GPS context ──────────────────────────────────────────────────────────────

  void _initGps() async {
    final permission = await Geolocator.requestPermission();
    if (permission == LocationPermission.denied ||
        permission == LocationPermission.deniedForever) return;

    const settings = LocationSettings(
      accuracy: LocationAccuracy.high,
      distanceFilter: 30,
    );
    _gpsSub = Geolocator.getPositionStream(locationSettings: settings).listen(
      (pos) {
        final prev = _lastPosition;
        _lastPosition = pos;
        if (_connected) {
          final movedSignificantly = prev == null ||
              Geolocator.distanceBetween(prev.latitude, prev.longitude,
                      pos.latitude, pos.longitude) >
                  50;
          if (prev == null || movedSignificantly) {
            _sendGpsContext(pos);
          }
        }
      },
      onError: (_) {},
    );
  }

  Future<String?> _reverseGeocode(Position pos) async {
    if (_kMapsApiKey.isNotEmpty) {
      try {
        final url = Uri.parse(
          'https://maps.googleapis.com/maps/api/geocode/json'
          '?latlng=${pos.latitude},${pos.longitude}&key=$_kMapsApiKey',
        );
        final resp = await http.get(url).timeout(const Duration(seconds: 5));
        if (resp.statusCode == 200) {
          final data = json.decode(resp.body) as Map<String, dynamic>;
          if (data['status'] == 'OK') {
            final results = data['results'] as List<dynamic>;
            if (results.isNotEmpty) {
              return (results.first as Map<String, dynamic>)['formatted_address']
                  as String?;
            }
          }
        }
      } catch (_) {}
    }
    // Fallback: OpenStreetMap Nominatim
    try {
      final url = Uri.parse(
        'https://nominatim.openstreetmap.org/reverse'
        '?lat=${pos.latitude}&lon=${pos.longitude}&format=json',
      );
      final resp = await http
          .get(url, headers: {'User-Agent': 'LoreSightMode/1.0'}).timeout(
        const Duration(seconds: 5),
      );
      if (resp.statusCode == 200) {
        final data = json.decode(resp.body) as Map<String, dynamic>;
        return data['display_name'] as String?;
      }
    } catch (_) {}
    return null;
  }

  Future<void> _sendGpsContext(Position pos) async {
    final address = await _reverseGeocode(pos);
    final addressPart = address != null ? ', address="$address"' : '';
    final tag = '[GPS: lat=${pos.latitude.toStringAsFixed(5)}, '
        'lon=${pos.longitude.toStringAsFixed(5)}, '
        'accuracy=${pos.accuracy.toStringAsFixed(0)}m$addressPart]';
    if (!_connected || _disposed) return;
    _wsSend({
      'client_content': {
        'turns': [
          {
            'role': 'user',
            'parts': [
              {'text': tag}
            ]
          }
        ],
        'turn_complete': false,
      }
    });
  }

  /// Streams live camera frames at 1fps via realtime_input.video.
  void _startVideoStream() {    _frameTimer?.cancel();
    _frameTimer = Timer.periodic(const Duration(seconds: 1), (_) async {
      if (!_connected || _disposed || _cameraService.controller == null) return;
      try {
        final xfile = await _cameraService.controller!.takePicture();
        final bytes = await xfile.readAsBytes();
        if (!_connected || _disposed) return;
        _wsSend({
          'realtime_input': {
            'video': {'data': base64Encode(bytes), 'mime_type': 'image/jpeg'}
          }
        });
      } catch (_) {}
    });
  }

  @override
  void dispose() {
    _disposed = true;
    _frameTimer?.cancel();
    _lightingSub?.cancel();
    _lowLightTimer?.cancel();
    _gpsSub?.cancel();
    _cameraService.dispose();
    _disconnectCleanup();
    _pulseCtrl.dispose();
    FlutterPcmSound.release();
    _scrollCtrl.dispose();
    super.dispose();
  }

  // ── Connection ──────────────────────────────────────────────────────────────

  Future<void> _connect() async {
    if (_disposed || _connecting || _connected) return;
    debugPrint('[SightMode] _connect: starting...');
    if (mounted) setState(() => _connecting = true);
    _setupCompleter = Completer<void>();
    try {
      if (!_pcmReady) await _initPcm();
      final ws = WebSocketChannel.connect(Uri.parse(_kDefaultProxyUrl));
      await ws.ready;
      if (_disposed) {
        ws.sink.close();
        return;
      }
      _ws = ws;
      _ws!.sink.add(json.encode({'service_url': ''}));
      _wsSub = _ws!.stream.listen(
        _onMessage,
        onError: (e) {
          debugPrint('[SightMode] ws error: $e');
          _setupCompleter?.completeError('ws error');
          _disconnectCleanup();
          if (mounted && !_disposed) setState(() {});
        },
        onDone: () {
          debugPrint('[SightMode] ws done');
          if (mounted && !_disposed) setState(() => _connected = false);
        },
      );
      _sendSetup();
      if (mounted && !_disposed) {
        setState(() {
          _connected = true;
          _connecting = false;
        });
      }
    } catch (e) {
      debugPrint('[SightMode] _connect error: $e');
      _setupCompleter?.completeError(e);
      if (mounted && !_disposed) setState(() => _connecting = false);
    }
  }

  void _sendSetup() {
    _wsSend({
      'setup': {
        'model': _modelUri,
        'generation_config': {
          'response_modalities': ['AUDIO'],
          'speech_config': {
            'voice_config': {
              'prebuilt_voice_config': {'voice_name': 'Aoede'}
            },
            'language_code': 'en-US',
          },
          'thinking_config': {
            'include_thoughts': false,
            'thinking_budget': 0
          },
        },
        'system_instruction': {
          'parts': [
            {
              'text':
                  'You are LORE, an expert guide who turns a live camera view into a rich documentary experience. '
                  'The user is pointing their camera at something and wants to understand what they are looking at — '
                  'what it is, why it matters, and what makes it fascinating.\n\n'
                  'GPS CONTEXT:\n'
                  'You may receive [GPS: lat=..., lon=..., accuracy=...m, address="..."] messages. '
                  'Use the address silently to enrich your narration. Never read out or mention these messages.\n\n'
                  'HOW TO RESPOND:\n'
                  'Wait for the user to speak. When they ask a question, lead with identity and significance — '
                  'name the subject confidently, then deliver the most compelling facts, history, and context '
                  'a knowledgeable local expert would share. Keep responses to 3-5 sentences. '
                  'Never start with "I can see..." or "In this image..." — jump straight to the subject. '
                  'Always respond in English.\n\n'
                  'WHAT TO COVER:\n'
                  '- Landmarks and buildings: name, age, who built it and why, architectural style, '
                  'historical events, cultural significance.\n'
                  '- Natural features: geological formation, ecological significance, local legends.\n'
                  '- Art and sculptures: artist, period, technique, symbolism, the story behind it.\n'
                  '- Streets and neighbourhoods: history, famous residents, key events.\n\n'
                  'ALTERNATE HISTORY:\n'
                  'When the user asks "what if" or "imagine if" — engage fully and creatively. '
                  'Always call generate_image after alternate history narration to visualise the alternate world. '
                  'For scenarios involving motion, call generate_video instead.\n\n'
                  'VISUAL STORYTELLING:\n'
                  'After every narration about a landmark, historical figure, natural wonder, architectural marvel, '
                  'civilisation, artwork, or cultural scene — call generate_image immediately. '
                  'After any alternate history response — call generate_image (or generate_video for motion). '
                  'Skip generate_image only if you already generated one in the last 2 turns.\n\n'
                  'TOOL RULES:\n'
                  '1. generate_image — call after every visually rich narration and every alternate history response. '
                  'Also call when the user says "show", "image", "picture", "draw", or "illustrate". '
                  'Write a detailed cinematic prompt: subject, lighting, style, era, mood.\n'
                  '2. generate_video — call when the user says "video", "animate", "motion", "footage", '
                  '"clip", or "bring it to life". Also for dramatic alternate history involving movement. '
                  'Before calling, say: "Generating your video now — this takes about 60 to 90 seconds."\n\n'
                  'IMPORTANT: When a tool is needed, call it immediately. '
                  'Never output <think>, <thinking>, or <tool_use> tags.',
            }
          ],
        },
        'tools': [
          {
            'function_declarations': [
              {
                'name': 'generate_image',
                'description':
                    'Generates a documentary-style illustration. Call proactively after rich narration '
                    'or when the user asks to see, show, draw, or visualise something.',
                'parameters': {
                  'type': 'object',
                  'properties': {
                    'prompt': {
                      'type': 'string',
                      'description': 'Detailed cinematic image generation prompt.'
                    }
                  },
                  'required': ['prompt']
                },
              },
              {
                'name': 'generate_video',
                'description':
                    'Generates a short cinematic video clip (8 seconds). Call when the user asks '
                    'for a video or animation. Takes 60-90 seconds.',
                'parameters': {
                  'type': 'object',
                  'properties': {
                    'prompt': {
                      'type': 'string',
                      'description': 'Detailed video generation prompt.'
                    }
                  },
                  'required': ['prompt']
                },
              },
            ]
          }
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
    });
  }

  void _disconnectCleanup() {
    _frameTimer?.cancel();
    _frameTimer = null;
    _recorder.stop();
    _recordSub?.cancel();
    _recordSub = null;
    _wsSub?.cancel();
    _wsSub = null;
    _ws?.sink.close();
    _ws = null;
    _connected = false;
    _recording = false;
    _pcmReady = false;
    _feedQueue.clear();
    _setupCompleter = null;
    try {
      FlutterPcmSound.release();
    } catch (_) {}
  }

  // ── Message handling ─────────────────────────────────────────────────────────

  void _onMessage(dynamic raw) {
    try {
      final text = raw is Uint8List ? utf8.decode(raw) : raw as String;
      final data = json.decode(text) as Map<String, dynamic>;
      if (data.containsKey('toolCall')) {
        _handleToolCall(data);
        return;
      }
      final msg = _GeminiMsg.parse(data);
      switch (msg.type) {
        case _GeminiMsgType.setupComplete:
          _setupCompleter?.complete();
          break;
        case _GeminiMsgType.audio:
          if (msg.audioBase64 != null && msg.audioBase64!.isNotEmpty) {
            _playPcmChunk(base64Decode(msg.audioBase64!));
          }
        case _GeminiMsgType.inputTranscription:
          if (msg.text != null && msg.text!.isNotEmpty) {
            _appendTranscript(msg.text!, isUser: true, finished: msg.textFinished ?? false);
          }
        case _GeminiMsgType.outputTranscription:
          if (msg.text != null && msg.text!.isNotEmpty) {
            _appendTranscript(msg.text!, isUser: false, finished: msg.textFinished ?? false);
          }
        case _GeminiMsgType.turnComplete:
          if (mounted && !_disposed) {
            setState(() {
              _playing = false;
              _lastMsgFinished = true;
              _lastUserMsgFinished = true;
            });
          }
          _saveSession();
        case _GeminiMsgType.interrupted:
          _stopPlayback();
          if (mounted && !_disposed) {
            setState(() {
              _lastMsgFinished = true;
              _lastUserMsgFinished = true;
            });
          }
        case _GeminiMsgType.toolCall:
          _handleToolCall(data);
        case _GeminiMsgType.unknown:
          break;
      }
    } catch (e) {
      debugPrint('[SightMode] _onMessage error: $e');
    }
  }

  // ── Tool calls ──────────────────────────────────────────────────────────────

  void _handleToolCall(Map<String, dynamic> data) {
    final calls =
        (data['toolCall']?['functionCalls'] as List<dynamic>?) ?? [];
    for (final call in calls) {
      final c = call as Map<String, dynamic>;
      final name = c['name'] as String? ?? '';
      final id = c['id'] as String? ?? '';
      final prompt =
          (c['args'] as Map<String, dynamic>?)?['prompt'] as String? ?? '';
      if (name == 'generate_image') {
        final loadingId = _addLoadingMsg('Generating image...');
        _runGenerateImage(id, prompt, loadingId);
      } else if (name == 'generate_video') {
        final loadingId =
            _addLoadingMsg('Generating video — this takes ~60-90s...');
        _runGenerateVideo(id, prompt, loadingId);
      }
    }
  }

  String _addLoadingMsg(String label) {
    final id = 'loading_${DateTime.now().microsecondsSinceEpoch}';
    if (mounted && !_disposed) {
      setState(() => _messages.add(_ChatMsg(
          id: id, isUser: false, text: label, kind: _ChatMsgKind.loading)));
    }
    _scrollToBottom();
    return id;
  }

  void _removeLoadingMsg(String id) {
    if (mounted && !_disposed) {
      setState(() => _messages.removeWhere((m) => m.id == id));
    }
  }

  Future<void> _runGenerateImage(
      String callId, String prompt, String loadingId) async {
    final host = Uri.parse(_kDefaultProxyUrl).host;
    try {
      final resp = await http
          .post(
            Uri.parse('http://$host:8091/generate'),
            headers: {'Content-Type': 'application/json'},
            body: json.encode({'prompt': prompt}),
          )
          .timeout(const Duration(seconds: 60));
      _removeLoadingMsg(loadingId);
      if (resp.statusCode == 200) {
        final body = json.decode(resp.body) as Map<String, dynamic>;
        final b64 = body['image_base64'] as String?;
        final mime = body['mime_type'] as String? ?? 'image/png';
        if (b64 != null && b64.isNotEmpty) {
          final msg = _ChatMsg(
            id: '${DateTime.now().microsecondsSinceEpoch}',
            isUser: false,
            text: '',
            imageBytes: base64Decode(b64),
            imageMime: mime,
            kind: _ChatMsgKind.image,
          );
          if (mounted && !_disposed) setState(() => _messages.add(msg));
          _scrollToBottom();
          _saveSession();
          _wsSend({
            'tool_response': {
              'function_responses': [
                {
                  'id': callId,
                  'name': 'generate_image',
                  'response': {'result': 'Image generated successfully.'}
                }
              ]
            }
          });
          return;
        }
      }
      throw Exception('HTTP ${resp.statusCode}');
    } catch (e) {
      _removeLoadingMsg(loadingId);
      _wsSend({
        'tool_response': {
          'function_responses': [
            {
              'id': callId,
              'name': 'generate_image',
              'response': {'error': e.toString()}
            }
          ]
        }
      });
    }
  }

  Future<void> _runGenerateVideo(
      String callId, String prompt, String loadingId) async {
    final host = Uri.parse(_kDefaultProxyUrl).host;
    try {
      final resp = await http
          .post(
            Uri.parse('http://$host:8092/generate'),
            headers: {'Content-Type': 'application/json'},
            body: json.encode({'prompt': prompt}),
          )
          .timeout(const Duration(minutes: 4));
      _removeLoadingMsg(loadingId);
      if (resp.statusCode == 200) {
        final body = json.decode(resp.body) as Map<String, dynamic>;
        final videoUrl = body['video_url'] as String?;
        if (videoUrl != null && videoUrl.isNotEmpty) {
          final msg = _ChatMsg(
            id: '${DateTime.now().microsecondsSinceEpoch}',
            isUser: false,
            text: '',
            videoUrl: videoUrl,
            kind: _ChatMsgKind.video,
          );
          if (mounted && !_disposed) setState(() => _messages.add(msg));
          _scrollToBottom();
          _saveSession();
          _wsSend({
            'tool_response': {
              'function_responses': [
                {
                  'id': callId,
                  'name': 'generate_video',
                  'response': {'result': 'Video generated successfully.'}
                }
              ]
            }
          });
          return;
        }
      }
      throw Exception('HTTP ${resp.statusCode}');
    } catch (e) {
      _removeLoadingMsg(loadingId);
      _wsSend({
        'tool_response': {
          'function_responses': [
            {
              'id': callId,
              'name': 'generate_video',
              'response': {'error': e.toString()}
            }
          ]
        }
      });
    }
  }

  void _appendTranscript(String rawText, {required bool isUser, required bool finished}) {
    if (!mounted || _disposed || rawText.trim().isEmpty) return;

    final displayText = _stripLocationTag(rawText);
    if (displayText.isEmpty) return;

    setState(() {
      final lastFinished = isUser ? _lastUserMsgFinished : _lastMsgFinished;
      if (!lastFinished &&
          _messages.isNotEmpty &&
          _messages.last.isUser == isUser &&
          _messages.last.kind == _ChatMsgKind.text) {
        final existing = _messages.last.text;
        final needsSpace = existing.isNotEmpty &&
            !existing.endsWith(' ') &&
            !displayText.startsWith(' ');
        _messages.last.text =
            needsSpace ? '$existing $displayText' : '$existing$displayText';
        if (finished) {
          if (isUser) {
            _lastUserMsgFinished = true;
          } else {
            _lastMsgFinished = true;
          }
        }
      } else {
        _messages.add(_ChatMsg(
          id: '${DateTime.now().microsecondsSinceEpoch}',
          isUser: isUser,
          text: displayText,
          kind: _ChatMsgKind.text,
        ));
        if (isUser) {
          _lastUserMsgFinished = finished;
        } else {
          _lastMsgFinished = finished;
        }
      }
    });
    _scrollToBottom();
  }

  // ── Mic ─────────────────────────────────────────────────────────────────────

  Future<void> _toggleMic() async {
    if (_disposed) return;
    if (!_connected) {
      await _connect();
      return;
    }
    if (_recording) {
      await _stopRecording();
    } else {
      await _startRecording();
    }
  }

  Future<void> _startRecording() async {
    if (_recording) return;
    final status = await Permission.microphone.request();
    if (!status.isGranted) return;
    try {
      final stream = await _recorder.startStream(const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        sampleRate: 16000,
        numChannels: 1,
        noiseSuppress: true,
        echoCancel: true,
        autoGain: true,
      ));
      _recordSub = stream.listen((chunk) {
        if (!_connected || chunk.isEmpty) return;
        _wsSend({
          'realtime_input': {
            'media_chunks': [
              {'mime_type': 'audio/pcm;rate=16000', 'data': base64Encode(chunk)}
            ]
          }
        });
      });
      if (mounted && !_disposed) setState(() => _recording = true);
    } catch (_) {}
  }

  Future<void> _stopRecording() async {
    await _recordSub?.cancel();
    _recordSub = null;
    await _recorder.stop();
    if (mounted && !_disposed) setState(() => _recording = false);
  }

  // ── Audio playback ──────────────────────────────────────────────────────────

  void _playPcmChunk(Uint8List pcmBytes) {
    if (_disposed || !_pcmReady) return;
    _feedQueue.add(pcmBytes);
    if (!_feeding) _drainFeedQueue();
    if (mounted && !_disposed && !_playing) setState(() => _playing = true);
  }

  Future<void> _drainFeedQueue() async {
    if (_feeding) return;
    _feeding = true;
    while (_feedQueue.isNotEmpty && !_disposed && _pcmReady) {
      final chunk = _feedQueue.removeAt(0);
      try {
        await FlutterPcmSound.feed(PcmArrayInt16(
            bytes: chunk.buffer
                .asByteData(chunk.offsetInBytes, chunk.lengthInBytes)));
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
      _pcmReady = true;
    } catch (_) {}
    if (mounted && !_disposed) setState(() => _playing = false);
  }

  // ── Helpers ─────────────────────────────────────────────────────────────────

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

  void _wsSend(Map<String, dynamic> msg) {
    try {
      _ws?.sink.add(json.encode(msg));
    } catch (_) {}
  }

  Future<void> _saveSession() async {
    if (_sessionId.isEmpty) return;
    await _Store.save(_sessionId, _messages);
  }

  void _toggleSubtitles() {
    final next = !_showTranscript;
    setState(() => _showTranscript = next);
    _Store.saveSubtitlePref(next);
  }

  // ── Build ───────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(
        fit: StackFit.expand,
        children: [
          // ── Live camera preview ──────────────────────────────────────────
          if (_cameraReady && _cameraService.controller != null)
            _FullscreenCamera(controller: _cameraService.controller!)
          else
            const Center(
                child: CircularProgressIndicator(color: Colors.white54)),

          // ── Transcript overlay ───────────────────────────────────────────
          if (_messages.isNotEmpty)
            Positioned(
              left: 0,
              right: 0,
              bottom: 0,
              child: _TranscriptPanel(
                messages: _messages,
                scrollCtrl: _scrollCtrl,
                visible: _showTranscript,
              ),
            ),

          // ── Top bar ──────────────────────────────────────────────────────
          Positioned(
            top: 0,
            left: 0,
            right: 0,
            child: SafeArea(
              child: Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
                child: Row(
                  children: [
                    IconButton(
                      icon: const Icon(Icons.arrow_back, color: Colors.white),
                      onPressed: () => Navigator.pop(context),
                    ),
                    const Spacer(),
                    // Subtitle toggle
                    IconButton(
                      icon: Icon(
                        _showTranscript
                            ? Icons.subtitles_rounded
                            : Icons.subtitles_off_rounded,
                        color: Colors.white70,
                        size: 20,
                      ),
                      tooltip: 'Toggle transcript',
                      onPressed: _toggleSubtitles,
                    ),
                    const SizedBox(width: 4),
                    // Connection status dot
                    Padding(
                      padding: const EdgeInsets.only(right: 12),
                      child: Container(
                        width: 7,
                        height: 7,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: _connecting
                              ? Colors.amber
                              : _connected
                                  ? Colors.greenAccent
                                  : Colors.white24,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),

          // ── Low-light warning ────────────────────────────────────────────
          if (_lowLight)
            const Positioned(
              top: 80,
              left: 16,
              right: 16,
              child: _LowLightBanner(),
            ),

          // ── Mic FAB (bottom-right) ───────────────────────────────────────
          Positioned(
            right: 16,
            bottom: 0,
            child: SafeArea(
              child: Padding(
                padding: const EdgeInsets.only(bottom: 24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.center,
                  children: [
                    _MicFab(
                      recording: _recording,
                      connected: _connected,
                      connecting: _connecting,
                      playing: _playing,
                      pulse: _pulseCtrl,
                      onTap: _toggleMic,
                    ),
                    const SizedBox(height: 5),
                    Text(
                      _recording ? 'listening...' : 'tap to mute',
                      style: const TextStyle(
                          color: Colors.white38,
                          fontSize: 10,
                          letterSpacing: 0.3),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ── Fullscreen camera ─────────────────────────────────────────────────────────

class _FullscreenCamera extends StatelessWidget {
  final CameraController controller;
  const _FullscreenCamera({required this.controller});

  @override
  Widget build(BuildContext context) {
    return SizedBox.expand(
      child: FittedBox(
        fit: BoxFit.cover,
        child: SizedBox(
          width: controller.value.previewSize?.height ?? 1,
          height: controller.value.previewSize?.width ?? 1,
          child: CameraPreview(controller),
        ),
      ),
    );
  }
}

// ── Transcript panel ──────────────────────────────────────────────────────────

class _TranscriptPanel extends StatelessWidget {
  final List<_ChatMsg> messages;
  final ScrollController scrollCtrl;
  final bool visible;

  const _TranscriptPanel(
      {required this.messages,
      required this.scrollCtrl,
      required this.visible});

  @override
  Widget build(BuildContext context) {
    return AnimatedSlide(
      offset: visible ? Offset.zero : const Offset(0, 1),
      duration: const Duration(milliseconds: 250),
      curve: Curves.easeInOut,
      child: AnimatedOpacity(
        opacity: visible ? 1.0 : 0.0,
        duration: const Duration(milliseconds: 200),
        child: Container(
          constraints: BoxConstraints(
              maxHeight: MediaQuery.of(context).size.height * 0.5),
          decoration: BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: [
                Colors.transparent,
                Colors.black.withAlpha(200),
                Colors.black.withAlpha(230),
              ],
            ),
          ),
          child: ListView.builder(
            controller: scrollCtrl,
            padding: const EdgeInsets.fromLTRB(16, 24, 80, 80),
            itemCount: messages.length,
            itemBuilder: (_, i) => _ChatBubble(msg: messages[i]),
          ),
        ),
      ),
    );
  }
}

class _ChatBubble extends StatelessWidget {
  final _ChatMsg msg;
  const _ChatBubble({required this.msg});

  @override
  Widget build(BuildContext context) {
    if (msg.kind == _ChatMsgKind.loading) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(
          children: [
            const SizedBox(
              width: 12,
              height: 12,
              child: CircularProgressIndicator(strokeWidth: 1.5, color: Colors.white38),
            ),
            const SizedBox(width: 8),
            Text(msg.text,
                style: const TextStyle(color: Colors.white38, fontSize: 12)),
          ],
        ),
      );
    }
    if (msg.kind == _ChatMsgKind.image && msg.imageBytes != null) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(10),
          child: Image.memory(msg.imageBytes!,
              fit: BoxFit.cover,
              width: double.infinity,
              gaplessPlayback: true),
        ),
      );
    }
    if (msg.kind == _ChatMsgKind.video && msg.videoUrl != null) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: _VideoPlayer(url: msg.videoUrl!),
      );
    }
    if (msg.text.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Text(
        msg.text,
        style: TextStyle(
          color: msg.isUser
              ? Colors.greenAccent.withAlpha(200)
              : Colors.white.withAlpha(220),
          fontSize: 13,
          height: 1.5,
          letterSpacing: 0.1,
        ),
      ),
    );
  }
}

class _VideoPlayer extends StatefulWidget {
  final String url;
  const _VideoPlayer({required this.url});

  @override
  State<_VideoPlayer> createState() => _VideoPlayerState();
}

class _VideoPlayerState extends State<_VideoPlayer> {
  VideoPlayerController? _ctrl;
  bool _ready = false;

  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    final ctrl = VideoPlayerController.networkUrl(Uri.parse(widget.url));
    await ctrl.initialize();
    if (!mounted) { ctrl.dispose(); return; }
    setState(() { _ctrl = ctrl; _ready = true; });
    ctrl.play();
    ctrl.setLooping(true);
  }

  @override
  void dispose() {
    _ctrl?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!_ready || _ctrl == null) {
      return const AspectRatio(
        aspectRatio: 16 / 9,
        child: Center(child: CircularProgressIndicator(color: Colors.white38)),
      );
    }
    return ClipRRect(
      borderRadius: BorderRadius.circular(10),
      child: AspectRatio(
        aspectRatio: _ctrl!.value.aspectRatio,
        child: VideoPlayer(_ctrl!),
      ),
    );
  }
}

// ── Low-light banner ──────────────────────────────────────────────────────────

class _LowLightBanner extends StatelessWidget {
  const _LowLightBanner();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(
        color: Colors.orange.withAlpha(200),
        borderRadius: BorderRadius.circular(8),
      ),
      child: const Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.wb_sunny_outlined, color: Colors.white, size: 14),
          SizedBox(width: 6),
          Text('Low light — results may vary',
              style: TextStyle(color: Colors.white, fontSize: 12)),
        ],
      ),
    );
  }
}

// ── Mic FAB ───────────────────────────────────────────────────────────────────

class _MicFab extends StatelessWidget {
  final bool recording;
  final bool connected;
  final bool connecting;
  final bool playing;
  final AnimationController pulse;
  final VoidCallback onTap;

  const _MicFab({
    required this.recording,
    required this.connected,
    required this.connecting,
    required this.playing,
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
        builder: (_, child) => Transform.scale(
          scale: recording ? (1.0 + pulse.value * 0.08) : 1.0,
          child: child,
        ),
        child: Container(
          width: 56,
          height: 56,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: Colors.black.withAlpha(160),
            border: Border.all(color: color, width: 2),
            boxShadow: [
              BoxShadow(
                  color: color.withAlpha(80), blurRadius: 16, spreadRadius: 1)
            ],
          ),
          child: connecting
              ? const Center(
                  child: SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(
                        strokeWidth: 2, color: Colors.white54),
                  ),
                )
              : playing && !recording
                  ? const Center(
                      child: Icon(Icons.volume_up_rounded,
                          color: Colors.greenAccent, size: 22))
                  : Icon(
                      recording ? Icons.mic_off_rounded : Icons.mic_rounded,
                      color: color,
                      size: 26,
                    ),
        ),
      ),
    );
  }
}
