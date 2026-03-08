/// Microphone / audio streaming service for VoiceMode and LoreMode.
///
/// Requirements 3.1, 24.3:
/// - Continuously stream PCM audio to the backend while active
/// - Detect high ambient noise (> 70 dB) and signal the caller
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:logging/logging.dart';
import 'package:path_provider/path_provider.dart';
import 'package:record/record.dart';

const int _sampleRate = 16000; // 16 kHz mono — Gemini Live API requirement
const int _chunkDurationMs = 500; // Emit a chunk every 500 ms

/// A chunk of base-64-encoded PCM audio ready for transmission.
class AudioChunk {
  final String base64Audio;
  final int sampleRate;
  final int timestamp;

  const AudioChunk({
    required this.base64Audio,
    required this.sampleRate,
    required this.timestamp,
  });
}

/// Fired when ambient noise is estimated to exceed 70 dB (Requirement 3.5).
class HighNoiseLevelWarning {
  final double estimatedDb;
  const HighNoiseLevelWarning(this.estimatedDb);
}

/// Streams microphone audio as base-64 PCM chunks.
class MicrophoneService {
  final _log = Logger('MicrophoneService');
  final _audioController = StreamController<AudioChunk>.broadcast();
  final _noiseController = StreamController<HighNoiseLevelWarning>.broadcast();

  final AudioRecorder _recorder = AudioRecorder();
  Timer? _chunkTimer;
  bool _isRecording = false;
  String? _tempFilePath;

  /// Stream of audio chunks (~every 500 ms) for transmission to the backend.
  Stream<AudioChunk> get audioChunks => _audioController.stream;

  /// Fires when high ambient noise is detected.
  Stream<HighNoiseLevelWarning> get noiseWarnings => _noiseController.stream;

  bool get isRecording => _isRecording;

  // ── Lifecycle ────────────────────────────────────────────────────────────

  /// Start recording and emitting audio chunks.
  Future<void> startRecording() async {
    if (_isRecording) return;

    final tempDir = await getTemporaryDirectory();
    _tempFilePath = '${tempDir.path}/lore_audio_chunk.pcm';

    // Start streaming recorder — emits raw PCM
    await _recorder.start(
      const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        sampleRate: _sampleRate,
        numChannels: 1,
      ),
      path: _tempFilePath!,
    );

    _isRecording = true;
    _chunkTimer = Timer.periodic(
      const Duration(milliseconds: _chunkDurationMs),
      (_) => _emitChunk(),
    );
    _log.info('Microphone recording started');
  }

  /// Stop recording.
  Future<void> stopRecording() async {
    if (!_isRecording) return;
    _isRecording = false;
    _chunkTimer?.cancel();
    _chunkTimer = null;
    await _recorder.stop();
    _log.info('Microphone recording stopped');
  }

  /// Release all resources.
  Future<void> dispose() async {
    await stopRecording();
    await _recorder.dispose();
    await _audioController.close();
    await _noiseController.close();
  }

  // ── Internal ─────────────────────────────────────────────────────────────

  Future<void> _emitChunk() async {
    if (!_isRecording || _tempFilePath == null) return;

    try {
      final amplitude = await _recorder.getAmplitude();
      // Amplitude in dBFS — convert to rough SPL estimate for the warning.
      // dBFS ≈ 0 is loud; dBFS ≈ -90 is near-silence.
      // Mapping: assume full-scale ≈ 94 dB SPL → (94 + amplitude.current)
      final estimatedDb = 94 + amplitude.current;
      if (estimatedDb > 70) {
        _noiseController.add(HighNoiseLevelWarning(estimatedDb));
      }

      final file = File(_tempFilePath!);
      if (!await file.exists()) return;

      final bytes = await file.readAsBytes();
      if (bytes.isEmpty) return;

      // Rotate the temp file for the next chunk
      await file.delete();

      final base64Audio = base64Encode(bytes);
      _audioController.add(AudioChunk(
        base64Audio: base64Audio,
        sampleRate: _sampleRate,
        timestamp: DateTime.now().millisecondsSinceEpoch,
      ));
    } catch (e) {
      _log.warning('Failed to emit audio chunk: $e');
    }
  }
}
