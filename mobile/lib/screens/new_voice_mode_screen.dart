/// New VoiceMode screen — connects directly to the Gemini Live API proxy.
library;

import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;

import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_pcm_sound/flutter_pcm_sound.dart';
import 'package:gal/gal.dart';
import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';
import 'package:permission_handler/permission_handler.dart';
import 'package:record/record.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:video_player/video_player.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

// ── Config ────────────────────────────────────────────────────────────────────

const String _kExplicitProxyUrl = String.fromEnvironment('GEMINI_PROXY_URL', defaultValue: '');
const String _kGatewayUrl = String.fromEnvironment('WEBSOCKET_GATEWAY_URL', defaultValue: '');

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

const String _kProjectId = String.fromEnvironment('GCP_PROJECT_ID', defaultValue: '');
const String _kModel = 'gemini-2.5-flash-native-audio-preview-12-2025';

String get _modelUri {
  if (_kProjectId.isNotEmpty) {
    return 'projects/$_kProjectId/locations/us-central1/publishers/google/models/$_kModel';
  }
  return 'models/$_kModel';
}

// ── Gemini message parsing ────────────────────────────────────────────────────

enum _GeminiMsgType { setupComplete, audio, inputTranscription, outputTranscription, toolCall, turnComplete, interrupted, unknown }

class _GeminiMsg {
  final _GeminiMsgType type;
  final String? audioBase64;
  final String? text;
  final bool? textFinished;

  const _GeminiMsg({required this.type, this.audioBase64, this.text, this.textFinished});

  factory _GeminiMsg.parse(Map<String, dynamic> data) {
    try {
      if (data.containsKey('setupComplete')) return const _GeminiMsg(type: _GeminiMsgType.setupComplete);
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
      kind: _ChatMsgKind.values.firstWhere((e) => e.name == j['kind'], orElse: () => _ChatMsgKind.text),
      timestamp: DateTime.fromMillisecondsSinceEpoch(j['timestamp'] as int? ?? 0),
    );
  }
}

// ── Persistence ───────────────────────────────────────────────────────────────

class _ChatStore {
  static const _currentKey = 'lore_voice_current_session';
  static const _sessionsKey = 'lore_voice_sessions';

  static Future<String> currentSessionId() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString(_currentKey) ?? await newSession();
  }

  static Future<String> newSession() async {
    final prefs = await SharedPreferences.getInstance();
    final id = 'session_${DateTime.now().millisecondsSinceEpoch}';
    await prefs.setString(_currentKey, id);
    return id;
  }

  static Future<void> save(String sessionId, List<_ChatMsg> messages) async {
    final prefs = await SharedPreferences.getInstance();
    final toSave = messages.where((m) => m.kind != _ChatMsgKind.loading && (m.text.isNotEmpty || m.imageBytes != null || m.videoUrl != null)).toList();
    await prefs.setString('lore_session_$sessionId', json.encode(toSave.map((m) => m.toJson()).toList()));
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
      return (json.decode(raw) as List).map((e) => _ChatMsg.fromJson(e as Map<String, dynamic>)).toList();
    } catch (_) { return []; }
  }
}

// ── Screen ────────────────────────────────────────────────────────────────────

class NewVoiceModeScreen extends ConsumerStatefulWidget {
  const NewVoiceModeScreen({super.key});

  @override
  ConsumerState<NewVoiceModeScreen> createState() => _NewVoiceModeScreenState();
}

class _NewVoiceModeScreenState extends ConsumerState<NewVoiceModeScreen> with TickerProviderStateMixin {
  WebSocketChannel? _ws;
  StreamSubscription? _wsSub;
  bool _connected = false;
  bool _connecting = false;
  bool _disposed = false;

  final AudioRecorder _recorder = AudioRecorder();
  bool _recording = false;
  StreamSubscription? _recordSub;

  bool _pcmReady = false;
  bool _playing = false;
  final List<Uint8List> _feedQueue = [];
  bool _feeding = false;

  final List<_ChatMsg> _messages = [];
  final ScrollController _scrollCtrl = ScrollController();
  bool _lastUserMsgFinished = true;
  bool _lastAssistantMsgFinished = true;

  late AnimationController _waveCtrl;
  late AnimationController _pulseCtrl;

  String _sessionId = '';

  @override
  void initState() {
    super.initState();
    _waveCtrl = AnimationController(vsync: this, duration: const Duration(milliseconds: 1500))..repeat();
    _pulseCtrl = AnimationController(vsync: this, duration: const Duration(seconds: 1))..repeat(reverse: true);
    _loadSession(); // PCM init happens inside, before connect
  }

  Future<void> _loadSession() async {
    // Init PCM first — must be ready before first audio chunks arrive
    await _initPcm();
    _sessionId = await _ChatStore.currentSessionId();
    final saved = await _ChatStore.load(_sessionId);
    if (saved.isNotEmpty && mounted && !_disposed) setState(() => _messages.addAll(saved));
    _connect();
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
    _waveCtrl.dispose();
    _pulseCtrl.dispose();
    FlutterPcmSound.release();
    _scrollCtrl.dispose();
    super.dispose();
  }

  // ── Connection ──────────────────────────────────────────────────────────────

  Future<void> _connect() async {
    if (_disposed || _connecting || _connected) return;
    if (mounted) setState(() => _connecting = true);
    try {
      final ws = WebSocketChannel.connect(Uri.parse(_kDefaultProxyUrl));
      await ws.ready;
      if (_disposed) { ws.sink.close(); return; }
      _ws = ws;
      _ws!.sink.add(json.encode({'service_url': ''}));
      _wsSub = _ws!.stream.listen(
        _onMessage,
        onError: (_) { _disconnectCleanup(); if (mounted && !_disposed) setState(() {}); },
        onDone: () { if (mounted && !_disposed) setState(() => _connected = false); },
      );
      _sendSetup();
      if (mounted && !_disposed) setState(() { _connected = true; _connecting = false; });
    } catch (_) {
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
          'parts': [{'text':
            'You are LORE — an immersive AI documentary narrator and visual storyteller. '
            'LORE turns the world into a living, visual documentary experience. '
            'Users speak any topic — a landmark, historical event, scientific concept, '
            'culture, nature, architecture — and you deliver rich, cinematic documentary '
            'narration as if they are watching a high-quality BBC or National Geographic film. '
            'Be authoritative, vivid, and engaging. Use evocative language. '
            'Build narrative momentum — open with a compelling hook, develop the story, '
            'and leave the listener wanting more. '
            'Always respond in English regardless of the language spoken to you. '
            '\n\nVISUAL STORYTELLING — this is your most important behaviour:\n'
            'LORE is a VISUAL documentary experience. You MUST proactively generate images '
            'to accompany your narration. After delivering narration about any visually '
            'compelling subject — a landmark, historical figure, natural wonder, architectural '
            'marvel, battle, civilisation, animal, celestial body, artwork, or cultural scene — '
            'you MUST call generate_image immediately after speaking, without waiting to be asked. '
            'Think of it as: you narrate, then you show. Every story deserves a visual. '
            'Do not generate an image if you already generated one in the last 2 turns.\n\n'
            'TOOL USE RULES — follow these exactly:\n'
            '1. generate_image: Call this proactively after narrating any visually rich topic. '
            'Also call it whenever the user says "show", "image", "picture", "draw", '
            '"illustrate", "what does it look like", or any similar visual request. '
            'Craft a detailed, cinematic prompt — include lighting, style, era, mood. '
            'Do NOT just describe — CALL THE FUNCTION.\n'
            '2. generate_video: Call this whenever the user says "video", "animate", '
            '"motion", "footage", "clip", "bring it to life", "show me a video", '
            'or any similar motion request. Also proactively offer video for dramatic '
            'moments: battles, eruptions, migrations, storms, ceremonies. '
            'Before calling, say: "Generating your video now — this takes about 60 to 90 seconds." '
            'Then CALL THE FUNCTION immediately.\n\n'
            'CRITICAL: When a tool is needed, call it — do not just narrate instead. '
            'Do NOT output <think>, <thinking>, or <tool_use> tags.',
          }],
        },
        'tools': [{'function_declarations': [
          {
            'name': 'generate_image',
            'description': 'Generates a documentary-style illustration. Call when the user asks to see, show, draw, or visualise something.',
            'parameters': {'type': 'object', 'properties': {'prompt': {'type': 'string', 'description': 'Detailed image generation prompt.'}}, 'required': ['prompt']},
          },
          {
            'name': 'generate_video',
            'description': 'Generates a short cinematic video clip (8 seconds). Call when the user asks for a video or animation. Takes 60-90 seconds.',
            'parameters': {'type': 'object', 'properties': {'prompt': {'type': 'string', 'description': 'Detailed video generation prompt.'}}, 'required': ['prompt']},
          },
        ]}],
        'input_audio_transcription': {},
        'output_audio_transcription': {},
        'realtime_input_config': {
          'automatic_activity_detection': {'disabled': false, 'silence_duration_ms': 1000, 'prefix_padding_ms': 500},
          'activity_handling': 'START_OF_ACTIVITY_INTERRUPTS',
        },
      },
    });
  }

  void _disconnectCleanup() {
    _recorder.stop();
    _recordSub?.cancel(); _recordSub = null;
    _wsSub?.cancel(); _wsSub = null;
    _ws?.sink.close(); _ws = null;
    _connected = false; _recording = false;
    _feedQueue.clear();
    try { FlutterPcmSound.release(); } catch (_) {}
  }

  // ── Message handling ─────────────────────────────────────────────────────────

  void _onMessage(dynamic raw) {
    try {
      final text = raw is Uint8List ? utf8.decode(raw) : raw as String;
      final data = json.decode(text) as Map<String, dynamic>;
      if (data.containsKey('toolCall')) { _handleToolCall(data); return; }
      final msg = _GeminiMsg.parse(data);
      switch (msg.type) {
        case _GeminiMsgType.setupComplete:
          break; // green dot is enough feedback
        case _GeminiMsgType.audio:
          if (msg.audioBase64 != null && msg.audioBase64!.isNotEmpty) _playPcmChunk(base64Decode(msg.audioBase64!));
        case _GeminiMsgType.inputTranscription:
          if (msg.text != null && msg.text!.isNotEmpty) _appendTranscript(msg.text!, isUser: true, finished: msg.textFinished ?? false);
        case _GeminiMsgType.outputTranscription:
          if (msg.text != null && msg.text!.isNotEmpty) _appendTranscript(msg.text!, isUser: false, finished: msg.textFinished ?? false);
        case _GeminiMsgType.turnComplete:
          if (mounted && !_disposed) setState(() { _playing = false; _lastUserMsgFinished = true; _lastAssistantMsgFinished = true; });
          _saveSession();
        case _GeminiMsgType.interrupted:
          _stopPlayback();
          if (mounted && !_disposed) setState(() { _lastUserMsgFinished = true; _lastAssistantMsgFinished = true; });
        case _GeminiMsgType.toolCall:
          _handleToolCall(data);
        case _GeminiMsgType.unknown:
          break;
      }
    } catch (_) {}
  }

  // ── Tool calls ──────────────────────────────────────────────────────────────

  void _handleToolCall(Map<String, dynamic> data) {
    final calls = (data['toolCall']?['functionCalls'] as List<dynamic>?) ?? [];
    for (final call in calls) {
      final c = call as Map<String, dynamic>;
      final name = c['name'] as String? ?? '';
      final id = c['id'] as String? ?? '';
      final prompt = (c['args'] as Map<String, dynamic>?)?['prompt'] as String? ?? '';
      if (name == 'generate_image') {
        final loadingId = _addLoadingMsg('Generating image...');
        _runGenerateImage(id, prompt, loadingId);
      } else if (name == 'generate_video') {
        final loadingId = _addLoadingMsg('Generating video — this takes ~60-90s...');
        _runGenerateVideo(id, prompt, loadingId);
      }
    }
  }

  /// Adds an inline loading indicator row and returns its id.
  String _addLoadingMsg(String label) {
    final id = 'loading_${DateTime.now().microsecondsSinceEpoch}';
    if (mounted && !_disposed) {
      setState(() => _messages.add(_ChatMsg(id: id, isUser: false, text: label, kind: _ChatMsgKind.loading)));
    }
    _scrollToBottom();
    return id;
  }

  void _removeLoadingMsg(String id) {
    if (mounted && !_disposed) setState(() => _messages.removeWhere((m) => m.id == id));
  }

  Future<void> _runGenerateImage(String callId, String prompt, String loadingId) async {
    final host = Uri.parse(_kDefaultProxyUrl).host;
    try {
      final resp = await http.post(
        Uri.parse('http://$host:8091/generate'),
        headers: {'Content-Type': 'application/json'},
        body: json.encode({'prompt': prompt}),
      ).timeout(const Duration(seconds: 60));

      _removeLoadingMsg(loadingId);

      if (resp.statusCode == 200) {
        final body = json.decode(resp.body) as Map<String, dynamic>;
        final b64 = body['image_base64'] as String?;
        final mime = body['mime_type'] as String? ?? 'image/png';
        if (b64 != null && b64.isNotEmpty) {
          final msg = _ChatMsg(id: '${DateTime.now().microsecondsSinceEpoch}', isUser: false, text: '', imageBytes: base64Decode(b64), imageMime: mime, kind: _ChatMsgKind.image);
          if (mounted && !_disposed) setState(() => _messages.add(msg));
          _scrollToBottom();
          _saveSession();
          _wsSend({'tool_response': {'function_responses': [{'id': callId, 'name': 'generate_image', 'response': {'result': 'Image generated successfully.'}}]}});
          return;
        }
      }
      throw Exception('HTTP ${resp.statusCode}');
    } catch (e) {
      _removeLoadingMsg(loadingId);
      _wsSend({'tool_response': {'function_responses': [{'id': callId, 'name': 'generate_image', 'response': {'error': e.toString()}}]}});
    }
  }

  Future<void> _runGenerateVideo(String callId, String prompt, String loadingId) async {
    final host = Uri.parse(_kDefaultProxyUrl).host;
    try {
      final resp = await http.post(
        Uri.parse('http://$host:8092/generate'),
        headers: {'Content-Type': 'application/json'},
        body: json.encode({'prompt': prompt}),
      ).timeout(const Duration(minutes: 4));

      _removeLoadingMsg(loadingId);

      if (resp.statusCode == 200) {
        final body = json.decode(resp.body) as Map<String, dynamic>;
        final videoUrl = body['video_url'] as String?;
        if (videoUrl != null && videoUrl.isNotEmpty) {
          final msg = _ChatMsg(id: '${DateTime.now().microsecondsSinceEpoch}', isUser: false, text: '', videoUrl: videoUrl, kind: _ChatMsgKind.video);
          if (mounted && !_disposed) setState(() => _messages.add(msg));
          _scrollToBottom();
          _saveSession();
          _wsSend({'tool_response': {'function_responses': [{'id': callId, 'name': 'generate_video', 'response': {'result': 'Video generated successfully.'}}]}});
          return;
        }
      }
      throw Exception('HTTP ${resp.statusCode}');
    } catch (e) {
      _removeLoadingMsg(loadingId);
      _wsSend({'tool_response': {'function_responses': [{'id': callId, 'name': 'generate_video', 'response': {'error': e.toString()}}]}});
    }
  }

  // ── Audio recording ─────────────────────────────────────────────────────────

  Future<void> _toggleMic() async {
    if (_disposed) return;
    if (!_connected) { await _connect(); return; }
    if (_recording) {
      await _stopRecording();
      _wsSend({'realtime_input': {'audio_stream_end': true}});
    } else {
      await _startRecording();
    }
  }

  Future<void> _startRecording() async {
    final status = await Permission.microphone.request();
    if (!status.isGranted) return;
    try {
      final stream = await _recorder.startStream(const RecordConfig(
        encoder: AudioEncoder.pcm16bits, sampleRate: 16000, numChannels: 1,
        noiseSuppress: true, echoCancel: true, autoGain: true,
      ));
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
      try { await FlutterPcmSound.feed(PcmArrayInt16(bytes: chunk.buffer.asByteData(chunk.offsetInBytes, chunk.lengthInBytes))); } catch (_) {}
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

  void _appendTranscript(String text, {required bool isUser, required bool finished}) {
    if (!mounted || _disposed || text.trim().isEmpty) return;
    setState(() {
      final lastFinished = isUser ? _lastUserMsgFinished : _lastAssistantMsgFinished;
      if (!lastFinished && _messages.isNotEmpty && _messages.last.isUser == isUser && _messages.last.kind == _ChatMsgKind.text) {
        _messages.last.text += text;
        if (finished) {
          if (isUser) { _lastUserMsgFinished = true; } else { _lastAssistantMsgFinished = true; }
        }
      } else {
        _messages.add(_ChatMsg(id: '${DateTime.now().microsecondsSinceEpoch}', isUser: isUser, text: text, kind: _ChatMsgKind.text));
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
    try { _ws?.sink.add(json.encode(msg)); } catch (_) {}
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
    if (mounted && !_disposed) setState(() { _messages.clear(); _connected = false; _connecting = false; _lastUserMsgFinished = true; _lastAssistantMsgFinished = true; });
    await Future.delayed(const Duration(milliseconds: 200));
    _connect();
  }

  // ── Build ───────────────────────────────────────────────────────────────────

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF0A1A0A),
      appBar: AppBar(
        backgroundColor: const Color(0xFF0A1A0A),
        foregroundColor: Colors.white,
        centerTitle: true,
        title: const Text('Voice Mode', style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600, letterSpacing: 1)),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 4),
            child: Center(
              child: Container(
                width: 7, height: 7,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: _connecting ? Colors.amber : _connected ? Colors.greenAccent : Colors.white24,
                ),
              ),
            ),
          ),
          IconButton(icon: const Icon(Icons.add_rounded, size: 22), tooltip: 'New session', onPressed: _startNewSession),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: _messages.isEmpty
                ? const _EmptyState()
                : ListView.builder(
                    controller: _scrollCtrl,
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                    itemCount: _messages.length,
                    itemBuilder: (_, i) => _ChatBubble(msg: _messages[i]),
                  ),
          ),
          _WaveformBar(active: _recording, animation: _waveCtrl),
          _MicButton(
            recording: _recording, connected: _connected, connecting: _connecting,
            playing: _playing, pulse: _pulseCtrl, onTap: _toggleMic,
          ),
          const SizedBox(height: 28),
        ],
      ),
    );
  }
}

// ── Widgets ───────────────────────────────────────────────────────────────────

class _EmptyState extends StatelessWidget {
  const _EmptyState();
  @override
  Widget build(BuildContext context) {
    return const Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.mic_none, color: Colors.white12, size: 72),
          SizedBox(height: 16),
          Text('Tap the mic to begin', textAlign: TextAlign.center, style: TextStyle(color: Colors.white24, fontSize: 15, letterSpacing: 0.5)),
          SizedBox(height: 6),
          Text('Ask LORE about anything', textAlign: TextAlign.center, style: TextStyle(color: Colors.white12, fontSize: 12)),
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
    if (msg.kind == _ChatMsgKind.loading) return _LoadingRow(label: msg.text);
    if (msg.kind == _ChatMsgKind.image && msg.imageBytes != null) return _ImageBubble(bytes: msg.imageBytes!, mime: msg.imageMime ?? 'image/png');
    if (msg.kind == _ChatMsgKind.video && msg.videoUrl != null) return _VideoBubble(url: msg.videoUrl!);
    if (msg.text.isEmpty) return const SizedBox.shrink();

    return Align(
      alignment: msg.isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.78),
        decoration: BoxDecoration(
          color: msg.isUser ? Colors.greenAccent.withAlpha(40) : Colors.white.withAlpha(12),
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(16), topRight: const Radius.circular(16),
            bottomLeft: Radius.circular(msg.isUser ? 16 : 4),
            bottomRight: Radius.circular(msg.isUser ? 4 : 16),
          ),
          border: Border.all(color: msg.isUser ? Colors.greenAccent.withAlpha(60) : Colors.white.withAlpha(15)),
        ),
        child: Text(msg.text, style: TextStyle(color: msg.isUser ? Colors.greenAccent : Colors.white, fontSize: 14)),
      ),
    );
  }
}

// ── Loading row (inline, replaces old system messages) ────────────────────────

class _LoadingRow extends StatefulWidget {
  final String label;
  const _LoadingRow({required this.label});
  @override
  State<_LoadingRow> createState() => _LoadingRowState();
}

class _LoadingRowState extends State<_LoadingRow> with SingleTickerProviderStateMixin {
  late AnimationController _ctrl;
  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(vsync: this, duration: const Duration(milliseconds: 1200))..repeat();
  }
  @override
  void dispose() { _ctrl.dispose(); super.dispose(); }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          SizedBox(
            width: 16, height: 16,
            child: AnimatedBuilder(
              animation: _ctrl,
              builder: (_, __) => CircularProgressIndicator(
                value: null, strokeWidth: 1.5,
                color: Colors.greenAccent.withAlpha(180),
              ),
            ),
          ),
          const SizedBox(width: 10),
          Text(widget.label, style: const TextStyle(color: Colors.white38, fontSize: 12, fontStyle: FontStyle.italic)),
        ],
      ),
    );
  }
}

// ── Themed dialog helper ──────────────────────────────────────────────────────

Future<void> _showLoreDialog(BuildContext context, {required String title, required String message}) {
  return showDialog(
    context: context,
    builder: (_) => AlertDialog(
      backgroundColor: const Color(0xFF0F2010),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16), side: BorderSide(color: Colors.greenAccent.withAlpha(60))),
      title: Text(title, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
      content: Text(message, style: const TextStyle(color: Colors.white70, fontSize: 13)),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('OK', style: TextStyle(color: Colors.greenAccent)),
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
        onTap: () => _openFullscreen(context),
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 6),
          constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.82),
          decoration: BoxDecoration(borderRadius: BorderRadius.circular(12), border: Border.all(color: Colors.white.withAlpha(20))),
          clipBehavior: Clip.antiAlias,
          child: Stack(
            children: [
              Hero(tag: bytes.hashCode, child: Image.memory(bytes, fit: BoxFit.cover)),
              Positioned(
                bottom: 8, right: 8,
                child: Container(
                  padding: const EdgeInsets.all(4),
                  decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(6)),
                  child: const Icon(Icons.fullscreen_rounded, color: Colors.white70, size: 18),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  void _openFullscreen(BuildContext context) {
    Navigator.of(context).push(MaterialPageRoute(builder: (_) => _FullscreenImagePage(bytes: bytes)));
  }
}

class _FullscreenImagePage extends StatelessWidget {
  final Uint8List bytes;
  const _FullscreenImagePage({required this.bytes});

  Future<void> _saveToGallery(BuildContext context) async {
    try {
      await Gal.putImageBytes(bytes, name: 'lore_${DateTime.now().millisecondsSinceEpoch}.png');
      if (context.mounted) await _showLoreDialog(context, title: 'Saved', message: 'Image saved to your gallery.');
    } catch (e) {
      if (context.mounted) await _showLoreDialog(context, title: 'Error', message: 'Could not save image: $e');
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
          minScale: 0.5, maxScale: 5.0,
          child: Hero(tag: bytes.hashCode, child: Image.memory(bytes, fit: BoxFit.contain)),
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
      ..initialize().then((_) { if (mounted) setState(() => _initialized = true); })
         .catchError((_) { if (mounted) setState(() => _error = true); });
  }

  @override
  void dispose() { _ctrl.dispose(); super.dispose(); }

  @override
  Widget build(BuildContext context) {
    final width = MediaQuery.of(context).size.width * 0.85;
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 6),
        width: width,
        decoration: BoxDecoration(borderRadius: BorderRadius.circular(12), border: Border.all(color: Colors.white.withAlpha(20)), color: Colors.black),
        clipBehavior: Clip.antiAlias,
        child: _error
            ? const Padding(padding: EdgeInsets.all(16), child: Text('Video unavailable', style: TextStyle(color: Colors.white38, fontSize: 12)))
            : !_initialized
                ? SizedBox(height: width * 9 / 16, child: const Center(child: CircularProgressIndicator(color: Colors.greenAccent, strokeWidth: 2)))
                : Stack(
                    children: [
                      AspectRatio(aspectRatio: _ctrl.value.aspectRatio, child: VideoPlayer(_ctrl)),
                      // Tap to play/pause
                      Positioned.fill(
                        child: GestureDetector(
                          onTap: () => setState(() { _ctrl.value.isPlaying ? _ctrl.pause() : _ctrl.play(); }),
                          child: AnimatedOpacity(
                            opacity: _ctrl.value.isPlaying ? 0.0 : 1.0,
                            duration: const Duration(milliseconds: 200),
                            child: Container(color: Colors.black38, child: const Center(child: Icon(Icons.play_arrow_rounded, color: Colors.white, size: 52))),
                          ),
                        ),
                      ),
                      // Top-right action buttons
                      Positioned(
                        top: 8, right: 8,
                        child: Row(
                          children: [
                            _VideoIconBtn(icon: Icons.download_rounded, onTap: () => _saveToGallery(context)),
                            const SizedBox(width: 6),
                            _VideoIconBtn(icon: Icons.fullscreen_rounded, onTap: () => _openFullscreen(context)),
                          ],
                        ),
                      ),
                      // Progress bar
                      Positioned(
                        bottom: 0, left: 0, right: 0,
                        child: VideoProgressIndicator(_ctrl, allowScrubbing: true,
                          colors: const VideoProgressColors(playedColor: Colors.greenAccent, bufferedColor: Colors.white24, backgroundColor: Colors.white12)),
                      ),
                    ],
                  ),
      ),
    );
  }

  void _openFullscreen(BuildContext context) {
    _ctrl.pause();
    Navigator.of(context).push(MaterialPageRoute(builder: (_) => _FullscreenVideoPage(url: widget.url)));
  }

  Future<void> _saveToGallery(BuildContext context) async {
    try {
      final resp = await http.get(Uri.parse(widget.url));
      if (resp.statusCode == 200) {
        final tmp = await getTemporaryDirectory();
        final file = File('${tmp.path}/lore_${DateTime.now().millisecondsSinceEpoch}.mp4');
        await file.writeAsBytes(resp.bodyBytes);
        await Gal.putVideo(file.path);
        if (context.mounted) await _showLoreDialog(context, title: 'Saved', message: 'Video saved to your gallery.');
      } else {
        throw Exception('HTTP ${resp.statusCode}');
      }
    } catch (e) {
      if (context.mounted) await _showLoreDialog(context, title: 'Error', message: 'Could not save video: $e');
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
        decoration: BoxDecoration(color: Colors.black54, borderRadius: BorderRadius.circular(6)),
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
    SystemChrome.setPreferredOrientations([DeviceOrientation.landscapeLeft, DeviceOrientation.landscapeRight]);
    _ctrl = VideoPlayerController.networkUrl(Uri.parse(widget.url))
      ..initialize().then((_) { if (mounted) { setState(() => _initialized = true); _ctrl.play(); } });
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
        final file = File('${tmp.path}/lore_${DateTime.now().millisecondsSinceEpoch}.mp4');
        await file.writeAsBytes(resp.bodyBytes);
        await Gal.putVideo(file.path);
        if (mounted) await _showLoreDialog(context, title: 'Saved', message: 'Video saved to your gallery.');
      } else {
        throw Exception('HTTP ${resp.statusCode}');
      }
    } catch (e) {
      if (mounted) await _showLoreDialog(context, title: 'Error', message: 'Could not save video: $e');
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
                ? AspectRatio(aspectRatio: _ctrl.value.aspectRatio, child: VideoPlayer(_ctrl))
                : const CircularProgressIndicator(color: Colors.greenAccent),
          ),
          if (_initialized) ...[
            Positioned.fill(child: GestureDetector(onTap: () => setState(() { _ctrl.value.isPlaying ? _ctrl.pause() : _ctrl.play(); }))),
            Positioned(
              bottom: 24, left: 16, right: 16,
              child: Row(
                children: [
                  IconButton(
                    icon: Icon(_ctrl.value.isPlaying ? Icons.pause_rounded : Icons.play_arrow_rounded, color: Colors.white, size: 32),
                    onPressed: () => setState(() { _ctrl.value.isPlaying ? _ctrl.pause() : _ctrl.play(); }),
                  ),
                  Expanded(child: VideoProgressIndicator(_ctrl, allowScrubbing: true,
                    colors: const VideoProgressColors(playedColor: Colors.greenAccent, bufferedColor: Colors.white24, backgroundColor: Colors.white12))),
                ],
              ),
            ),
            Positioned(
              top: 40, right: 8,
              child: Row(
                children: [
                  IconButton(icon: const Icon(Icons.download_rounded, color: Colors.white), onPressed: _saveToGallery),
                  IconButton(icon: const Icon(Icons.close_rounded, color: Colors.white, size: 28), onPressed: () => Navigator.of(context).pop()),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

// ── Waveform + Mic button ─────────────────────────────────────────────────────

class _WaveformBar extends StatelessWidget {
  final bool active;
  final AnimationController animation;
  const _WaveformBar({required this.active, required this.animation});

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 48,
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      decoration: BoxDecoration(color: Colors.white.withAlpha(6), borderRadius: BorderRadius.circular(10)),
      child: AnimatedBuilder(
        animation: animation,
        builder: (_, __) => CustomPaint(size: Size.infinite, painter: _WavePainter(t: animation.value, active: active)),
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
      final n = i / bars;
      final amp = active
          ? (math.sin((n * math.pi * 4) + t * math.pi * 2) * 0.5 + math.sin((n * math.pi * 6) + t * math.pi * 3) * 0.3).abs()
          : 0.05;
      final h = math.max(2.0, amp * size.height * 0.7);
      paint.color = Color.lerp(Colors.greenAccent.withAlpha(30), Colors.greenAccent, active ? amp.clamp(0.0, 1.0) : 0.1)!;
      canvas.drawRRect(
        RRect.fromRectAndRadius(Rect.fromCenter(center: Offset(i * bw + bw / 2, cy), width: bw * 0.5, height: h), const Radius.circular(2)),
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
  final bool playing;
  final AnimationController pulse;
  final VoidCallback onTap;

  const _MicButton({
    required this.recording, required this.connected, required this.connecting,
    required this.playing, required this.pulse, required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final color = recording ? Colors.redAccent : connected ? Colors.greenAccent : Colors.white38;
    return GestureDetector(
      onTap: connecting ? null : onTap,
      child: AnimatedBuilder(
        animation: pulse,
        builder: (_, child) => Transform.scale(scale: recording ? (1.0 + pulse.value * 0.08) : 1.0, child: child),
        child: Container(
          width: 72, height: 72,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: color.withAlpha(30),
            border: Border.all(color: color, width: 2),
            boxShadow: [BoxShadow(color: color.withAlpha(60), blurRadius: 20, spreadRadius: 2)],
          ),
          child: connecting
              ? const Center(child: SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white54)))
              : playing && !recording
                  ? const Center(child: Icon(Icons.volume_up_rounded, color: Colors.greenAccent, size: 28))
                  : Icon(recording ? Icons.stop_rounded : Icons.mic_rounded, color: color, size: 32),
        ),
      ),
    );
  }
}
