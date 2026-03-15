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
const String _kGatewayUrl =
    String.fromEnvironment('WEBSOCKET_GATEWAY_URL', defaultValue: '');
const String _kMapsApiKey =
    String.fromEnvironment('GOOGLE_MAPS_API_KEY', defaultValue: '');

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
const String _kModel = 'gemini-2.5-flash-native-audio-preview-12-2025';

String get _modelUri {
  if (_kProjectId.isNotEmpty) {
    return 'projects/$_kProjectId/locations/us-central1/publishers/google/models/$_kModel';
  }
  return 'models/$_kModel';
}

const _kPrefSubtitles = 'gps_subtitles';
const _kDirectionsApiUrl =
    'https://maps.googleapis.com/maps/api/directions/json';

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

  const _Landmark({required this.name, this.position, required this.discoveredAt});
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

  // ── Landmarks (discovered via [LOCATION:] tags) ────────────────────────────
  final List<_Landmark> _landmarks = [];
  _Landmark? _selectedLandmark;
  _DirectionsResult? _currentDirections;
  bool _loadingDirections = false;

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
                  'You are LORE GPS — an immersive AI walking tour guide and documentary narrator. '
                  'You receive the user\'s real-time GPS location as [GPS: lat=..., lon=..., accuracy=...m, address="..."] messages.\n\n'
                  'LOCATION IS ALWAYS FROM THE GPS MESSAGE — NEVER FROM YOUR TRAINING DATA.\n'
                  'The address field is the ground truth. If the address says "Chennai, Tamil Nadu, India" — '
                  'the user is in Chennai. Do NOT assume or guess a different city. '
                  'Do NOT use your training data defaults. The address in the message overrides everything.\n\n'
                  'GPS CONTEXT MESSAGES ARE SILENT:\n'
                  'When you receive a [GPS: ...] message, silently update your location awareness. '
                  'DO NOT speak or narrate unless:\n'
                  '1. The user asks you something (e.g. "where am I?", "what\'s nearby?", "tell me about this").\n'
                  '2. The user moves within ~50m of a truly significant landmark (famous monument, historic site, '
                  'major attraction) that you have NOT already narrated about this session.\n'
                  'For routine GPS updates — stay completely silent.\n\n'
                  'WHEN YOU DO NARRATE:\n'
                  'Use the address to identify the most interesting landmark, neighbourhood, or point of interest. '
                  'Lead with identity and significance. Be confident and specific. '
                  'Keep responses to 3-5 sentences — punchy and memorable. '
                  'Never repeat a location you already narrated this session.\n\n'
                  'NAVIGATION:\n'
                  'You do NOT provide turn-by-turn directions — the app handles that. '
                  'When the user asks to go somewhere — call navigate_to with the destination name.\n\n'
                  'LOCATION IDENTIFICATION:\n'
                  'Every time you narrate about a named place, include: [LOCATION: <name>]\n'
                  'This adds it to the map — it is critical.\n\n'
                  'TOOL USE RULES:\n'
                  '1. navigate_to: Call when user asks for directions or to go somewhere.\n\n'
                  'NEVER output <think>, <thinking>, or <tool_use> tags in your response.',
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

  void _registerLandmark(String name) {
    if (_landmarks.any((l) => l.name.toLowerCase() == name.toLowerCase())) return;
    final landmark = _Landmark(name: name, discoveredAt: DateTime.now());
    final landmarkIndex = _landmarks.length; // capture index before adding
    if (mounted && !_disposed) {
      setState(() => _landmarks.add(landmark));
    }
    if (_lastPosition != null) {
      final pos = LatLng(_lastPosition!.latitude, _lastPosition!.longitude);
      final markerId = MarkerId('landmark_$landmarkIndex');
      if (mounted && !_disposed) {
        setState(() {
          _markers.add(Marker(
            markerId: markerId,
            position: pos,
            infoWindow: InfoWindow(title: name),
            icon: BitmapDescriptor.defaultMarkerWithHue(BitmapDescriptor.hueOrange),
            onTap: () => _onLandmarkCardTap(landmark),
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
    _Landmark landmark;
    final existing = _landmarks.where((l) => l.name.toLowerCase() == destination.toLowerCase());
    if (existing.isNotEmpty) {
      landmark = existing.first;
    } else {
      landmark = _Landmark(name: destination, discoveredAt: DateTime.now());
      if (mounted && !_disposed) setState(() => _landmarks.add(landmark));
    }
    _onLandmarkCardTap(landmark);
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
            _markers.removeWhere((m) => m.markerId.value == 'destination');
            _markers.add(Marker(markerId: const MarkerId('destination'), position: polylinePoints.last, infoWindow: InfoWindow(title: landmark.name), icon: BitmapDescriptor.defaultMarkerWithHue(BitmapDescriptor.hueGreen)));
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
  }

  void _clearDirections() {
    setState(() {
      _selectedLandmark = null; _currentDirections = null; _polylines.clear();
      _markers.removeWhere((m) => m.markerId.value == 'destination');
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
          if (_recognisedLocation != null)
            Positioned(
              top: _gpsLost ? 172 : 116,
              left: 16,
              right: 80,
              child: _LocationChip(name: _recognisedLocation!),
            ),
          if (_landmarks.isNotEmpty)
            Positioned(
              top: _recognisedLocation != null ? (_gpsLost ? 216 : 160) : (_gpsLost ? 172 : 116),
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
    );
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
                              Icon(Icons.place,
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

// ── Location chip ─────────────────────────────────────────────────────────────

class _LocationChip extends StatelessWidget {
  final String name;
  const _LocationChip({required this.name});

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(20),
      child: BackdropFilter(
        filter: ImageFilter.blur(sigmaX: 8, sigmaY: 8),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          decoration: BoxDecoration(
            color: Colors.black.withAlpha(120),
            borderRadius: BorderRadius.circular(20),
            border: Border.all(color: Colors.white.withAlpha(50)),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.location_on,
                  color: Colors.greenAccent,
                  shadows: [Shadow(color: Colors.black45, blurRadius: 4)],
                  size: 14),
              const SizedBox(width: 5),
              Flexible(
                child: Text(
                  name,
                  style: const TextStyle(
                      color: Colors.white,
                      shadows: [Shadow(color: Colors.black45, blurRadius: 4)],
                      fontSize: 12,
                      fontWeight: FontWeight.w500),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ),
      ),
    );
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
