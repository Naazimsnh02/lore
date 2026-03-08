/// Widget that displays the live documentary stream at the bottom of a screen.
///
/// Renders the latest items from [SessionState.streamElements] (narration
/// transcripts, illustrated facts, etc.) in a scrollable card strip.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/models.dart';
import '../providers/app_providers.dart';

class DocumentaryStreamWidget extends ConsumerWidget {
  const DocumentaryStreamWidget({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final elements = ref.watch(sessionProvider).streamElements;

    if (elements.isEmpty) return const SizedBox.shrink();

    return Container(
      height: 180,
      decoration: BoxDecoration(
        gradient: LinearGradient(
          colors: [Colors.transparent, Colors.black.withAlpha(230)],
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
        ),
      ),
      child: ListView.builder(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        scrollDirection: Axis.horizontal,
        reverse: false,
        itemCount: elements.length,
        itemBuilder: (_, index) => _StreamElementCard(element: elements[index]),
      ),
    );
  }
}

class _StreamElementCard extends StatelessWidget {
  final DocumentaryStreamElement element;
  const _StreamElementCard({required this.element});

  @override
  Widget build(BuildContext context) {
    final (icon, color) = switch (element.contentType) {
      ContentType.narration => (Icons.record_voice_over, Colors.blueAccent),
      ContentType.video => (Icons.videocam, Colors.purpleAccent),
      ContentType.illustration => (Icons.image, Colors.tealAccent),
      ContentType.fact => (Icons.info_outline, Colors.amberAccent),
      ContentType.transition => (Icons.swap_horiz, Colors.white38),
    };

    // Extract a human-readable snippet from the content map
    final text = element.content['text'] as String? ??
        element.content['url'] as String? ??
        element.contentType.name;

    return Container(
      width: 200,
      margin: const EdgeInsets.only(right: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.white.withAlpha(20),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: color.withAlpha(77)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, color: color, size: 14),
              const SizedBox(width: 6),
              Text(
                element.contentType.name.toUpperCase(),
                style: TextStyle(
                    color: color, fontSize: 10, fontWeight: FontWeight.bold),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Expanded(
            child: Text(
              text,
              style: const TextStyle(color: Colors.white70, fontSize: 12),
              maxLines: 6,
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ),
    );
  }
}
