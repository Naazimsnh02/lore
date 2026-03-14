/// Audio playback service for narration and documentary audio content.
///
/// Requirements 24.7:
/// - Play narration audio from base64 PCM data or URLs
/// - Support background playback
/// - Provide playback controls (play, pause, resume, stop, seek)
/// - Handle audio queue for sequential narration segments
/// - Stream live PCM chunks with low latency (addLiveChunk / flushLiveAudio)
library;

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:just_audio/just_audio.dart';
import 'package:logging/logging.dart';
import 'package:path_provider/path_provider.dart';

/// Playback state exposed to the UI layer.
enum PlaybackStatus { idle, loading, playing, paused, completed, error }

/// Manages audio playback for narration segments and documentary audio.
class AudioPlaybackService {
  final _log = Logger('AudioPlaybackService');
  final AudioPlayer _player = AudioPlayer();

  final _statusController = StreamController<PlaybackStatus>.broadcast();
  final List<_QueueEntry> _queue = [];
  int _currentIndex = -1;
  int _tempFileCounter = 0;
  bool _disposed = false;

  // Live streaming state — PCM chunks accumulate here until flushLiveAudio()
  final List<Uint8List> _liveChunks = [];
  static const int _sampleRate = 24000;
  static const int _channels = 1;
  static const int _bitsPerSample = 16;

  // Minimum bytes to buffer before starting playback (~100ms at 24kHz 16-bit mono)
  static const int _minPlaybackBytes = 24000 * 2 ~/ 10; // 4800 bytes ≈ 100ms

  // ── Public streams ──────────────────────────────────────────────────────

  /// Stream of playback state changes.
  Stream<PlayerState> get playerState => _player.playerStateStream;

  /// Stream of the current playback position.
  Stream<Duration> get position => _player.positionStream;

  /// Stream of the total duration (null until loaded).
  Stream<Duration?> get duration => _player.durationStream;

  /// High-level playback status for UI consumption.
  Stream<PlaybackStatus> get status => _statusController.stream;

  // ── Public getters ──────────────────────────────────────────────────────

  bool get isPlaying => _player.playing;

  Duration get currentPosition => _player.position;

  Duration? get totalDuration => _player.duration;

  int get queueLength => _queue.length;

  int get currentIndex => _currentIndex;

  bool get hasNext => _currentIndex < _queue.length - 1;

  // ── Lifecycle ───────────────────────────────────────────────────────────

  AudioPlaybackService() {
    _player.playerStateStream.listen(_onPlayerStateChanged);
    _player.processingStateStream.listen(_onProcessingStateChanged);
  }

  // ── Playback from base64 PCM ────────────────────────────────────────────

  /// Play audio from a base64-encoded PCM byte string.
  ///
  /// The PCM data is wrapped in a minimal WAV header and written to a temp
  /// file, then played via just_audio.
  Future<void> playFromBase64(
    String base64Audio, {
    int sampleRate = 24000,
    int channels = 1,
    int bitsPerSample = 16,
  }) async {
    if (_disposed) return;
    _statusController.add(PlaybackStatus.loading);

    try {
      final pcmBytes = base64Decode(base64Audio);
      final wavFile = await _pcmToWavFile(
        pcmBytes,
        sampleRate: sampleRate,
        channels: channels,
        bitsPerSample: bitsPerSample,
      );
      await _player.setFilePath(wavFile.path);
      await _player.play();
      _statusController.add(PlaybackStatus.playing);
    } catch (e) {
      _log.warning('Failed to play base64 audio: $e');
      _statusController.add(PlaybackStatus.error);
    }
  }

  /// Play audio from a URL (e.g. Cloud Storage signed URL).
  Future<void> playFromUrl(String url) async {
    if (_disposed) return;
    _statusController.add(PlaybackStatus.loading);

    try {
      await _player.setUrl(url);
      await _player.play();
      _statusController.add(PlaybackStatus.playing);
    } catch (e) {
      _log.warning('Failed to play audio from URL: $e');
      _statusController.add(PlaybackStatus.error);
    }
  }

  // ── Playback controls ──────────────────────────────────────────────────

  /// Pause playback.
  Future<void> pause() async {
    if (_disposed) return;
    await _player.pause();
    _statusController.add(PlaybackStatus.paused);
  }

  /// Resume playback after a pause.
  Future<void> resume() async {
    if (_disposed) return;
    await _player.play();
    _statusController.add(PlaybackStatus.playing);
  }

  /// Stop playback and reset position.
  Future<void> stop() async {
    if (_disposed) return;
    await _player.stop();
    _statusController.add(PlaybackStatus.idle);
  }

  /// Seek to a specific position in the current audio.
  Future<void> seekTo(Duration position) async {
    if (_disposed) return;
    await _player.seek(position);
  }

  // ── Queue management ───────────────────────────────────────────────────

  /// Add a base64-encoded PCM audio segment to the playback queue.
  ///
  /// If nothing is currently playing, playback starts automatically.
  Future<void> addToQueue(
    String base64Audio, {
    int sampleRate = 24000,
    int channels = 1,
    int bitsPerSample = 16,
    String? label,
  }) async {
    if (_disposed) return;

    try {
      final pcmBytes = base64Decode(base64Audio);
      final wavFile = await _pcmToWavFile(
        pcmBytes,
        sampleRate: sampleRate,
        channels: channels,
        bitsPerSample: bitsPerSample,
      );
      _queue.add(_QueueEntry(filePath: wavFile.path, label: label));

      // Auto-play if the queue was empty
      if (!isPlaying && _currentIndex < 0) {
        await playNext();
      }
    } catch (e) {
      _log.warning('Failed to add audio to queue: $e');
    }
  }

  // ── Live PCM streaming ─────────────────────────────────────────────────

  /// Append a raw PCM chunk from the Live API audio stream.
  ///
  /// Mirrors AudioLoop.play_audio() from the reference script — chunks are
  /// buffered until we have enough for smooth playback (~100ms), then flushed
  /// immediately so audio starts playing without waiting for turn_complete.
  /// Call [flushLiveAudio] at turn_complete to play any remaining bytes.
  Future<void> addLiveChunk(Uint8List pcmBytes) async {
    if (_disposed || pcmBytes.isEmpty) return;
    _liveChunks.add(pcmBytes);

    // Start playing as soon as we have enough data for smooth playback.
    // This mirrors the reference script's immediate play_audio() behaviour.
    final buffered = _liveChunks.fold<int>(0, (sum, c) => sum + c.length);
    if (buffered >= _minPlaybackBytes && !isPlaying) {
      await _flushLiveChunksToQueue();
    }
  }

  /// Finalise the current live turn: flush any remaining buffered PCM chunks.
  ///
  /// Called on turn_complete. Most audio will already be playing from the
  /// incremental flushes in addLiveChunk; this handles the tail end.
  Future<void> flushLiveAudio() async {
    if (_disposed) return;
    if (_liveChunks.isNotEmpty) {
      await _flushLiveChunksToQueue();
    }
  }

  /// Internal: concatenate buffered chunks, write one WAV, queue for playback.
  Future<void> _flushLiveChunksToQueue() async {
    if (_liveChunks.isEmpty) return;

    final totalBytes = _liveChunks.fold<int>(0, (sum, c) => sum + c.length);
    final pcmBytes = Uint8List(totalBytes);
    var offset = 0;
    for (final chunk in _liveChunks) {
      pcmBytes.setAll(offset, chunk);
      offset += chunk.length;
    }
    _liveChunks.clear();

    try {
      final wavFile = await _pcmToWavFile(
        pcmBytes,
        sampleRate: _sampleRate,
        channels: _channels,
        bitsPerSample: _bitsPerSample,
      );
      _queue.add(_QueueEntry(filePath: wavFile.path, label: 'live'));
      if (!isPlaying && _currentIndex < 0) {
        await playNext();
      }
    } catch (e) {
      _log.warning('Failed to flush live audio: $e');
    }
  }

  /// Discard buffered live chunks without playing (called on barge-in).
  void discardLiveAudio() {
    _liveChunks.clear();
  }

  /// Add a URL-based audio source to the queue.
  Future<void> addUrlToQueue(String url, {String? label}) async {
    if (_disposed) return;
    _queue.add(_QueueEntry(url: url, label: label));

    if (!isPlaying && _currentIndex < 0) {
      await playNext();
    }
  }

  /// Play the next item in the queue.
  Future<void> playNext() async {
    if (_disposed) return;

    final nextIndex = _currentIndex + 1;
    if (nextIndex >= _queue.length) {
      _log.fine('Queue complete — no more items');
      _currentIndex = -1;
      _statusController.add(PlaybackStatus.completed);
      return;
    }

    _currentIndex = nextIndex;
    final entry = _queue[_currentIndex];
    _statusController.add(PlaybackStatus.loading);

    try {
      if (entry.filePath != null) {
        await _player.setFilePath(entry.filePath!);
      } else if (entry.url != null) {
        await _player.setUrl(entry.url!);
      } else {
        _log.warning('Queue entry has neither file path nor URL');
        _statusController.add(PlaybackStatus.error);
        return;
      }
      await _player.play();
      _statusController.add(PlaybackStatus.playing);
    } catch (e) {
      _log.warning('Failed to play queue item $_currentIndex: $e');
      _statusController.add(PlaybackStatus.error);
      // Skip to next on error
      await playNext();
    }
  }

  /// Clear the playback queue and stop playback.
  Future<void> clearQueue() async {
    await stop();
    _queue.clear();
    _currentIndex = -1;
  }

  // ── Disposal ────────────────────────────────────────────────────────────

  /// Release all resources. Must be called when the service is no longer needed.
  Future<void> dispose() async {
    _disposed = true;
    await _player.dispose();
    await _statusController.close();
    // Clean up temp WAV files
    await _cleanupTempFiles();
  }

  // ── Internal ────────────────────────────────────────────────────────────

  void _onPlayerStateChanged(PlayerState state) {
    if (_disposed) return;
    if (!state.playing) {
      if (state.processingState == ProcessingState.completed) {
        _statusController.add(PlaybackStatus.completed);
      }
    }
  }

  void _onProcessingStateChanged(ProcessingState state) {
    if (_disposed) return;
    if (state == ProcessingState.completed) {
      // Auto-advance to the next queued item
      if (hasNext) {
        playNext();
      } else {
        _currentIndex = -1;
        _statusController.add(PlaybackStatus.completed);
      }
    }
  }

  /// Convert raw PCM bytes to a WAV file with a proper header.
  Future<File> _pcmToWavFile(
    Uint8List pcmBytes, {
    required int sampleRate,
    required int channels,
    required int bitsPerSample,
  }) async {
    final tempDir = await getTemporaryDirectory();
    final filePath =
        '${tempDir.path}/lore_narration_${_tempFileCounter++}.wav';
    final file = File(filePath);

    final byteRate = sampleRate * channels * (bitsPerSample ~/ 8);
    final blockAlign = channels * (bitsPerSample ~/ 8);
    final dataSize = pcmBytes.length;
    final fileSize = 36 + dataSize;

    // Build the 44-byte WAV header
    final header = ByteData(44);
    // "RIFF"
    header.setUint8(0, 0x52); // R
    header.setUint8(1, 0x49); // I
    header.setUint8(2, 0x46); // F
    header.setUint8(3, 0x46); // F
    // File size - 8
    header.setUint32(4, fileSize, Endian.little);
    // "WAVE"
    header.setUint8(8, 0x57); // W
    header.setUint8(9, 0x41); // A
    header.setUint8(10, 0x56); // V
    header.setUint8(11, 0x45); // E
    // "fmt "
    header.setUint8(12, 0x66); // f
    header.setUint8(13, 0x6D); // m
    header.setUint8(14, 0x74); // t
    header.setUint8(15, 0x20); // (space)
    // Subchunk1 size (16 for PCM)
    header.setUint32(16, 16, Endian.little);
    // Audio format (1 = PCM)
    header.setUint16(20, 1, Endian.little);
    // Number of channels
    header.setUint16(22, channels, Endian.little);
    // Sample rate
    header.setUint32(24, sampleRate, Endian.little);
    // Byte rate
    header.setUint32(28, byteRate, Endian.little);
    // Block align
    header.setUint16(32, blockAlign, Endian.little);
    // Bits per sample
    header.setUint16(34, bitsPerSample, Endian.little);
    // "data"
    header.setUint8(36, 0x64); // d
    header.setUint8(37, 0x61); // a
    header.setUint8(38, 0x74); // t
    header.setUint8(39, 0x61); // a
    // Data size
    header.setUint32(40, dataSize, Endian.little);

    // Write header + PCM data
    final wavBytes = Uint8List(44 + dataSize);
    wavBytes.setAll(0, header.buffer.asUint8List());
    wavBytes.setAll(44, pcmBytes);

    await file.writeAsBytes(wavBytes, flush: true);
    return file;
  }

  /// Remove temporary WAV files created during playback.
  Future<void> _cleanupTempFiles() async {
    try {
      final tempDir = await getTemporaryDirectory();
      final dir = Directory(tempDir.path);
      await for (final entity in dir.list()) {
        if (entity is File && entity.path.contains('lore_narration_')) {
          await entity.delete();
        }
      }
    } catch (e) {
      _log.fine('Temp file cleanup failed (non-critical): $e');
    }
  }
}

/// Internal queue entry — either a local file path or a remote URL.
class _QueueEntry {
  final String? filePath;
  final String? url;
  final String? label;

  const _QueueEntry({this.filePath, this.url, this.label});
}
