/// LoreMode — simultaneous camera + voice + GPS → Gemini Live API.
///
/// Combines Sight Mode (live camera frames) and Voice Mode (audio + chat +
/// image/video generation) into a single immersive documentary experience.
/// GPS coordinates are injected as text context so Gemini can enrich
/// narration with precise location awareness.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
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

// ── Config ────────────────────────────────────────────────────────────────────

const String _kExplicitProxyUrl =
    String.fromEnvironment('GEMINI_PROXY_URL', defaultValue: '');

String get _kDefaultProxyUrl {
  if (_kExplicitProxyUrl.isNotEmpty) return _kExplicitProxyUrl;
  return 'ws://10.0.2.2:8090';
}

const String _kProjectId = String.fromEnvironment('GCP_PROJECT_ID', defaultValue: '');
const String _kMapsApiKey = String.fromEnvironment('GOOGLE_MAPS_API_KEY', defaultValue: '');
const bool _kUseVertexAI = String.fromEnvironment('GOOGLE_GENAI_USE_VERTEXAI', defaultValue: 'false') == 'true';

const String _kIllustratorUrlOverride = String.fromEnvironment('NANO_ILLUSTRATOR_URL', defaultValue: '');

String get _kIllustratorUrl {
  if (_kIllustratorUrlOverride.isNotEmpty) return _kIllustratorUrlOverride;
  final host = Uri.parse(_kDefaultProxyUrl).host;
  return 'http://$host:8091/generate';
}


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
const _kPrefSubtitles = 'lore_lore_subtitles';

// ── Location tag parser (same pattern as SightMode) ──────────────────────────

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
          return _GeminiMsg(
            type: _GeminiMsgType.outputTranscription,
            text: outTrans['text'] as String? ?? '',
            textFinished: outTrans['finished'] as bool? ?? false,
          );
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
      if (data.containsKey('toolCall')) {
        return const _GeminiMsg(type: _GeminiMsgType.toolCall);
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
        'imageBase64':
            imageBytes != null ? base64Encode(imageBytes!) : null,
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
      imageBytes:
          b64 != null && b64.isNotEmpty ? base64Decode(b64) : null,
      imageMime: j['imageMime'] as String?,
      videoUrl: j['videoUrl'] as String?,
      kind: _ChatMsgKind.values.firstWhere(
        (e) => e.name == j['kind'],
        orElse: () => _ChatMsgKind.text,
      ),
      timestamp: DateTime.fromMillisecondsSinceEpoch(
          j['timestamp'] as int? ?? 0),
    );
  }
}

// ── Session persistence ───────────────────────────────────────────────────────

class _ChatStore {
  static const _currentKey = 'lore_lore_current_session';
  static const _sessionsKey = 'lore_lore_sessions';

  static Future<String> currentSessionId() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_currentKey) ?? await newSession();
  }

  static Future<String> newSession() async {
    final prefs = await SharedPreferences.getInstance();
    final id = 'lore_${DateTime.now().millisecondsSinceEpoch}';
    await prefs.setString(_currentKey, id);
    return id;
  }

  static Future<void> save(
      String sessionId, List<_ChatMsg> messages) async {
    final prefs = await SharedPreferences.getInstance();
    final toSave = messages
        .where((m) =>
            m.kind != _ChatMsgKind.loading &&
            (m.text.isNotEmpty ||
                m.imageBytes != null ||
                m.videoUrl != null))
        .toList();
    await prefs.setString(
      'lore_session_$sessionId',
      json.encode(toSave.map((m) => m.toJson()).toList()),
    );
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
    return prefs.getBool(_kPrefSubtitles) ?? true; // on by default in LoreMode
  }

  static Future<void> saveSubtitlePref(bool value) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_kPrefSubtitles, value);
  }
}


// ── Screen ────────────────────────────────────────────────────────────────────

class LoreModeScreen extends ConsumerStatefulWidget {
  const LoreModeScreen({super.key});

  @override
  ConsumerState<LoreModeScreen> createState() => _LoreModeScreenState();
}

class _LoreModeScreenState extends ConsumerState<LoreModeScreen>
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
  Timer? _frameTimer;

  // ── GPS ────────────────────────────────────────────────────────────────────
  StreamSubscription<Position>? _gpsSub;
  Position? _lastPosition;
  bool _gpsLost = false;
  Timer? _gpsLostTimer;

  // ── Mic ────────────────────────────────────────────────────────────────────
  final AudioRecorder _recorder = AudioRecorder();
  bool _recording = false;
  StreamSubscription? _recordSub;

  // ── PCM playback ───────────────────────────────────────────────────────────
  bool _pcmReady = false;
  bool _playing = false;
  final List<Uint8List> _feedQueue = [];
  bool _feeding = false;

  // ── Chat ───────────────────────────────────────────────────────────────────
  final List<_ChatMsg> _messages = [];
  final ScrollController _scrollCtrl = ScrollController();
  bool _lastUserMsgFinished = true;
  bool _lastAssistantMsgFinished = true;
  bool _showTranscript = true;

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
    final subtitlePref = await _ChatStore.loadSubtitlePref();
    if (mounted && !_disposed) setState(() => _showTranscript = subtitlePref);

    await _initPcm();
    _sessionId = await _ChatStore.currentSessionId();
    final saved = await _ChatStore.load(_sessionId);
    if (saved.isNotEmpty && mounted && !_disposed) {
      setState(() => _messages.addAll(saved));
    }

    await _initCameraAndStream();
    _initGps();
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
    final camStatus = await Permission.camera.request();
    final micStatus = await Permission.microphone.request();

    if (!camStatus.isGranted) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
              content: Text('Camera permission required for Lore Mode.')),
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

    await _connect();

    try {
      await _setupCompleter?.future.timeout(const Duration(seconds: 10));
    } catch (_) {}
    if (_disposed) return;

    _startVideoStream();
    if (micStatus.isGranted) await _startRecording();
  }

  void _initGps() async {
    final permission = await Geolocator.requestPermission();
    if (permission == LocationPermission.denied ||
        permission == LocationPermission.deniedForever) {
      if (mounted && !_disposed) setState(() => _gpsLost = true);
      return;
    }

    const settings = LocationSettings(
      accuracy: LocationAccuracy.high,
      distanceFilter: 10,
    );

    _gpsSub = Geolocator.getPositionStream(locationSettings: settings).listen(
      (pos) {
        final prev = _lastPosition;
        _lastPosition = pos;
        _gpsLostTimer?.cancel();
        if (mounted && !_disposed && _gpsLost) {
          setState(() => _gpsLost = false);
        }
        if (_connected) {
          final movedSignificantly = prev == null ||
              Geolocator.distanceBetween(prev.latitude, prev.longitude,
                      pos.latitude, pos.longitude) >
                  30;
          if (prev == null || movedSignificantly) {
            _sendGpsContext(pos);
          }
        }
      },
      onError: (_) {
        if (mounted && !_disposed) setState(() => _gpsLost = true);
      },
    );

    // Mark GPS as lost if no fix arrives within 10 seconds
    _gpsLostTimer = Timer(const Duration(seconds: 10), () {
      if (_lastPosition == null && mounted && !_disposed) {
        setState(() => _gpsLost = true);
      }
    });
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
          .get(url, headers: {'User-Agent': 'LoreLoreMode/1.0'}).timeout(
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

  void _startVideoStream() {
    _frameTimer?.cancel();
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
    _gpsLostTimer?.cancel();
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
          _setupCompleter?.completeError(e);
          _disconnectCleanup();
          if (mounted && !_disposed) setState(() {});
        },
        onDone: () {
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
                  'You are LORE, an immersive AI documentary narrator and expert guide. '
                  'You have access to a live camera feed and the user\'s voice simultaneously. '
                  'You also receive GPS coordinates as [GPS: lat=..., lon=..., accuracy=...m, address="..."] '
                  'context messages — use these silently to enrich your narration. Never read them out.\n\n'
                  'HOW TO RESPOND:\n'
                  'Combine what you see through the camera with what the user says to deliver rich, '
                  'cinematic documentary narration — like a BBC or National Geographic film about the '
                  'exact place they are standing in. Lead with identity and significance. '
                  'Name the subject confidently. Deliver the most compelling facts, history, and context '
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
                  'When the user asks "what if", "imagine if", or "suppose" — engage fully and creatively. '
                  'Explore the counterfactual with authority and vividness. Make it feel real. '
                  'Always call generate_image immediately after alternate history narration. '
                  'For dramatic scenarios involving motion, call generate_video instead.\n\n'
                  'HISTORICAL CHARACTERS:\n'
                  'At historically significant locations, you may briefly narrate in the voice of a '
                  'relevant historical figure to bring the place to life — always making clear it is '
                  'a dramatic interpretation. After speaking as a historical figure, call generate_image.\n\n'
                  'VISUAL STORYTELLING:\n'
                  'After every narration about a landmark, historical figure, natural wonder, architectural '
                  'marvel, civilisation, artwork, or cultural scene — call generate_image immediately. '
                  'After any alternate history response — call generate_image (or generate_video for motion). '
                  'After any historical character narration — call generate_image. '
                  'Skip generate_image only if you already generated one in the last 2 turns.\n\n'
                  'TOOL RULES:\n'
                  '1. generate_image — call after every visually rich narration, every alternate history '
                  'response, and every historical character moment. Also call when the user says "show", '
                  '"image", "picture", "draw", "illustrate", or "what does it look like". '
                  'Write a detailed cinematic prompt: subject, lighting, style, era, mood.\n'
                  '2. generate_video — call when the user says "video", "animate", "motion", "footage", '
                  '"clip", or "bring it to life". Also for dramatic alternate history involving movement, '
                  'battles, eruptions, migrations, storms, and ceremonies. '
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
                    'Generates a documentary-style illustration. Call when the user asks to see, '
                    'show, draw, or visualise something, or proactively after rich narration.',
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
        case _GeminiMsgType.audio:
          if (msg.audioBase64 != null && msg.audioBase64!.isNotEmpty) {
            _playPcmChunk(base64Decode(msg.audioBase64!));
          }
        case _GeminiMsgType.inputTranscription:
          if (msg.text != null && msg.text!.isNotEmpty) {
            _appendTranscript(msg.text!,
                isUser: true, finished: msg.textFinished ?? false);
          }
        case _GeminiMsgType.outputTranscription:
          if (msg.text != null && msg.text!.isNotEmpty) {
            _appendTranscript(msg.text!,
                isUser: false, finished: msg.textFinished ?? false);
          }
        case _GeminiMsgType.turnComplete:
          if (mounted && !_disposed) {
            setState(() {
              _playing = false;
              _lastUserMsgFinished = true;
              _lastAssistantMsgFinished = true;
            });
          }
          _saveSession();
        case _GeminiMsgType.interrupted:
          _stopPlayback();
          if (mounted && !_disposed) {
            setState(() {
              _lastUserMsgFinished = true;
              _lastAssistantMsgFinished = true;
            });
          }
        case _GeminiMsgType.toolCall:
          _handleToolCall(data);
        case _GeminiMsgType.unknown:
          break;
      }
    } catch (_) {}
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
    try {
      final resp = await http
          .post(
            Uri.parse(_kIllustratorUrl),
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

  // ── Mic ─────────────────────────────────────────────────────────────────────

  Future<void> _toggleMic() async {
    if (_disposed) return;
    if (!_connected) {
      await _connect();
      return;
    }
    if (_recording) {
      await _stopRecording();
      _wsSend({'realtime_input': {'audio_stream_end': true}});
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
              {
                'mime_type': 'audio/pcm;rate=16000',
                'data': base64Encode(chunk)
              }
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

  // ── Chat helpers ────────────────────────────────────────────────────────────

  void _appendTranscript(String rawText,
      {required bool isUser, required bool finished}) {
    if (!mounted || _disposed || rawText.trim().isEmpty) return;

    final displayText = isUser ? rawText : _stripLocationTag(rawText);
    if (displayText.isEmpty) return;

    setState(() {
      final lastFinished =
          isUser ? _lastUserMsgFinished : _lastAssistantMsgFinished;
      if (!lastFinished &&
          _messages.isNotEmpty &&
          _messages.last.isUser == isUser &&
          _messages.last.kind == _ChatMsgKind.text) {
        final existing = _messages.last.text;
        if (!isUser) {
          final needsSpace = existing.isNotEmpty &&
              !existing.endsWith(' ') &&
              !displayText.startsWith(' ');
          _messages.last.text =
              needsSpace ? '$existing $displayText' : '$existing$displayText';
        } else {
          _messages.last.text += displayText;
        }
        if (finished) {
          if (isUser) {
            _lastUserMsgFinished = true;
          } else {
            _lastAssistantMsgFinished = true;
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
          _lastAssistantMsgFinished = finished;
        }
      }
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

  void _wsSend(Map<String, dynamic> msg) {
    try {
      _ws?.sink.add(json.encode(msg));
    } catch (_) {}
  }

  Future<void> _saveSession() async {
    if (_sessionId.isEmpty) return;
    await _ChatStore.save(_sessionId, _messages);
  }

  Future<void> _startNewSession() async {
    await _stopRecording();
    await _stopPlayback();
    _disconnectCleanup();
    _sessionId = await _ChatStore.newSession();
    if (mounted && !_disposed) {
      setState(() {
        _messages.clear();
        _connected = false;
        _connecting = false;
        _lastUserMsgFinished = true;
        _lastAssistantMsgFinished = true;
      });
    }
    await Future.delayed(const Duration(milliseconds: 200));
    await _connect();
    try {
      await _setupCompleter?.future.timeout(const Duration(seconds: 10));
    } catch (_) {}
    if (!_disposed) {
      _startVideoStream();
      await _startRecording();
    }
  }

  void _toggleSubtitles() {
    final next = !_showTranscript;
    setState(() => _showTranscript = next);
    _ChatStore.saveSubtitlePref(next);
  }

  // ── Build ───────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(
        fit: StackFit.expand,
        children: [
          // ── Live camera preview ────────────────────────────────────────────
          if (_cameraReady && _cameraService.controller != null)
            _FullscreenCamera(controller: _cameraService.controller!)
          else
            const Center(
                child: CircularProgressIndicator(color: Colors.white54)),

          // ── Chat overlay (bottom half) ─────────────────────────────────────
          if (_messages.isNotEmpty)
            Positioned(
              left: 0,
              right: 0,
              bottom: 0,
              child: _ChatOverlay(
                messages: _messages,
                scrollCtrl: _scrollCtrl,
                visible: _showTranscript,
              ),
            ),

          // ── Top bar ────────────────────────────────────────────────────────
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
                    // New session
                    IconButton(
                      icon: const Icon(Icons.add_rounded,
                          size: 22, color: Colors.white70),
                      tooltip: 'New session',
                      onPressed: _startNewSession,
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

          // ── Status indicators (MIC / CAM / GPS) ───────────────────────────
          Positioned(
            top: 60,
            right: 12,
            child: SafeArea(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  _StatusChip(
                    icon: Icons.mic,
                    label: 'MIC',
                    active: _recording,
                  ),
                  const SizedBox(height: 6),
                  _StatusChip(
                    icon: Icons.camera_alt,
                    label: 'CAM',
                    active: _cameraReady,
                  ),
                  const SizedBox(height: 6),
                  _StatusChip(
                    icon: Icons.gps_fixed,
                    label: 'GPS',
                    active: !_gpsLost && _lastPosition != null,
                  ),
                ],
              ),
            ),
          ),

          // ── Low-light warning ──────────────────────────────────────────────
          if (_lowLight)
            const Positioned(
              top: 80,
              left: 16,
              right: 80,
              child: _LowLightBanner(),
            ),

          // ── GPS lost banner ────────────────────────────────────────────────
          if (_gpsLost)
            Positioned(
              top: _lowLight ? 120 : 80,
              left: 16,
              right: 80,
              child: const _GpsLostBanner(),
            ),

          // ── Mic FAB (bottom-right) ─────────────────────────────────────────
          Positioned(
            right: 16,
            bottom: 0,
            child: SafeArea(
              child: Padding(
                padding: const EdgeInsets.only(bottom: 24),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
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

// ── Chat overlay ──────────────────────────────────────────────────────────────

class _ChatOverlay extends StatelessWidget {
  final List<_ChatMsg> messages;
  final ScrollController scrollCtrl;
  final bool visible;

  const _ChatOverlay({
    required this.messages,
    required this.scrollCtrl,
    required this.visible,
  });

  @override
  Widget build(BuildContext context) {
    return AnimatedSlide(
      offset: visible ? Offset.zero : const Offset(0, 1),
      duration: const Duration(milliseconds: 250),
      curve: Curves.easeInOut,
      child: AnimatedOpacity(
        opacity: visible ? 1.0 : 0.0,
        duration: const Duration(milliseconds: 250),
        child: Container(
          constraints: BoxConstraints(
            maxHeight: MediaQuery.of(context).size.height * 0.45,
          ),
          decoration: const BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: [Colors.transparent, Color(0xCC000000)],
            ),
          ),
          child: ListView.builder(
            controller: scrollCtrl,
            padding:
                const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            itemCount: messages.length,
            itemBuilder: (_, i) => _ChatBubble(msg: messages[i]),
          ),
        ),
      ),
    );
  }
}

// ── Chat bubble ───────────────────────────────────────────────────────────────

class _ChatBubble extends StatelessWidget {
  final _ChatMsg msg;
  const _ChatBubble({required this.msg});

  @override
  Widget build(BuildContext context) {
    if (msg.kind == _ChatMsgKind.loading) {
      return _LoadingRow(label: msg.text);
    }
    if (msg.kind == _ChatMsgKind.image && msg.imageBytes != null) {
      return _ImageBubble(bytes: msg.imageBytes!, mime: msg.imageMime ?? 'image/png');
    }
    if (msg.kind == _ChatMsgKind.video && msg.videoUrl != null) {
      return _VideoBubble(url: msg.videoUrl!);
    }
    if (msg.text.isEmpty) return const SizedBox.shrink();

    return Align(
      alignment:
          msg.isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 3),
        padding:
            const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * 0.78),
        decoration: BoxDecoration(
          color: msg.isUser
              ? Colors.greenAccent.withAlpha(200)
              : Colors.black.withAlpha(140),
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(14),
            topRight: const Radius.circular(14),
            bottomLeft: Radius.circular(msg.isUser ? 14 : 4),
            bottomRight: Radius.circular(msg.isUser ? 4 : 14),
          ),
          border: Border.all(
            color: msg.isUser
                ? Colors.greenAccent
                : Colors.white.withAlpha(20),
          ),
        ),
        child: Text(
          msg.text,
          style: TextStyle(
            color: msg.isUser ? Colors.black : Colors.white,
            fontSize: 13,
            height: 1.4,
          ),
        ),
      ),
    );
  }
}

// ── Loading row ───────────────────────────────────────────────────────────────

class _LoadingRow extends StatefulWidget {
  final String label;
  const _LoadingRow({required this.label});

  @override
  State<_LoadingRow> createState() => _LoadingRowState();
}

class _LoadingRowState extends State<_LoadingRow>
    with SingleTickerProviderStateMixin {
  late AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
        vsync: this, duration: const Duration(milliseconds: 1200))
      ..repeat();
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          SizedBox(
            width: 16,
            height: 16,
            child: AnimatedBuilder(
              animation: _ctrl,
              builder: (_, __) => CircularProgressIndicator(
                value: null,
                strokeWidth: 1.5,
                color: Colors.greenAccent.withAlpha(180),
              ),
            ),
          ),
          const SizedBox(width: 10),
          Text(widget.label,
              style: const TextStyle(
                  color: Colors.white38,
                  fontSize: 12,
                  fontStyle: FontStyle.italic)),
        ],
      ),
    );
  }
}

// ── Themed dialog helper ──────────────────────────────────────────────────────

Future<void> _showLoreDialog(BuildContext context,
    {required String title, required String message}) {
  return showDialog(
    context: context,
    builder: (_) => AlertDialog(
      backgroundColor: const Color(0xFF0A1A0A),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(16),
        side: BorderSide(color: Colors.greenAccent.withAlpha(60)),
      ),
      title: Text(title,
          style: const TextStyle(
              color: Colors.white, fontWeight: FontWeight.w600)),
      content: Text(message,
          style: const TextStyle(color: Colors.white70, fontSize: 13)),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child:
              const Text('OK', style: TextStyle(color: Colors.greenAccent)),
        ),
      ],
    ),
  );
}

// ── Image bubble ──────────────────────────────────────────────────────────────

class _ImageBubble extends StatelessWidget {
  final Uint8List bytes;
  final String mime;
  const _ImageBubble({required this.bytes, required this.mime});

  @override
  Widget build(BuildContext context) {
    return Align(
      alignment: Alignment.centerLeft,
      child: GestureDetector(
        onTap: () => Navigator.of(context).push(
          MaterialPageRoute(
              builder: (_) => _FullscreenImagePage(bytes: bytes)),
        ),
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 6),
          constraints: BoxConstraints(
              maxWidth: MediaQuery.of(context).size.width * 0.82),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(12),
            border: Border.all(color: Colors.white.withAlpha(20)),
          ),
          clipBehavior: Clip.antiAlias,
          child: Stack(
            children: [
              Hero(
                  tag: bytes.hashCode,
                  child: Image.memory(bytes, fit: BoxFit.cover)),
              Positioned(
                bottom: 8,
                right: 8,
                child: Container(
                  padding: const EdgeInsets.all(4),
                  decoration: BoxDecoration(
                      color: Colors.black54,
                      borderRadius: BorderRadius.circular(6)),
                  child: const Icon(Icons.fullscreen_rounded,
                      color: Colors.white70, size: 18),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _FullscreenImagePage extends StatelessWidget {
  final Uint8List bytes;
  const _FullscreenImagePage({required this.bytes});

  Future<void> _saveToGallery(BuildContext context) async {
    try {
      await Gal.putImageBytes(bytes,
          name: 'lore_${DateTime.now().millisecondsSinceEpoch}.png');
      if (context.mounted) {
        await _showLoreDialog(context,
            title: 'Saved', message: 'Image saved to your gallery.');
      }
    } catch (e) {
      if (context.mounted) {
        await _showLoreDialog(context,
            title: 'Error', message: 'Could not save image: $e');
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: const Text('Image', style: TextStyle(fontSize: 15)),
        actions: [
          IconButton(
            icon: const Icon(Icons.download_rounded),
            tooltip: 'Save to gallery',
            onPressed: () => _saveToGallery(context),
          ),
        ],
      ),
      body: Center(
        child: InteractiveViewer(
          minScale: 0.5,
          maxScale: 5.0,
          child: Hero(
              tag: bytes.hashCode,
              child: Image.memory(bytes, fit: BoxFit.contain)),
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
      ..initialize()
          .then((_) {
            if (mounted) setState(() => _initialized = true);
          })
          .catchError((_) {
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
                child: Text('Video unavailable',
                    style: TextStyle(color: Colors.white38, fontSize: 12)))
            : !_initialized
                ? SizedBox(
                    height: width * 9 / 16,
                    child: const Center(
                        child: CircularProgressIndicator(
                            color: Colors.greenAccent, strokeWidth: 2)))
                : Stack(
                    children: [
                      AspectRatio(
                          aspectRatio: _ctrl.value.aspectRatio,
                          child: VideoPlayer(_ctrl)),
                      Positioned.fill(
                        child: GestureDetector(
                          onTap: () => setState(() => _ctrl.value.isPlaying
                              ? _ctrl.pause()
                              : _ctrl.play()),
                          child: AnimatedOpacity(
                            opacity: _ctrl.value.isPlaying ? 0.0 : 1.0,
                            duration: const Duration(milliseconds: 200),
                            child: Container(
                              color: Colors.black38,
                              child: const Center(
                                  child: Icon(Icons.play_arrow_rounded,
                                      color: Colors.white, size: 52)),
                            ),
                          ),
                        ),
                      ),
                      Positioned(
                        top: 8,
                        right: 8,
                        child: Row(
                          children: [
                            _VideoIconBtn(
                                icon: Icons.download_rounded,
                                onTap: () => _saveToGallery(context)),
                            const SizedBox(width: 6),
                            _VideoIconBtn(
                                icon: Icons.fullscreen_rounded,
                                onTap: () => _openFullscreen(context)),
                          ],
                        ),
                      ),
                      Positioned(
                        bottom: 0,
                        left: 0,
                        right: 0,
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
                    ],
                  ),
      ),
    );
  }

  void _openFullscreen(BuildContext context) {
    _ctrl.pause();
    Navigator.of(context).push(
        MaterialPageRoute(builder: (_) => _FullscreenVideoPage(url: widget.url)));
  }

  Future<void> _saveToGallery(BuildContext context) async {
    try {
      final resp = await http.get(Uri.parse(widget.url));
      if (resp.statusCode == 200) {
        final tmp = await getTemporaryDirectory();
        final file = File(
            '${tmp.path}/lore_${DateTime.now().millisecondsSinceEpoch}.mp4');
        await file.writeAsBytes(resp.bodyBytes);
        await Gal.putVideo(file.path);
        if (context.mounted) {
          await _showLoreDialog(context,
              title: 'Saved', message: 'Video saved to your gallery.');
        }
      } else {
        throw Exception('HTTP ${resp.statusCode}');
      }
    } catch (e) {
      if (context.mounted) {
        await _showLoreDialog(context,
            title: 'Error', message: 'Could not save video: $e');
      }
    }
  }
}

class _VideoIconBtn extends StatelessWidget {
  final IconData icon;
  final VoidCallback onTap;
  const _VideoIconBtn({required this.icon, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.all(5),
        decoration: BoxDecoration(
            color: Colors.black54,
            borderRadius: BorderRadius.circular(6)),
        child: Icon(icon, color: Colors.white70, size: 18),
      ),
    );
  }
}

class _FullscreenVideoPage extends StatefulWidget {
  final String url;
  const _FullscreenVideoPage({required this.url});

  @override
  State<_FullscreenVideoPage> createState() => _FullscreenVideoPageState();
}

class _FullscreenVideoPageState extends State<_FullscreenVideoPage> {
  late VideoPlayerController _ctrl;
  bool _initialized = false;

  @override
  void initState() {
    super.initState();
    SystemChrome.setPreferredOrientations([
      DeviceOrientation.landscapeLeft,
      DeviceOrientation.landscapeRight,
    ]);
    _ctrl = VideoPlayerController.networkUrl(Uri.parse(widget.url))
      ..initialize().then((_) {
        if (mounted) {
          setState(() => _initialized = true);
          _ctrl.play();
        }
      });
  }

  @override
  void dispose() {
    SystemChrome.setPreferredOrientations([DeviceOrientation.portraitUp]);
    _ctrl.dispose();
    super.dispose();
  }

  Future<void> _saveToGallery() async {
    try {
      final resp = await http.get(Uri.parse(widget.url));
      if (resp.statusCode == 200) {
        final tmp = await getTemporaryDirectory();
        final file = File(
            '${tmp.path}/lore_${DateTime.now().millisecondsSinceEpoch}.mp4');
        await file.writeAsBytes(resp.bodyBytes);
        await Gal.putVideo(file.path);
        if (mounted) {
          await _showLoreDialog(context,
              title: 'Saved', message: 'Video saved to your gallery.');
        }
      } else {
        throw Exception('HTTP ${resp.statusCode}');
      }
    } catch (e) {
      if (mounted) {
        await _showLoreDialog(context,
            title: 'Error', message: 'Could not save video: $e');
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(
        children: [
          Center(
            child: _initialized
                ? AspectRatio(
                    aspectRatio: _ctrl.value.aspectRatio,
                    child: VideoPlayer(_ctrl))
                : const CircularProgressIndicator(color: Colors.greenAccent),
          ),
          if (_initialized) ...[
            Positioned.fill(
              child: GestureDetector(
                onTap: () => setState(() =>
                    _ctrl.value.isPlaying ? _ctrl.pause() : _ctrl.play()),
              ),
            ),
            Positioned(
              bottom: 24,
              left: 16,
              right: 16,
              child: Row(
                children: [
                  IconButton(
                    icon: Icon(
                      _ctrl.value.isPlaying
                          ? Icons.pause_rounded
                          : Icons.play_arrow_rounded,
                      color: Colors.white,
                      size: 32,
                    ),
                    onPressed: () => setState(() => _ctrl.value.isPlaying
                        ? _ctrl.pause()
                        : _ctrl.play()),
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
                ],
              ),
            ),
            Positioned(
              top: 40,
              right: 8,
              child: Row(
                children: [
                  IconButton(
                    icon: const Icon(Icons.download_rounded,
                        color: Colors.white),
                    onPressed: _saveToGallery,
                  ),
                  IconButton(
                    icon: const Icon(Icons.close_rounded,
                        color: Colors.white, size: 28),
                    onPressed: () => Navigator.of(context).pop(),
                  ),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

// ── Status chip (MIC / CAM / GPS) ─────────────────────────────────────────────

class _StatusChip extends StatelessWidget {
  final IconData icon;
  final String label;
  final bool active;

  const _StatusChip({
    required this.icon,
    required this.label,
    required this.active,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: active
            ? Colors.greenAccent.withAlpha(160)
            : Colors.black.withAlpha(140),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(
          color: active
              ? Colors.greenAccent.withAlpha(100)
              : Colors.white.withAlpha(20),
        ),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, color: Colors.white, size: 12),
          const SizedBox(width: 4),
          Text(label,
              style: const TextStyle(
                  color: Colors.white,
                  fontSize: 10,
                  fontWeight: FontWeight.w600)),
        ],
      ),
    );
  }
}

// ── Banners ───────────────────────────────────────────────────────────────────

class _LowLightBanner extends StatelessWidget {
  const _LowLightBanner();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: Colors.orange.withAlpha(200),
        borderRadius: BorderRadius.circular(8),
      ),
      child: const Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.wb_sunny_outlined, color: Colors.white, size: 14),
          SizedBox(width: 6),
          Flexible(
            child: Text(
              'Low light — move to a brighter area for best results.',
              style: TextStyle(color: Colors.white, fontSize: 12),
            ),
          ),
        ],
      ),
    );
  }
}

class _GpsLostBanner extends StatelessWidget {
  const _GpsLostBanner();

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: Colors.red.withAlpha(200),
        borderRadius: BorderRadius.circular(8),
      ),
      child: const Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.gps_off, color: Colors.white, size: 14),
          SizedBox(width: 6),
          Flexible(
            child: Text(
              'GPS signal lost — describe your location verbally.',
              style: TextStyle(color: Colors.white, fontSize: 12),
            ),
          ),
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
