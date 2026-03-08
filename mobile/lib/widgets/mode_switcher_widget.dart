/// A compact pill widget that shows the current mode and allows quick switching.
///
/// Requirement 1.6: Allow mode switching during an active session.
/// Requirement 1.7: Session memory is preserved when switching.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/models.dart';
import '../providers/app_providers.dart';

class ModeSwitcherWidget extends ConsumerWidget {
  final LoreMode currentMode;

  const ModeSwitcherWidget({super.key, required this.currentMode});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return GestureDetector(
      onTap: () => _showModeSheet(context, ref),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        decoration: BoxDecoration(
          color: Colors.black54,
          borderRadius: BorderRadius.circular(20),
          border: Border.all(color: Colors.white24),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(_modeIcon(currentMode), color: Colors.white, size: 14),
            const SizedBox(width: 6),
            Text(
              _modeLabel(currentMode),
              style: const TextStyle(color: Colors.white, fontSize: 12),
            ),
            const SizedBox(width: 4),
            const Icon(Icons.expand_more, color: Colors.white54, size: 14),
          ],
        ),
      ),
    );
  }

  void _showModeSheet(BuildContext context, WidgetRef ref) {
    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF1A1A1A),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (_) => Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              'Switch Mode',
              style: TextStyle(
                  color: Colors.white,
                  fontSize: 18,
                  fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 16),
            ...LoreMode.values.map((mode) => ListTile(
                  leading: Icon(_modeIcon(mode), color: Colors.white70),
                  title: Text(_modeLabel(mode),
                      style: const TextStyle(color: Colors.white)),
                  selected: mode == currentMode,
                  selectedColor: Colors.deepPurpleAccent,
                  onTap: () {
                    // Requirement 1.7: session memory preserved (notifier keeps state)
                    ref.read(sessionProvider.notifier).setMode(mode);
                    Navigator.pop(context);
                    // Navigate back to home so the correct screen is pushed
                    Navigator.popUntil(context, (route) => route.isFirst);
                  },
                )),
          ],
        ),
      ),
    );
  }

  IconData _modeIcon(LoreMode mode) => switch (mode) {
        LoreMode.sight => Icons.camera_alt_outlined,
        LoreMode.voice => Icons.mic_outlined,
        LoreMode.lore => Icons.auto_awesome_outlined,
      };

  String _modeLabel(LoreMode mode) => switch (mode) {
        LoreMode.sight => 'SightMode',
        LoreMode.voice => 'VoiceMode',
        LoreMode.lore => 'LoreMode',
      };
}
