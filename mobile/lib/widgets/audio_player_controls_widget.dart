/// Playback controls for narration audio.
///
/// Displays play/pause, progress bar with seek, duration, and queue indicator.
///
/// Requirement 24.7.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:just_audio/just_audio.dart';

import '../providers/app_providers.dart';

/// Compact audio player controls for the VoiceMode bottom bar.
class AudioPlayerControlsWidget extends ConsumerStatefulWidget {
  const AudioPlayerControlsWidget({super.key});

  @override
  ConsumerState<AudioPlayerControlsWidget> createState() =>
      _AudioPlayerControlsWidgetState();
}

class _AudioPlayerControlsWidgetState
    extends ConsumerState<AudioPlayerControlsWidget> {
  @override
  Widget build(BuildContext context) {
    final audioService = ref.watch(audioPlaybackServiceProvider);

    return StreamBuilder<PlayerState>(
      stream: audioService.playerState,
      builder: (context, playerSnapshot) {
        final playerState = playerSnapshot.data;
        final isPlaying = playerState?.playing ?? false;
        final processingState =
            playerState?.processingState ?? ProcessingState.idle;
        final isLoading = processingState == ProcessingState.loading ||
            processingState == ProcessingState.buffering;

        return StreamBuilder<Duration>(
          stream: audioService.position,
          builder: (context, posSnapshot) {
            final position = posSnapshot.data ?? Duration.zero;

            return StreamBuilder<Duration?>(
              stream: audioService.duration,
              builder: (context, durSnapshot) {
                final duration = durSnapshot.data ?? Duration.zero;

                return Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  decoration: BoxDecoration(
                    color: Colors.white.withAlpha(10),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(color: Colors.white.withAlpha(15)),
                  ),
                  child: Row(
                    children: [
                      // Play / Pause / Loading button
                      _PlayPauseButton(
                        isPlaying: isPlaying,
                        isLoading: isLoading,
                        onPlay: audioService.resume,
                        onPause: audioService.pause,
                      ),
                      const SizedBox(width: 10),

                      // Progress bar with seek
                      Expanded(
                        child: _ProgressBar(
                          position: position,
                          duration: duration,
                          onSeek: audioService.seekTo,
                        ),
                      ),
                      const SizedBox(width: 10),

                      // Duration label
                      _DurationLabel(
                        position: position,
                        duration: duration,
                      ),

                      // Queue indicator
                      if (audioService.queueLength > 1) ...[
                        const SizedBox(width: 8),
                        _QueueIndicator(
                          currentIndex: audioService.currentIndex,
                          total: audioService.queueLength,
                          onNext: audioService.hasNext
                              ? audioService.playNext
                              : null,
                        ),
                      ],
                    ],
                  ),
                );
              },
            );
          },
        );
      },
    );
  }
}

/// Circular play/pause button with a loading spinner overlay.
class _PlayPauseButton extends StatelessWidget {
  final bool isPlaying;
  final bool isLoading;
  final VoidCallback onPlay;
  final VoidCallback onPause;

  const _PlayPauseButton({
    required this.isPlaying,
    required this.isLoading,
    required this.onPlay,
    required this.onPause,
  });

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 36,
      height: 36,
      child: Stack(
        alignment: Alignment.center,
        children: [
          if (isLoading)
            const SizedBox(
              width: 36,
              height: 36,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                color: Colors.greenAccent,
              ),
            ),
          IconButton(
            padding: EdgeInsets.zero,
            iconSize: 24,
            icon: Icon(
              isPlaying ? Icons.pause_rounded : Icons.play_arrow_rounded,
              color: Colors.greenAccent,
            ),
            onPressed: isPlaying ? onPause : onPlay,
          ),
        ],
      ),
    );
  }
}

/// Slim progress bar with drag-to-seek.
class _ProgressBar extends StatelessWidget {
  final Duration position;
  final Duration duration;
  final ValueChanged<Duration> onSeek;

  const _ProgressBar({
    required this.position,
    required this.duration,
    required this.onSeek,
  });

  @override
  Widget build(BuildContext context) {
    final maxMs = duration.inMilliseconds.toDouble();
    final currentMs = position.inMilliseconds
        .toDouble()
        .clamp(0.0, maxMs > 0 ? maxMs : 1.0);

    return SliderTheme(
      data: SliderThemeData(
        trackHeight: 3,
        thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 5),
        overlayShape: const RoundSliderOverlayShape(overlayRadius: 12),
        activeTrackColor: Colors.greenAccent,
        inactiveTrackColor: Colors.white.withAlpha(25),
        thumbColor: Colors.greenAccent,
        overlayColor: Colors.greenAccent.withAlpha(30),
      ),
      child: Slider(
        min: 0,
        max: maxMs > 0 ? maxMs : 1.0,
        value: currentMs,
        onChanged: (value) => onSeek(Duration(milliseconds: value.round())),
      ),
    );
  }
}

/// Displays `current / total` duration in mm:ss format.
class _DurationLabel extends StatelessWidget {
  final Duration position;
  final Duration duration;

  const _DurationLabel({required this.position, required this.duration});

  @override
  Widget build(BuildContext context) {
    return Text(
      '${_fmt(position)} / ${_fmt(duration)}',
      style: const TextStyle(color: Colors.white38, fontSize: 11),
    );
  }

  String _fmt(Duration d) {
    final minutes = d.inMinutes.remainder(60).toString().padLeft(2, '0');
    final seconds = d.inSeconds.remainder(60).toString().padLeft(2, '0');
    return '$minutes:$seconds';
  }
}

/// Shows the current queue position and a skip-next button.
class _QueueIndicator extends StatelessWidget {
  final int currentIndex;
  final int total;
  final VoidCallback? onNext;

  const _QueueIndicator({
    required this.currentIndex,
    required this.total,
    this.onNext,
  });

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(
          '${currentIndex + 1}/$total',
          style: const TextStyle(color: Colors.white30, fontSize: 10),
        ),
        if (onNext != null)
          IconButton(
            padding: EdgeInsets.zero,
            constraints: const BoxConstraints(minWidth: 24, minHeight: 24),
            iconSize: 18,
            icon: const Icon(Icons.skip_next_rounded, color: Colors.white38),
            onPressed: onNext,
          ),
      ],
    );
  }
}
