/// Microphone streaming service for VoiceMode — Option B (true Live API streaming).
///
/// Mirrors AudioLoop.listen_audio() from the Google AI Studio reference script:
///   while True:
///     data = stream.read(CHUNK_SIZE)
///     out_queue.put({"data": data, "mime_type": "audio/pcm"})
///
/// Instead of accumulating audio to a file and emitting one blob on stop,
/// this service emits AudioChunk events continuously at ~64ms intervals
/// (CHUNK_SIZE=1024 samples @ 16kHz) while the mic is active.
///
/// The backend LiveSessionManager feeds these chunks directly into the
/// persistent Gemini Live API session's out_queue → send_realtime_input().
/// VAD fires naturally on the continuous stream, just like the reference script.
///
/// Requirements 3.1, 24.3:
/// - Stream PCM audio while active, emit chunks continuously
/// - Detect high ambient noise (> 70 dB) and signal the caller
library;

import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'package:logging/logging.dart';
import 'package:record/record.dart';

// Matches CHUNK_SIZE = 1024 and SEND_SAMPLE_RATE = 16000 from the reference script.
// 1024 samples @ 16kHz = 64ms per chunk.
const int _sampleRate = 16000;
const int _chunkSamples = 1024; // samples per chunk
const int _chunkBytes = _chunkSamples * 2; // 16-bit = 2 bytes per sample
const int _chunkIntervalMs = (_chunkSamples * 1000) ~/ _sampleRate; // ≈ 64ms
const int _noiseCheckMs = 500;

/// A PCM audio chunk ready for transmission to the backend.
/// [base64Audio] is base64-encoded LINEAR16 PCM at 16 kHz mono.
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

/// Streams microphone audio as continuous PCM chunks while recording.
///
/// Usage:
///   startRecording() → audioChunks emits chunks every ~64ms
///   stopRecording()  → stops the stream (no final blob)
///
/// The screen sends each chunk as a voice_chunk WebSocket message.
/// When the user releases the mic button, the screen sends voice_mic_stop
/// separately — this service does not need to know about that.
class MicrophoneService {
  final _log = Logger('MicrophoneService');
  final _audioController = StreamController<AudioChunk>.broadcast();
  final _noiseController = StreamController<HighNoiseLevelWarning>.broadcast();

  final AudioRecorder _recorder = AudioRecorder();
  StreamSubscription<Uint8List>? _pcmSub;
  Timer? _noiseTimer;
  bool _isRecording = false;

  // Internal buffer to accumulate bytes until we have a full chunk
  final _buffer = <int>[];

  /// Emits AudioChunk continuously while recording (every ~64ms).
  Stream<AudioChunk> get audioChunks => _audioController.stream;

  /// Fires when high ambient noise is detected during recording.
  Stream<HighNoiseLevelWarning> get noiseWarnings => _noiseController.stream;

  bool get isRecording => _isRecording;

  // ── Lifecycle ────────────────────────────────────────────────────────────

  /// Start recording and begin streaming PCM chunks.
  ///
  /// Uses AudioRecorder.startStream() to get a continuous byte stream,
  /// which we slice into _chunkBytes-sized pieces and emit as AudioChunks.
  Future<void> startRecording() async {
    if (_isRecording) return;
    _isRecording = true;
    _buffer.clear();

    final stream = await _recorder.startStream(
      const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        sampleRate: _sampleRate,
        numChannels: 1,
      ),
    );

    // Subscribe to the raw PCM byte stream from the recorder.
    // Slice into _chunkBytes chunks and emit each as an AudioChunk.
    _pcmSub = stream.listen(
      _onPcmData,
      onError: (e) => _log.warning('PCM stream error: $e'),
      onDone: () => _log.info('PCM stream done'),
    );

    // Periodically check amplitude for noise warnings
    _noiseTimer = Timer.periodic(
      const Duration(milliseconds: _noiseCheckMs),
      (_) => _checkNoise(),
    );

    _log.info('Microphone streaming started (chunk=${_chunkBytes}B, interval=${_chunkIntervalMs}ms)');
  }

  /// Stop recording. Does NOT emit a final chunk — caller sends voice_mic_stop separately.
  Future<void> stopRecording() async {
    if (!_isRecording) return;
    _isRecording = false;

    _noiseTimer?.cancel();
    _noiseTimer = null;
    await _pcmSub?.cancel();
    _pcmSub = null;
    _buffer.clear();

    await _recorder.stop();
    _log.info('Microphone streaming stopped');
  }

  /// Release all resources.
  Future<void> dispose() async {
    await stopRecording();
    await _recorder.dispose();
    await _audioController.close();
    await _noiseController.close();
  }

  // ── Internal ─────────────────────────────────────────────────────────────

  void _onPcmData(Uint8List data) {
    if (!_isRecording) return;
    _buffer.addAll(data);

    // Emit complete chunks as they accumulate
    while (_buffer.length >= _chunkBytes) {
      final chunk = Uint8List.fromList(_buffer.sublist(0, _chunkBytes));
      _buffer.removeRange(0, _chunkBytes);
      _emitChunk(chunk);
    }
  }

  void _emitChunk(Uint8List pcmBytes) {
    final base64Audio = base64Encode(pcmBytes);
    _audioController.add(AudioChunk(
      base64Audio: base64Audio,
      sampleRate: _sampleRate,
      timestamp: DateTime.now().millisecondsSinceEpoch,
    ));
  }

  Future<void> _checkNoise() async {
    if (!_isRecording) return;
    try {
      final amplitude = await _recorder.getAmplitude();
      // dBFS ≈ 0 is loud; dBFS ≈ -90 is near-silence.
      // Rough SPL estimate: assume full-scale ≈ 94 dB SPL
      final estimatedDb = 94 + amplitude.current;
      if (estimatedDb > 70) {
        _noiseController.add(HighNoiseLevelWarning(estimatedDb));
      }
    } catch (e) {
      _log.warning('Noise check failed: $e');
    }
  }
}
