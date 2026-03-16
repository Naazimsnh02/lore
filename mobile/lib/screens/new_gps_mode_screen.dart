/// GPS Walking Tour — Gemini Live narration + Google Directions API navigation.
///
/// Architecture:
///   - Gemini Live API (port 8090): real-time audio narration
///   - GPS → silent [GPS:] text turns injected into Gemini session
///   - [LOCATION: name] tags extracted from narration → landmark cards + map markers
///   - Google Directions API: accurate turn-by-turn walking directions (called directly)
///   - GoogleMap: user position + landmark markers + route polyline
library;

import 'dart:async';
import 'dart:convert';
import 'dart:developer' as dev;
import 'dart:typed_data';
import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter_pcm_sound/flutter_pcm_sound.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:geolocator/geolocator.dart';
import 'package:google_maps_flutter/google_maps_flutter.dart';
import 'package:http/http.dart' as http;
import 'package:permission_handler/permission_handler.dart';
import 'package:record/record.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

// ── Config ────────────────────────────────────────────────────────────────────

const String _kExplicitProxyUrl =
    String.fromEnvironment('GEMINI_PROXY_URL', defaultValue: '');
const String _kMapsApiKey =
    String.fromEnvironment('GOOGLE_MAPS_API_KEY', defaultValue: '');

String get _kDefaultProxyUrl {
  if (_kExplicitProxyUrl.isNotEmpty) return _kExplicitProxyUrl;
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

const _kPrefSubtitles = 'gps_subtitles';
const _kDirectionsApiUrl =
    'https://maps.googleapis.com/maps/api/directions/json';
const _kNearbySearchUrl = 'https://places.googleapis.com/v1/places:searchNearby';

// ── Location tag parser ───────────────────────────────────────────────────────

final _locationTagRe = RegExp(r'\[LOCATION:\s*([^\]]+)\]');
final _thinkTagRe = RegExp(r'<think>.*?</think>', dotAll: true);

String? _extractLocation(String text) =>
    _locationTagRe.firstMatch(text)?.group(1)?.trim();

String _stripLocationTag(String text) =>
    text.replaceAll(_locationTagRe, '').replaceAll(_thinkTagRe, '').trim();

// ── Gemini message parsing ────────────────────────────────────────────────────

enum _GeminiMsgType {
  setupComplete, audio, inputTranscription, outputTranscription,
  toolCall, turnComplete, interrupted, unknown,
}

class _GeminiMsg {
  final _GeminiMsgType type;
  final String? audioBase64;
  final String? text;
  final bool? textFinished;

  const _GeminiMsg({required this.type, this.audioBase64, this.text, this.textFinished});

  factory _GeminiMsg.parse(Map<String, dynamic> data) {
    try {
      if (data.containsKey('setupComplete')) {
        return const _GeminiMsg(type: _GeminiMsgType.setupComplete);
      }
      final sc = data['serverContent'] as Map<String, dynamic>?;
      if (sc != null) {
        if (sc['turnComplete'] == true) return const _GeminiMsg(type: _GeminiMsgType.turnComplete);
        if (sc['interrupted'] == true) return const _GeminiMsg(type: _GeminiMsgType.interrupted);
        final inTrans = sc['inputTranscription'] as Map<String, dynamic>?;
        if (inTrans != null) return _GeminiMsg(type: _GeminiMsgType.inputTranscription, text: inTrans['text'] as String? ?? '', textFinished: inTrans['finished'] as bool? ?? false);
        final outTrans = sc['outputTranscription'] as Map<String, dynamic>?;
        if (outTrans != null) return _GeminiMsg(type: _GeminiMsgType.outputTranscription, text: outTrans['text'] as String? ?? '', textFinished: outTrans['finished'] as bool? ?? false);
        List<dynamic>? parts = (sc['modelTurn'] as Map<String, dynamic>?)?['parts'] as List<dynamic>?;
        parts ??= sc['parts'] as List<dynamic>?;
        if (parts != null) {
          for (final part in parts) {
            final p = part as Map<String, dynamic>;
            final audioData = (p['inlineData'] as Map<String, dynamic>?)?['data'] as String?;
            if (audioData != null && audioData.isNotEmpty) return _GeminiMsg(type: _GeminiMsgType.audio, audioBase64: audioData);
            final textPart = p['text'] as String?;
            if (textPart != null && textPart.isNotEmpty) return _GeminiMsg(type: _GeminiMsgType.outputTranscription, text: textPart, textFinished: false);
          }
        }
      }
      if (data.containsKey('toolCall')) return const _GeminiMsg(type: _GeminiMsgType.toolCall);
    } catch (_) {}
    return const _GeminiMsg(type: _GeminiMsgType.unknown);
  }
}

// ── Chat message model ────────────────────────────────────────────────────────

enum _ChatMsgKind { text, loading }

class _ChatMsg {
  final String id;
  final bool isUser;
  String text;
  final _ChatMsgKind kind;
  final DateTime timestamp;

  _ChatMsg({
    required this.id,
    required this.isUser,
    required this.text,
    required this.kind,
    DateTime? timestamp,
  }) : timestamp = timestamp ?? DateTime.now();

  Map<String, dynamic> toJson() => {
        'id': id,
        'isUser': isUser,
        'text': text,
        'kind': kind.name,
        'timestamp': timestamp.millisecondsSinceEpoch,
      };

  factory _ChatMsg.fromJson(Map<String, dynamic> j) {
    return _ChatMsg(
      id: j['id'] as String,
      isUser: j['isUser'] as bool,
      text: j['text'] as String? ?? '',
      kind: _ChatMsgKind.values.firstWhere((e) => e.name == j['kind'], orElse: () => _ChatMsgKind.text),
      timestamp: DateTime.fromMillisecondsSinceEpoch(j['timestamp'] as int? ?? 0),
    );
  }
}

// ── Landmark model ────────────────────────────────────────────────────────────

class _Landmark {
  final String name;
  final LatLng? position;
  final DateTime discoveredAt;
  final String? address;
  final double? rating;
  final int? userRatingCount;
  final String? primaryType;
  final List<String> types;
  final bool fromNearbySearch;

  const _Landmark({
    required this.name,
    this.position,
    required this.discoveredAt,
    this.address,
    this.rating,
    this.userRatingCount,
    this.primaryType,
    this.types = const [],
    this.fromNearbySearch = false,
  });
}

// ── Directions result ─────────────────────────────────────────────────────────

class _DirectionsResult {
  final String distanceText;
  final String durationText;
  final List<LatLng> polylinePoints;
  final List<String> steps;

  const _DirectionsResult({
    required this.distanceText,
    required this.durationText,
    required this.polylinePoints,
    required this.steps,
  });
}

// ── Session persistence ───────────────────────────────────────────────────────

class _ChatStore {
  static const _currentKey = 'gps_current_session';
  static const _sessionsKey = 'gps_sessions';

  static Future<String> currentSessionId() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_currentKey) ?? await newSession();
  }

  static Future<String> newSession() async {
    final prefs = await SharedPreferences.getInstance();
    final id = 'gps_${DateTime.now().millisecondsSinceEpoch}';
    await prefs.setString(_currentKey, id);
    return id;
  }

  static Future<void> save(String sessionId, List<_ChatMsg> messages) async {
    final prefs = await SharedPreferences.getInstance();
    final toSave = messages.where((m) => m.kind != _ChatMsgKind.loading && m.text.isNotEmpty).toList();
    await prefs.setString('gps_session_$sessionId', json.encode(toSave.map((m) => m.toJson()).toList()));
    final sessions = prefs.getStringList(_sessionsKey) ?? [];
    if (!sessions.contains(sessionId)) {
      sessions.add(sessionId);
      await prefs.setStringList(_sessionsKey, sessions);
    }
  }

  static Future<List<_ChatMsg>> load(String sessionId) async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString('gps_session_$sessionId');
    if (raw == null) return [];
    try {
      return (json.decode(raw) as List).map((e) => _ChatMsg.fromJson(e as Map<String, dynamic>)).toList();
    } catch (_) { return []; }
  }

  static Future<bool> loadSubtitlePref() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getBool(_kPrefSubtitles) ?? true;
  }

  static Future<void> saveSubtitlePref(bool value) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_kPrefSubtitles, value);
  }
}

// ── Screen ────────────────────────────────────────────────────────────────────

class NewGpsModeScreen extends ConsumerStatefulWidget {
  const NewGpsModeScreen({super.key});

  @override
  ConsumerState<NewGpsModeScreen> createState() => _NewGpsModeScreenState();
}

class _NewGpsModeScreenState extends ConsumerState<NewGpsModeScreen>
    with TickerProviderStateMixin {

  // ── WebSocket / Gemini ─────────────────────────────────────────────────────
  WebSocketChannel? _ws;
  StreamSubscription? _wsSub;
  bool _connected = false;
  bool _connecting = false;
  bool _disposed = false;
  Completer<void>? _setupCompleter;

  // ── GPS ────────────────────────────────────────────────────────────────────
  StreamSubscription<Position>? _gpsSub;
  Position? _lastPosition;
  bool _gpsLost = false;
  Timer? _gpsLostTimer;

  // ── Map ────────────────────────────────────────────────────────────────────
  GoogleMapController? _mapController;
  final Set<Marker> _markers = {};
  final Set<Polyline> _polylines = {};
  bool _followUser = true;
  Timer? _mapSnapshotTimer;

  // ── Landmarks (discovered via [LOCATION:] tags) ────────────────────────────
  final List<_Landmark> _landmarks = [];
  _Landmark? _selectedLandmark;
  _DirectionsResult? _currentDirections;
  bool _loadingDirections = false;
  bool _loadingNearby = false;
  LatLng? _lastFetchedCenter;

  // ── Mic ────────────────────────────────────────────────────────────────────
  final AudioRecorder _recorder = AudioRecorder();
  bool _recording = false;
  StreamSubscription? _recordSub;

  // ── PCM playback ───────────────────────────────────────────────────────────
  bool _pcmReady = false;
  bool _playing = false;
  final List<Uint8List> _feedQueue = [];
  bool _feeding = false;

  // ── Chat / transcript ──────────────────────────────────────────────────────
  final List<_ChatMsg> _messages = [];
  final ScrollController _scrollCtrl = ScrollController();
  bool _lastUserMsgFinished = true;
  bool _lastAssistantMsgFinished = true;
  bool _showTranscript = true;

  // ── Location display ───────────────────────────────────────────────────────
  String? _recognisedLocation;

  // ── Animation ─────────────────────────────────────────────────────────────
  late AnimationController _pulseCtrl;

  // ── Session ────────────────────────────────────────────────────────────────
  String _sessionId = '';

  @override
  void initState() {
    super.initState();
    _pulseCtrl = AnimationController(vsync: this, duration: const Duration(seconds: 1))
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

    await _initGpsOnly();
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

  Future<void> _initGpsOnly() async {
    dev.log('[GPS-MODE] init GPS only', name: 'GpsMode');
    final locPerm = await Permission.location.request();
    await Permission.microphone.request();

    if (!locPerm.isGranted) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Location permission required for GPS Walking Tour.')),
        );
      }
    } else {
      _startGpsStream();
    }
  }

  Future<void> _connectAndSendLocation() async {
    dev.log('[GPS-MODE] _connectAndSendLocation start', name: 'GpsMode');

    await _connect();
    try {
      await _setupCompleter?.future.timeout(const Duration(seconds: 10));
    } catch (e) {
      dev.log('[GPS-MODE] setup timeout/error: $e', name: 'GpsMode');
    }
    if (_disposed) return;

    if (_lastPosition == null) {
      final deadline = DateTime.now().add(const Duration(seconds: 15));
      while (_lastPosition == null && !_disposed && DateTime.now().isBefore(deadline)) {
        await Future.delayed(const Duration(milliseconds: 300));
      }
    }

    if (_lastPosition != null && _connected && !_disposed) {
      await _sendLocationContext(_lastPosition!);
    }

    _startGpsContextTimer();
    await _startRecording();
  }

  /// Reverse-geocodes [pos] using the Maps Geocoding API and returns a human-readable address.
  Future<String?> _reverseGeocode(Position pos) async {
    // Try Google Maps Geocoding API first
    if (_kMapsApiKey.isNotEmpty) {
      try {
        final url = Uri.parse(
          'https://maps.googleapis.com/maps/api/geocode/json'
          '?latlng=${pos.latitude},${pos.longitude}'
          '&key=$_kMapsApiKey',
        );
        final resp = await http.get(url).timeout(const Duration(seconds: 5));
        if (resp.statusCode == 200) {
          final data = json.decode(resp.body) as Map<String, dynamic>;
          dev.log('[GPS-MODE] geocode status: ${data['status']}', name: 'GpsMode');
          if (data['status'] == 'OK') {
            final results = data['results'] as List<dynamic>;
            if (results.isNotEmpty) {
              return (results.first as Map<String, dynamic>)['formatted_address'] as String?;
            }
          }
        }
      } catch (e) {
        dev.log('[GPS-MODE] Google geocode failed: $e', name: 'GpsMode');
      }
    }

    // Fallback: OpenStreetMap Nominatim (free, no key needed)
    try {
      final url = Uri.parse(
        'https://nominatim.openstreetmap.org/reverse'
        '?lat=${pos.latitude}&lon=${pos.longitude}&format=json',
      );
      final resp = await http.get(url, headers: {'User-Agent': 'LoreGPS/1.0'})
          .timeout(const Duration(seconds: 5));
      if (resp.statusCode == 200) {
        final data = json.decode(resp.body) as Map<String, dynamic>;
        final address = data['display_name'] as String?;
        dev.log('[GPS-MODE] Nominatim address: $address', name: 'GpsMode');
        return address;
      }
    } catch (e) {
      dev.log('[GPS-MODE] Nominatim geocode failed: $e', name: 'GpsMode');
    }

    return null;
  }

  /// Sends a [GPS: ...] context message to Gemini with coordinates + reverse-geocoded address.
  Future<void> _sendLocationContext(Position pos, {bool turnComplete = false}) async {
    final address = await _reverseGeocode(pos);
    final addressPart = address != null ? ', address="$address"' : '';
    final gpsTag = '[GPS: lat=${pos.latitude.toStringAsFixed(5)}, '
        'lon=${pos.longitude.toStringAsFixed(5)}, '
        'accuracy=${pos.accuracy.toStringAsFixed(0)}m$addressPart]';

    dev.log('[GPS-MODE] sending location tag: $gpsTag (turnComplete=$turnComplete)', name: 'GpsMode');
    _wsSend({
      'client_content': {
        'turns': [
          {
            'role': 'user',
            'parts': [{'text': gpsTag}]
          }
        ],
        'turn_complete': turnComplete,
      }
    });
  }

  Timer? _gpsContextTimer;

  void _startGpsContextTimer() {
    _gpsContextTimer?.cancel();
    _gpsContextTimer = Timer.periodic(const Duration(seconds: 30), (_) {
      _sendGpsContext();
    });
  }

  void _sendGpsContext({bool turnComplete = false}) {
    if (!_connected || _disposed || _lastPosition == null) return;
    _sendLocationContext(_lastPosition!, turnComplete: turnComplete);
  }

  /// Captures a snapshot of the current map view, resizes it to 512px wide,
  /// and sends it to Gemini as a realtime_input video frame.
  Future<void> _sendMapSnapshot() async {
    if (!_connected || _disposed || _mapController == null) return;
    try {
      final snapshot = await _mapController!.takeSnapshot();
      if (snapshot == null || snapshot.isEmpty || _disposed || !_connected) return;

      // Resize to 512px wide so the base64-encoded frame stays well under
      // the Gemini Live API 1 MB WebSocket frame limit.
      final codec = await instantiateImageCodec(snapshot, targetWidth: 512);
      final frame = await codec.getNextFrame();
      final byteData = await frame.image.toByteData(format: ImageByteFormat.png);
      frame.image.dispose();
      codec.dispose();

      if (byteData == null || _disposed || !_connected) return;
      final resized = byteData.buffer.asUint8List();

      _wsSend({
        'realtime_input': {
          'video': {'mime_type': 'image/png', 'data': base64Encode(resized)},
        }
      });
    } catch (e) {
      dev.log('[GPS-MODE] map snapshot failed: $e', name: 'GpsMode');
    }
  }

  void _startMapSnapshotTimer() {
    _mapSnapshotTimer?.cancel();
    // Send a map screenshot every 5 seconds so Gemini can see the current map state.
    // Max 1 FPS supported; 0.2 FPS (5s) is conservative and avoids hitting session limits.
    _mapSnapshotTimer = Timer.periodic(const Duration(seconds: 5), (_) {
      _sendMapSnapshot();
    });
  }

  void _startGpsStream() {
    dev.log('[GPS-MODE] starting GPS stream', name: 'GpsMode');
    const settings = LocationSettings(accuracy: LocationAccuracy.high, distanceFilter: 10);

    _gpsSub = Geolocator.getPositionStream(locationSettings: settings).listen(
      (pos) {
        final isFirstFix = _lastPosition == null;
        final prevPos = _lastPosition;
        _lastPosition = pos;
        _gpsLostTimer?.cancel();
        if (mounted && !_disposed && _gpsLost) setState(() => _gpsLost = false);

        if (isFirstFix && _mapController != null) {
          _mapController!.animateCamera(CameraUpdate.newLatLngZoom(LatLng(pos.latitude, pos.longitude), 16));
          _fetchNearbyLandmarks(pos);
        } else if (_followUser && _mapController != null) {
          _mapController!.animateCamera(CameraUpdate.newLatLng(LatLng(pos.latitude, pos.longitude)));
        }

        if (_connected) {
          final movedSignificantly = prevPos == null || Geolocator.distanceBetween(
            prevPos.latitude, prevPos.longitude, pos.latitude, pos.longitude,
          ) > 50;
          if (isFirstFix || movedSignificantly) {
            _sendLocationContext(pos, turnComplete: false);
          }
        }
      },
      onError: (e) {
        dev.log('[GPS-MODE] GPS stream error: $e', name: 'GpsMode');
        if (mounted && !_disposed) setState(() => _gpsLost = true);
      },
    );

    _gpsLostTimer = Timer(const Duration(seconds: 10), () {
      if (_lastPosition == null && mounted && !_disposed) {
        setState(() => _gpsLost = true);
      }
    });
  }

  @override
  void dispose() {
    _disposed = true;
    _gpsLostTimer?.cancel();
    _gpsContextTimer?.cancel();
    _mapSnapshotTimer?.cancel();
    _gpsSub?.cancel();
    _mapController?.dispose();
    _disconnectCleanup();
    _pulseCtrl.dispose();
    FlutterPcmSound.release();
    _scrollCtrl.dispose();
    super.dispose();
  }

  // ── Connection ──────────────────────────────────────────────────────────────

  Future<void> _connect() async {
    if (_disposed || _connecting || _connected) return;
    dev.log('[GPS-MODE] connecting to $_kDefaultProxyUrl', name: 'GpsMode');
    if (mounted) setState(() => _connecting = true);
    _setupCompleter = Completer<void>();
    try {
      if (!_pcmReady) await _initPcm();
      final ws = WebSocketChannel.connect(Uri.parse(_kDefaultProxyUrl));
      await ws.ready;
      if (_disposed) { ws.sink.close(); return; }
      _ws = ws;
      _ws!.sink.add(json.encode({'service_url': ''}));
      _wsSub = _ws!.stream.listen(
        _onMessage,
        onError: (e) {
          dev.log('[GPS-MODE] WS error: $e', name: 'GpsMode');
          _setupCompleter?.completeError(e);
          _disconnectCleanup();
          if (mounted && !_disposed) setState(() {});
        },
        onDone: () {
          dev.log('[GPS-MODE] WS closed', name: 'GpsMode');
          if (mounted && !_disposed) setState(() => _connected = false);
        },
      );
      _sendSetup();
      if (mounted && !_disposed) setState(() { _connected = true; _connecting = false; });
    } catch (e) {
      dev.log('[GPS-MODE] connect failed: $e', name: 'GpsMode');
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
            'voice_config': {'prebuilt_voice_config': {'voice_name': 'Aoede'}},
            'language_code': 'en-US',
          },
          'thinking_config': {'include_thoughts': false, 'thinking_budget': 0},
        },
        'system_instruction': {
          'parts': [
            {
              'text':
                  'You are LORE GPS, an AI walking tour guide. '
                  'You guide the user through their surroundings using real-time GPS location.\n\n'
                  'INPUTS YOU HAVE:\n'
                  '- GPS location as [GPS: lat=..., lon=..., accuracy=...m, address="..."] messages.\n'
                  '- The user\'s voice questions.\n'
                  '- A live screenshot of the Google Maps view, sent every 5 seconds as a video frame. '
                  'The map shows the user\'s blue dot position, nearby landmark markers, and any active route polyline. '
                  'Use the map to understand where the user is, what\'s around them, and what route they are on.\n'
                  '- You do not have camera access. You do not generate images or videos in this mode.\n\n'
                  'THE ADDRESS IS GROUND TRUTH:\n'
                  'The address field tells you exactly where the user is. If it says "Chennai, Tamil Nadu, India" — '
                  'the user is in Chennai. Never assume or guess a different location. '
                  'The address overrides anything from your training data.\n\n'
                  'WHEN TO SPEAK:\n'
                  'When you receive a [GPS: ...] message, silently update your location awareness. '
                  'Speak only when:\n'
                  '1. The user asks you something directly.\n'
                  '2. The user moves within about 50 metres of a truly significant landmark — a famous monument, '
                  'historic site, or major attraction — that you have not already narrated this session.\n'
                  'For all other GPS updates, stay completely silent. '
                  'If there is no clearly notable landmark nearby, stay silent until asked.\n\n'
                  'WHEN YOU NARRATE:\n'
                  'Name the place confidently. Deliver 2-4 sentences of compelling facts and context '
                  'a knowledgeable local would share. Never repeat a location you already narrated this session. '
                  'Always respond in English.\n\n'
                  'LOCATION TAGGING:\n'
                  'Every time you narrate about a named place, append [LOCATION: <name>] at the very end '
                  'of your response as metadata. Do not weave it into the spoken narration.\n\n'
                  'NAVIGATION:\n'
                  'You do not provide turn-by-turn directions — the app handles that. '
                  'When the user asks to go somewhere, call navigate_to with the destination name. '
                  'If the destination is ambiguous, ask one short clarifying question before calling navigate_to.\n\n'
                  'TOOL RULES:\n'
                  '1. navigate_to — call when the user asks for directions or to go somewhere.\n\n'
                  'Never output <think>, <thinking>, or <tool_use> tags.',
            }
          ],
        },
        'tools': [
          {
            'function_declarations': [
              {
                'name': 'navigate_to',
                'description': 'Shows walking directions to a destination on the map. Call when user asks to navigate, get directions, or go somewhere.',
                'parameters': {
                  'type': 'object',
                  'properties': {'destination': {'type': 'string', 'description': 'Name of the destination place.'}},
                  'required': ['destination'],
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
    _gpsContextTimer?.cancel();
    _mapSnapshotTimer?.cancel();
    _recorder.stop();
    _recordSub?.cancel(); _recordSub = null;
    _wsSub?.cancel(); _wsSub = null;
    _ws?.sink.close(); _ws = null;
    _connected = false; _recording = false; _pcmReady = false;
    _feedQueue.clear();
    _setupCompleter = null;
  }

  // ── Message handling ──────────────────────────────────────────────────────

  void _onMessage(dynamic raw) {
    try {
      final text = raw is Uint8List ? utf8.decode(raw) : raw as String;
      final data = json.decode(text) as Map<String, dynamic>;

      if (data.containsKey('toolCall')) {
        _handleToolCall(data); return;
      }

      final msg = _GeminiMsg.parse(data);
      switch (msg.type) {
        case _GeminiMsgType.setupComplete:
          _setupCompleter?.complete();
          _startMapSnapshotTimer();
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
            _handleOutputText(msg.text!, finished: msg.textFinished ?? false);
          }
        case _GeminiMsgType.turnComplete:
          if (mounted && !_disposed) {
            setState(() { _playing = false; _lastUserMsgFinished = true; _lastAssistantMsgFinished = true; });
          }
          _saveSession();
        case _GeminiMsgType.interrupted:
          _stopPlayback();
          if (mounted && !_disposed) {
            setState(() { _lastUserMsgFinished = true; _lastAssistantMsgFinished = true; });
          }
        case _GeminiMsgType.toolCall:
          _handleToolCall(data);
        case _GeminiMsgType.unknown:
          break;
      }
    } catch (e) {
      dev.log('[GPS-MODE] _onMessage error: $e', name: 'GpsMode');
    }
  }

  void _handleOutputText(String rawText, {required bool finished}) {
    final cleaned = rawText.replaceAll(_thinkTagRe, '').trim();
    if (cleaned.isEmpty) return;
    final locationName = _extractLocation(cleaned);
    if (locationName != null && locationName.isNotEmpty) {
      _registerLandmark(locationName);
      if (mounted && !_disposed) setState(() => _recognisedLocation = locationName);
    }
    _appendTranscript(cleaned, isUser: false, finished: finished);
  }

  Future<void> _fetchNearbyLandmarks(Position pos) =>
      _fetchLandmarksAtPosition(pos.latitude, pos.longitude);

  Future<void> _fetchLandmarksAtPosition(double lat, double lng) async {
    if (_kMapsApiKey.isEmpty || _loadingNearby) return;
    // Skip if we already fetched within 500 m of this centre
    if (_lastFetchedCenter != null) {
      final dist = Geolocator.distanceBetween(
          _lastFetchedCenter!.latitude, _lastFetchedCenter!.longitude, lat, lng);
      if (dist < 500) return;
    }
    _loadingNearby = true;
    _lastFetchedCenter = LatLng(lat, lng);

    try {
      final body = json.encode({
        'includedTypes': [
          'tourist_attraction',
          'museum',
          'historical_landmark',
          'cultural_landmark',
          'monument',
          'park',
          'art_gallery',
          'church',
          'hindu_temple',
          'mosque',
          'national_park',
          'zoo',
          'aquarium',
          'amusement_park',
          'castle',
          'historical_place',
        ],
        'maxResultCount': 20,
        'rankPreference': 'DISTANCE',
        'locationRestriction': {
          'circle': {
            'center': {
              'latitude': lat,
              'longitude': lng,
            },
            'radius': 2000.0,
          },
        },
      });

      final resp = await http.post(
        Uri.parse(_kNearbySearchUrl),
        headers: {
          'Content-Type': 'application/json',
          'X-Goog-Api-Key': _kMapsApiKey,
          'X-Goog-FieldMask':
              'places.displayName,places.formattedAddress,places.location,places.rating,places.userRatingCount,places.primaryType,places.types',
        },
        body: body,
      ).timeout(const Duration(seconds: 10));

      if (resp.statusCode != 200) {
        dev.log('[GPS-MODE] Nearby search failed: HTTP ${resp.statusCode} ${resp.body}', name: 'GpsMode');
        return;
      }

      final data = json.decode(resp.body) as Map<String, dynamic>;
      final places = data['places'] as List<dynamic>? ?? [];

      if (mounted && !_disposed) {
        setState(() {
          for (final place in places) {
            final p = place as Map<String, dynamic>;
            final displayName = (p['displayName'] as Map<String, dynamic>?)?['text'] as String? ?? '';
            if (displayName.isEmpty) continue;
            if (_landmarks.any((l) => l.name.toLowerCase() == displayName.toLowerCase())) continue;

            final location = p['location'] as Map<String, dynamic>?;
            final placeLat = location?['latitude'] as double?;
            final placeLng = location?['longitude'] as double?;
            final latLng = (placeLat != null && placeLng != null) ? LatLng(placeLat, placeLng) : null;

            final landmark = _Landmark(
              name: displayName,
              position: latLng,
              discoveredAt: DateTime.now(),
              address: p['formattedAddress'] as String?,
              rating: (p['rating'] as num?)?.toDouble(),
              userRatingCount: p['userRatingCount'] as int?,
              primaryType: p['primaryType'] as String?,
              types: (p['types'] as List<dynamic>?)?.cast<String>() ?? [],
              fromNearbySearch: true,
            );
            _landmarks.add(landmark);

            if (latLng != null) {
              final markerId = MarkerId('nearby_${_landmarks.length}');
              _markers.add(Marker(
                markerId: markerId,
                position: latLng,
                infoWindow: InfoWindow(title: displayName, snippet: landmark.address ?? ''),
                icon: BitmapDescriptor.defaultMarkerWithHue(BitmapDescriptor.hueAzure),
                onTap: () => _onMarkerTapped(landmark),
              ));
            }
          }
        });
      }
      dev.log('[GPS-MODE] Loaded ${places.length} nearby landmarks', name: 'GpsMode');
    } catch (e) {
      dev.log('[GPS-MODE] Nearby search error: $e', name: 'GpsMode');
    } finally {
      _loadingNearby = false;
    }
  }

  void _onMarkerTapped(_Landmark landmark) {
    if (!mounted || _disposed) return;
    setState(() => _selectedLandmark = landmark);
    _showLandmarkDetailSheet(landmark);
    _injectLandmarkContext(landmark);
  }

  /// Silently injects landmark metadata into the Gemini session so the AI
  /// can give a rich, accurate answer when the user asks about the tapped pin.
  void _injectLandmarkContext(_Landmark landmark) {
    if (!_connected || _disposed) return;
    final parts = <String>['User tapped "${landmark.name}" on the map.'];
    if (landmark.address != null) parts.add('Address: ${landmark.address}.');
    if (landmark.primaryType != null) {
      parts.add('Type: ${landmark.primaryType!.replaceAll('_', ' ')}.');
    }
    if (landmark.rating != null) {
      parts.add('Rating: ${landmark.rating}/5 (${landmark.userRatingCount ?? 0} reviews).');
    }
    final others = _landmarks
        .where((l) => l.name != landmark.name)
        .map((l) => l.name)
        .take(8)
        .join(', ');
    if (others.isNotEmpty) parts.add('Other visible landmarks on map: $others.');
    parts.add('Answer the user\'s next question with a detailed documentary-style response specifically about ${landmark.name}.');
    _wsSend({
      'client_content': {
        'turns': [
          {'role': 'user', 'parts': [{'text': '[MAP_CONTEXT: ${parts.join(' ')}]'}]}
        ],
        'turn_complete': false,
      }
    });
  }

  void _showLandmarkDetailSheet(_Landmark landmark) {
    showModalBottomSheet(
      context: context,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (_) => _LandmarkDetailSheet(
        landmark: landmark,
        onNavigate: () {
          Navigator.pop(context);
          _onLandmarkCardTap(landmark);
        },
        onClose: () => Navigator.pop(context),
      ),
    );
  }

  void _registerLandmark(String name) {
    if (_landmarks.any((l) => l.name.toLowerCase() == name.toLowerCase())) return;
    final landmark = _Landmark(
      name: name,
      position: _lastPosition != null
          ? LatLng(_lastPosition!.latitude, _lastPosition!.longitude)
          : null,
      discoveredAt: DateTime.now(),
    );
    final landmarkIndex = _landmarks.length;
    if (mounted && !_disposed) {
      setState(() => _landmarks.add(landmark));
    }
    if (landmark.position != null) {
      final markerId = MarkerId('landmark_$landmarkIndex');
      if (mounted && !_disposed) {
        setState(() {
          _markers.add(Marker(
            markerId: markerId,
            position: landmark.position!,
            infoWindow: InfoWindow(title: name),
            icon: BitmapDescriptor.defaultMarkerWithHue(BitmapDescriptor.hueOrange),
            onTap: () => _onMarkerTapped(landmark),
          ));
        });
      }
    }
  }

  void _handleToolCall(Map<String, dynamic> data) {
    final calls = (data['toolCall']?['functionCalls'] as List<dynamic>?) ?? [];
    for (final call in calls) {
      final c = call as Map<String, dynamic>;
      final name = c['name'] as String? ?? '';
      final id = c['id'] as String? ?? '';
      final args = c['args'] as Map<String, dynamic>? ?? {};

      if (name == 'navigate_to') {
        final destination = args['destination'] as String? ?? '';
        dev.log('[GPS-MODE] navigate_to: "$destination"', name: 'GpsMode');
        _wsSend({'tool_response': {'function_responses': [{'id': id, 'name': 'navigate_to', 'response': {'result': 'Directions to $destination are now shown on the map.'}}]}});
        _runNavigateTo(destination);
      } else {
        _wsSend({'tool_response': {'function_responses': [{'id': id, 'name': name, 'response': {'result': 'ok'}}]}});
      }
    }
  }

  void _runNavigateTo(String destination) {
    if (destination.isEmpty) return;
    final existing = _landmarks.where((l) => l.name.toLowerCase() == destination.toLowerCase());
    if (existing.isNotEmpty) {
      final landmark = existing.first;
      _onLandmarkCardTap(landmark);
      if (landmark.position != null) {
        _highlightLandmarkMarker(landmark);
      }
    } else {
      final landmark = _Landmark(name: destination, discoveredAt: DateTime.now());
      if (mounted && !_disposed) setState(() => _landmarks.add(landmark));
      _onLandmarkCardTap(landmark);
    }
  }

  void _highlightLandmarkMarker(_Landmark landmark) {
    if (landmark.position == null || !mounted || _disposed) return;
    setState(() {
      // Remove any old highlight marker
      _markers.removeWhere((m) => m.markerId.value == 'highlighted');
      _markers.add(Marker(
        markerId: const MarkerId('highlighted'),
        position: landmark.position!,
        infoWindow: InfoWindow(title: '📍 ${landmark.name}', snippet: 'Navigating here'),
        icon: BitmapDescriptor.defaultMarkerWithHue(BitmapDescriptor.hueRed),
        zIndexInt: 2,
        onTap: () => _onMarkerTapped(landmark),
      ));
    });
    _mapController?.showMarkerInfoWindow(const MarkerId('highlighted'));
    _mapController?.animateCamera(CameraUpdate.newLatLngZoom(landmark.position!, 16));
  }

  // ── Google Directions API ─────────────────────────────────────────────────

  Future<void> _fetchDirections(_Landmark landmark) async {
    if (_lastPosition == null || _kMapsApiKey.isEmpty) return;

    if (mounted && !_disposed) setState(() { _loadingDirections = true; _currentDirections = null; });

    try {
      final origin = '${_lastPosition!.latitude},${_lastPosition!.longitude}';
      final destination = Uri.encodeComponent(landmark.name);
      final url = Uri.parse('$_kDirectionsApiUrl?origin=$origin&destination=$destination&mode=walking&key=$_kMapsApiKey');
      final resp = await http.get(url).timeout(const Duration(seconds: 10));

      if (resp.statusCode != 200) throw Exception('HTTP ${resp.statusCode}');
      final data = json.decode(resp.body) as Map<String, dynamic>;
      if (data['status'] != 'OK') throw Exception('Directions API: ${data['status']}');

      final route = (data['routes'] as List).first as Map<String, dynamic>;
      final leg = (route['legs'] as List).first as Map<String, dynamic>;
      final distanceText = leg['distance']['text'] as String;
      final durationText = leg['duration']['text'] as String;
      final encodedPolyline = route['overview_polyline']['points'] as String;
      final polylinePoints = _decodePolyline(encodedPolyline);
      final steps = (leg['steps'] as List).map((s) {
        final html = (s as Map<String, dynamic>)['html_instructions'] as String? ?? '';
        return html.replaceAll(RegExp(r'<[^>]*>'), ' ').replaceAll(RegExp(r'\s+'), ' ').trim();
      }).toList();

      if (mounted && !_disposed) {
        setState(() {
          _currentDirections = _DirectionsResult(distanceText: distanceText, durationText: durationText, polylinePoints: polylinePoints, steps: steps);
          _loadingDirections = false;
          _polylines.clear();
          _polylines.add(Polyline(polylineId: const PolylineId('route'), points: polylinePoints, color: Colors.blueAccent, width: 5));
          if (polylinePoints.isNotEmpty) {
            _markers.removeWhere((m) => m.markerId.value == 'destination' || m.markerId.value == 'highlighted');
            _markers.add(Marker(
              markerId: const MarkerId('destination'),
              position: polylinePoints.last,
              infoWindow: InfoWindow(title: '📍 ${landmark.name}', snippet: '${_currentDirections?.distanceText ?? ''} · ${_currentDirections?.durationText ?? ''}'),
              icon: BitmapDescriptor.defaultMarkerWithHue(BitmapDescriptor.hueGreen),
              zIndexInt: 2,
              onTap: () => _onMarkerTapped(landmark),
            ));
          }
          _followUser = false;
        });
        if (_mapController != null && polylinePoints.isNotEmpty) {
          final bounds = _boundsFromLatLngList([LatLng(_lastPosition!.latitude, _lastPosition!.longitude), ...polylinePoints]);
          _mapController!.animateCamera(CameraUpdate.newLatLngBounds(bounds, 60));
        }
      }
    } catch (e) {
      if (mounted && !_disposed) setState(() => _loadingDirections = false);
    }
  }

  static List<LatLng> _decodePolyline(String encoded) {
    final points = <LatLng>[];
    int index = 0, len = encoded.length, lat = 0, lng = 0;
    while (index < len) {
      int b, shift = 0, result = 0;
      do { b = encoded.codeUnitAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
      lat += ((result & 1) != 0 ? ~(result >> 1) : (result >> 1));
      shift = 0; result = 0;
      do { b = encoded.codeUnitAt(index++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
      lng += ((result & 1) != 0 ? ~(result >> 1) : (result >> 1));
      points.add(LatLng(lat / 1e5, lng / 1e5));
    }
    return points;
  }

  LatLngBounds _boundsFromLatLngList(List<LatLng> points) {
    double minLat = points.first.latitude, maxLat = points.first.latitude;
    double minLng = points.first.longitude, maxLng = points.first.longitude;
    for (final p in points) {
      if (p.latitude < minLat) minLat = p.latitude; if (p.latitude > maxLat) maxLat = p.latitude;
      if (p.longitude < minLng) minLng = p.longitude; if (p.longitude > maxLng) maxLng = p.longitude;
    }
    return LatLngBounds(southwest: LatLng(minLat, minLng), northeast: LatLng(maxLat, maxLng));
  }

  void _onLandmarkCardTap(_Landmark landmark) {
    setState(() { _selectedLandmark = landmark; });
    _fetchDirections(landmark);
    if (landmark.position != null) {
      _highlightLandmarkMarker(landmark);
    }
  }

  void _clearDirections() {
    setState(() {
      _selectedLandmark = null; _currentDirections = null; _polylines.clear();
      _markers.removeWhere((m) => m.markerId.value == 'destination' || m.markerId.value == 'highlighted');
      _followUser = true;
    });
    if (_lastPosition != null && _mapController != null) {
      _mapController!.animateCamera(CameraUpdate.newLatLngZoom(LatLng(_lastPosition!.latitude, _lastPosition!.longitude), 16));
    }
  }

  // ── Audio recording ───────────────────────────────────────────────────────

  Future<void> _startRecording() async {
    try {
      final stream = await _recorder.startStream(const RecordConfig(encoder: AudioEncoder.pcm16bits, sampleRate: 16000, numChannels: 1));
      _recordSub = stream.listen((chunk) {
        if (!_connected || chunk.isEmpty) return;
        _wsSend({'realtime_input': {'media_chunks': [{'mime_type': 'audio/pcm;rate=16000', 'data': base64Encode(chunk)}]}});
      });
      if (mounted && !_disposed) setState(() => _recording = true);
    } catch (_) {}
  }

  Future<void> _stopRecording() async {
    await _recordSub?.cancel(); _recordSub = null;
    await _recorder.stop();
    if (mounted && !_disposed) setState(() => _recording = false);
  }

  Future<void> _toggleMic() async {
    if (_disposed) return;
    if (!_connected) { await _connectAndSendLocation(); return; }
    if (_recording) { await _stopRecording(); _wsSend({'realtime_input': {'audio_stream_end': true}}); }
    else { await _startRecording(); }
  }

  // ── Audio playback ────────────────────────────────────────────────────────

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
      try { await FlutterPcmSound.feed(PcmArrayInt16(bytes: chunk.buffer.asByteData(chunk.offsetInBytes, chunk.lengthInBytes))); } catch (_) {}
    }
    _feeding = false;
  }

  Future<void> _stopPlayback() async {
    _feedQueue.clear();
    try {
      await FlutterPcmSound.release();
      await FlutterPcmSound.setup(sampleRate: 24000, channelCount: 1);
    } catch (_) {}
    if (mounted && !_disposed) setState(() => _playing = false);
  }

  // ── Chat helpers ──────────────────────────────────────────────────────────

  void _appendTranscript(String rawText, {required bool isUser, required bool finished}) {
    if (!mounted || _disposed || rawText.trim().isEmpty) return;
    final displayText = isUser ? rawText : _stripLocationTag(rawText);
    if (displayText.isEmpty) return;

    setState(() {
      final lastFinished = isUser ? _lastUserMsgFinished : _lastAssistantMsgFinished;
      if (!lastFinished && _messages.isNotEmpty && _messages.last.isUser == isUser && _messages.last.kind == _ChatMsgKind.text) {
        final existing = _messages.last.text;
        if (!isUser) {
          final needsSpace = existing.isNotEmpty && !existing.endsWith(' ') && !displayText.startsWith(' ');
          _messages.last.text = needsSpace ? '$existing $displayText' : '$existing$displayText';
        } else {
          _messages.last.text += displayText;
        }
        if (finished) { if (isUser) { _lastUserMsgFinished = true; } else { _lastAssistantMsgFinished = true; } }
      } else {
        _messages.add(_ChatMsg(id: '${DateTime.now().microsecondsSinceEpoch}', isUser: isUser, text: displayText, kind: _ChatMsgKind.text));
        if (isUser) { _lastUserMsgFinished = finished; } else { _lastAssistantMsgFinished = finished; }
      }
    });
    _scrollToBottom();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_disposed && _scrollCtrl.hasClients) {
        _scrollCtrl.animateTo(_scrollCtrl.position.maxScrollExtent, duration: const Duration(milliseconds: 200), curve: Curves.easeOut);
      }
    });
  }

  void _wsSend(Map<String, dynamic> msg) {
    try {
      _ws?.sink.add(json.encode(msg));
    } catch (e) {
      dev.log('[GPS-MODE] _wsSend error: $e', name: 'GpsMode');
    }
  }

  Future<void> _saveSession() async {
    if (_sessionId.isEmpty) return;
    await _ChatStore.save(_sessionId, _messages);
  }

  Future<void> _startNewSession() async {
    await _stopRecording(); await _stopPlayback(); _disconnectCleanup();
    _sessionId = await _ChatStore.newSession();
    if (mounted && !_disposed) {
      setState(() {
        _messages.clear(); _landmarks.clear(); _markers.clear(); _polylines.clear();
        _selectedLandmark = null; _currentDirections = null; _recognisedLocation = null;
        _lastFetchedCenter = null;
        _connected = false; _connecting = false; _lastUserMsgFinished = true; _lastAssistantMsgFinished = true;
      });
    }
  }

  // ── Build ─────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: Stack(
        children: [
          _buildMap(),
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              child: ClipRRect(
                borderRadius: BorderRadius.circular(32),
                child: BackdropFilter(
                  filter: ImageFilter.blur(sigmaX: 12, sigmaY: 12),
                  child: Container(
                    decoration: BoxDecoration(
                      color: Colors.white.withAlpha(25),
                      borderRadius: BorderRadius.circular(32),
                      border: Border.all(color: Colors.white.withAlpha(30), width: 0.5),
                      boxShadow: [
                        BoxShadow(
                          color: Colors.black.withAlpha(40),
                          blurRadius: 12,
                          spreadRadius: 2,
                        ),
                      ],
                    ),
                    padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
                    child: Row(
                      children: [
                        IconButton(
                            icon: const Icon(Icons.arrow_back,
                                color: Colors.white,
                                shadows: [
                                  Shadow(color: Colors.black54, blurRadius: 4)
                                ]),
                            onPressed: () => Navigator.pop(context)),
                        const Spacer(),
                        GestureDetector(
                          onTap: () {
                            setState(() => _showTranscript = !_showTranscript);
                            _ChatStore.saveSubtitlePref(_showTranscript);
                          },
                          child: Icon(
                              _showTranscript
                                  ? Icons.subtitles
                                  : Icons.subtitles_off,
                              color: _showTranscript
                                  ? Colors.greenAccent
                                  : Colors.white,
                              shadows: const [
                                Shadow(color: Colors.black54, blurRadius: 4)
                              ],
                              size: 20),
                        ),
                        const SizedBox(width: 4),
                        IconButton(
                            icon: const Icon(Icons.refresh,
                                color: Colors.white,
                                shadows: [
                                  Shadow(color: Colors.black54, blurRadius: 4)
                                ],
                                size: 20),
                            tooltip: 'New session',
                            onPressed: _startNewSession),
                        const SizedBox(width: 4),
                        // Connection status dot
                        Padding(
                          padding: const EdgeInsets.only(right: 12),
                          child: Container(
                              width: 8,
                              height: 8,
                              decoration: BoxDecoration(
                                  shape: BoxShape.circle,
                                  boxShadow: const [
                                    BoxShadow(
                                        color: Colors.black26, blurRadius: 2)
                                  ],
                                  color: _connecting
                                      ? Colors.amber
                                      : _connected
                                          ? Colors.greenAccent
                                          : Colors.white54)),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
          if (_gpsLost) const Positioned(top: 116, left: 16, right: 16, child: _GpsLostBanner()),
          if (_landmarks.isNotEmpty)
            Positioned(
              top: _gpsLost ? 172 : 116,
              left: 0,
              right: 0,
              child: _LandmarkCarousel(landmarks: _landmarks, selected: _selectedLandmark, onTap: _onLandmarkCardTap),
            ),
          if (_selectedLandmark != null) Positioned(left: 12, right: 12, bottom: 110, child: _DirectionsPanel(landmark: _selectedLandmark!, directions: _currentDirections, loading: _loadingDirections, onClose: _clearDirections)),
          if (_showTranscript) Positioned(left: 0, right: 0, bottom: 0, child: _SubtitleOverlay(messages: _messages, scrollCtrl: _scrollCtrl)),
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
                      _recording ? 'listening...' : 'tap to start',
                      style: const TextStyle(color: Colors.white38, fontSize: 10, letterSpacing: 0.3),
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

  Widget _buildMap() {
    return GoogleMap(
      onMapCreated: (c) { _mapController = c; if (_lastPosition != null) { c.animateCamera(CameraUpdate.newLatLngZoom(LatLng(_lastPosition!.latitude, _lastPosition!.longitude), 16)); } },
      initialCameraPosition: CameraPosition(target: _lastPosition != null ? LatLng(_lastPosition!.latitude, _lastPosition!.longitude) : const LatLng(0, 0), zoom: _lastPosition != null ? 16.0 : 2.0),
      myLocationEnabled: true, myLocationButtonEnabled: false, markers: _markers, polylines: _polylines, mapType: MapType.normal,
      onCameraMoveStarted: () { if (_followUser && mounted && !_disposed) setState(() => _followUser = false); },
      onCameraIdle: _onCameraIdle,
    );
  }

  Future<void> _onCameraIdle() async {
    if (_mapController == null || _disposed) return;
    final region = await _mapController!.getVisibleRegion();
    final centerLat = (region.northeast.latitude + region.southwest.latitude) / 2;
    final centerLng = (region.northeast.longitude + region.southwest.longitude) / 2;
    _fetchLandmarksAtPosition(centerLat, centerLng);
  }
}

// ── Widgets ───────────────────────────────────────────────────────────────────

class _GpsLostBanner extends StatelessWidget {
  const _GpsLostBanner();
  @override
  Widget build(BuildContext context) {
    return Container(padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10), decoration: BoxDecoration(color: Colors.orange.withAlpha(204), borderRadius: BorderRadius.circular(8)),
      child: const Row(children: [Icon(Icons.gps_off, color: Colors.white, size: 18), SizedBox(width: 8), Expanded(child: Text('GPS signal lost. Narration paused.', style: TextStyle(color: Colors.white, fontSize: 13)))]));
  }
}

class _LandmarkCarousel extends StatelessWidget {
  final List<_Landmark> landmarks;
  final _Landmark? selected;
  final void Function(_Landmark) onTap;
  const _LandmarkCarousel(
      {required this.landmarks, required this.selected, required this.onTap});
  @override
  Widget build(BuildContext context) {
    return SizedBox(
        height: 38,
        child: ListView.builder(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            itemCount: landmarks.length,
            itemBuilder: (_, i) {
              final lm = landmarks[i];
              final isSelected = selected?.name == lm.name;
              return Padding(
                padding: const EdgeInsets.only(right: 8),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(20),
                  child: BackdropFilter(
                    filter: ImageFilter.blur(sigmaX: 8, sigmaY: 8),
                    child: GestureDetector(
                        onTap: () => onTap(lm),
                        child: Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 12, vertical: 6),
                            decoration: BoxDecoration(
                                color: isSelected
                                    ? Colors.greenAccent.withAlpha(80)
                                    : Colors.black.withAlpha(120),
                                borderRadius: BorderRadius.circular(20),
                                border: Border.all(
                                    color: isSelected
                                        ? Colors.greenAccent.withAlpha(160)
                                        : Colors.white.withAlpha(50),
                                    width: 1)),
                            child: Row(mainAxisSize: MainAxisSize.min, children: [
                              Icon(lm.fromNearbySearch ? Icons.near_me : Icons.place,
                                  color: isSelected
                                      ? Colors.white
                                      : Colors.white.withAlpha(200),
                                  shadows: const [
                                    Shadow(color: Colors.black45, blurRadius: 4)
                                  ],
                                  size: 12),
                              const SizedBox(width: 5),
                              Text(lm.name,
                                  style: TextStyle(
                                      color: Colors.white,
                                      shadows: const [
                                        Shadow(
                                            color: Colors.black45,
                                            blurRadius: 4)
                                      ],
                                      fontSize: 12,
                                      fontWeight: isSelected
                                          ? FontWeight.w600
                                          : FontWeight.normal))
                            ]))),
                  ),
                ),
              );
            }));
  }
}

class _DirectionsPanel extends StatelessWidget {
  final _Landmark landmark; final _DirectionsResult? directions; final bool loading; final VoidCallback onClose;
  const _DirectionsPanel({required this.landmark, required this.directions, required this.loading, required this.onClose});
  @override
  Widget build(BuildContext context) {
    return Container(padding: const EdgeInsets.all(14), decoration: BoxDecoration(color: Colors.black.withAlpha(230), borderRadius: BorderRadius.circular(12), border: Border.all(color: Colors.white12)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
        Row(children: [const Icon(Icons.directions_walk, color: Colors.blueAccent, size: 18), const SizedBox(width: 8), Expanded(child: Text(landmark.name, style: const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w600), overflow: TextOverflow.ellipsis)), IconButton(icon: const Icon(Icons.close, color: Colors.white54, size: 18), padding: EdgeInsets.zero, constraints: const BoxConstraints(), onPressed: onClose)]),
        if (loading) const Padding(padding: EdgeInsets.only(top: 10), child: Row(children: [SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.blueAccent)), SizedBox(width: 10), Text('Getting walking directions...', style: TextStyle(color: Colors.white54, fontSize: 13))]))
        else if (directions != null) ...[const SizedBox(height: 8), Row(children: [const Icon(Icons.straighten, color: Colors.white54, size: 14), const SizedBox(width: 6), Text(directions!.distanceText, style: const TextStyle(color: Colors.white, fontSize: 13)), const SizedBox(width: 16), const Icon(Icons.access_time, color: Colors.white54, size: 14), const SizedBox(width: 6), Text(directions!.durationText, style: const TextStyle(color: Colors.white, fontSize: 13))]),
          if (directions!.steps.isNotEmpty) ...[const SizedBox(height: 10), const Divider(color: Colors.white12, height: 1), const SizedBox(height: 8), ...directions!.steps.take(3).map((step) => Padding(padding: const EdgeInsets.only(bottom: 4), child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [const Icon(Icons.arrow_right, color: Colors.blueAccent, size: 16), const SizedBox(width: 4), Expanded(child: Text(step, style: const TextStyle(color: Colors.white70, fontSize: 12)))]))), if (directions!.steps.length > 3) Text('+ ${directions!.steps.length - 3} more steps', style: const TextStyle(color: Colors.white38, fontSize: 11))]],
      ]));
  }
}

class _SubtitleOverlay extends StatelessWidget {
  final List<_ChatMsg> messages; final ScrollController scrollCtrl;
  const _SubtitleOverlay({required this.messages, required this.scrollCtrl});
  @override
  Widget build(BuildContext context) {
    // Sit flush at the bottom — bottom padding accounts for SafeArea + mic FAB (56) + label (20) + spacing (24+5)
    final bottomPad = MediaQuery.of(context).padding.bottom + 56 + 20 + 30;
    return Container(
      constraints: BoxConstraints(maxHeight: MediaQuery.of(context).size.height * 0.35),
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [Colors.transparent, Color(0xCC000000)],
        ),
      ),
      child: ListView.builder(
        controller: scrollCtrl,
        padding: EdgeInsets.fromLTRB(16, 8, 16, bottomPad),
        itemCount: messages.length,
        itemBuilder: (_, i) => _ChatBubble(msg: messages[i]),
      ),
    );
  }
}

class _ChatBubble extends StatelessWidget {
  final _ChatMsg msg;
  const _ChatBubble({required this.msg});
  @override
  Widget build(BuildContext context) {
    return Align(alignment: msg.isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(margin: const EdgeInsets.symmetric(vertical: 3), padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8), constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.78),
        decoration: BoxDecoration(color: msg.isUser ? Colors.greenAccent.withAlpha(200) : Colors.black.withAlpha(140), borderRadius: BorderRadius.only(topLeft: const Radius.circular(14), topRight: const Radius.circular(14), bottomLeft: Radius.circular(msg.isUser ? 14 : 4), bottomRight: Radius.circular(msg.isUser ? 4 : 14)), border: Border.all(color: msg.isUser ? Colors.greenAccent : Colors.white.withAlpha(20))),
        child: Text(msg.text, style: TextStyle(color: msg.isUser ? Colors.black : Colors.white, fontSize: 13, height: 1.4))));
  }
}

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
              BoxShadow(color: color.withAlpha(80), blurRadius: 16, spreadRadius: 1),
            ],
          ),
          child: connecting
              ? const Center(
                  child: SizedBox(
                    width: 20,
                    height: 20,
                    child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white54),
                  ),
                )
              : playing && !recording
                  ? const Center(child: Icon(Icons.volume_up_rounded, color: Colors.greenAccent, size: 22))
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

class _LandmarkDetailSheet extends StatelessWidget {
  final _Landmark landmark;
  final VoidCallback onNavigate;
  final VoidCallback onClose;

  const _LandmarkDetailSheet({
    required this.landmark,
    required this.onNavigate,
    required this.onClose,
  });

  String _formatType(String? type) {
    if (type == null || type.isEmpty) return 'Place';
    return type.replaceAll('_', ' ').split(' ').map((w) => w.isNotEmpty ? '${w[0].toUpperCase()}${w.substring(1)}' : '').join(' ');
  }

  @override
  Widget build(BuildContext context) {
    final bottomPad = MediaQuery.of(context).padding.bottom;
    return Container(
      margin: const EdgeInsets.only(top: 80),
      decoration: const BoxDecoration(
        color: Color(0xFF1E1E1E),
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          // Drag handle
          Container(
            margin: const EdgeInsets.only(top: 12),
            width: 40,
            height: 4,
            decoration: BoxDecoration(
              color: Colors.white24,
              borderRadius: BorderRadius.circular(2),
            ),
          ),
          Padding(
            padding: EdgeInsets.fromLTRB(20, 20, 20, 16 + bottomPad),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Name
                Text(
                  landmark.name,
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 22,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                const SizedBox(height: 8),
                // Type badge
                if (landmark.primaryType != null || landmark.types.isNotEmpty)
                  Wrap(
                    spacing: 6,
                    runSpacing: 4,
                    children: [
                      if (landmark.primaryType != null)
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                          decoration: BoxDecoration(
                            color: Colors.blueAccent.withAlpha(40),
                            borderRadius: BorderRadius.circular(12),
                            border: Border.all(color: Colors.blueAccent.withAlpha(80)),
                          ),
                          child: Text(
                            _formatType(landmark.primaryType),
                            style: const TextStyle(color: Colors.blueAccent, fontSize: 12, fontWeight: FontWeight.w500),
                          ),
                        ),
                    ],
                  ),
                if (landmark.primaryType != null || landmark.types.isNotEmpty)
                  const SizedBox(height: 12),
                // Rating
                if (landmark.rating != null)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: Row(
                      children: [
                        Text(
                          landmark.rating!.toStringAsFixed(1),
                          style: const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w600),
                        ),
                        const SizedBox(width: 6),
                        ...List.generate(5, (i) {
                          final starValue = landmark.rating! - i;
                          return Icon(
                            starValue >= 0.75 ? Icons.star_rounded : starValue >= 0.25 ? Icons.star_half_rounded : Icons.star_border_rounded,
                            color: Colors.amber,
                            size: 18,
                          );
                        }),
                        if (landmark.userRatingCount != null) ...[
                          const SizedBox(width: 8),
                          Text(
                            '(${landmark.userRatingCount})',
                            style: const TextStyle(color: Colors.white54, fontSize: 13),
                          ),
                        ],
                      ],
                    ),
                  ),
                // Address
                if (landmark.address != null && landmark.address!.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 16),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Icon(Icons.location_on_outlined, color: Colors.white54, size: 18),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            landmark.address!,
                            style: const TextStyle(color: Colors.white70, fontSize: 13, height: 1.4),
                          ),
                        ),
                      ],
                    ),
                  ),
                // Coordinates
                if (landmark.position != null)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 16),
                    child: Row(
                      children: [
                        const Icon(Icons.my_location, color: Colors.white38, size: 16),
                        const SizedBox(width: 8),
                        Text(
                          '${landmark.position!.latitude.toStringAsFixed(5)}, ${landmark.position!.longitude.toStringAsFixed(5)}',
                          style: const TextStyle(color: Colors.white38, fontSize: 12, fontFamily: 'monospace'),
                        ),
                      ],
                    ),
                  ),
                // Navigate button
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton.icon(
                    onPressed: onNavigate,
                    icon: const Icon(Icons.directions_walk, size: 20),
                    label: const Text('Get Walking Directions'),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: Colors.blueAccent,
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                      textStyle: const TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
