/// Home screen — mode selection and session entry point.
///
/// Requirement 1.2: System displays mode selection options on launch.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/models.dart';
import '../providers/app_providers.dart';
import 'sight_mode_screen.dart';
import 'voice_mode_screen.dart';
import 'new_voice_mode_screen.dart';
import 'lore_mode_screen.dart';
import 'gps_walking_tour_screen.dart';

class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final session = ref.watch(sessionProvider);

    return Scaffold(
      backgroundColor: Colors.black,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 32),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // App header
              const _LoreHeader(),
              const SizedBox(height: 48),

              // Mode cards — scrollable so they fit on any screen size
              Expanded(
                child: SingleChildScrollView(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      _ModeCard(
                        title: 'SightMode',
                        subtitle: 'Point your camera at a landmark',
                        icon: Icons.camera_alt_outlined,
                        gradient: const [Color(0xFF1A237E), Color(0xFF283593)],
                        onTap: () => _enterMode(context, ref, LoreMode.sight),
                      ),
                      const SizedBox(height: 16),
                      _ModeCard(
                        title: 'VoiceMode',
                        subtitle: 'Speak any topic for an instant documentary',
                        icon: Icons.mic_outlined,
                        gradient: const [Color(0xFF1B5E20), Color(0xFF2E7D32)],
                        onTap: () => _enterMode(context, ref, LoreMode.voice),
                      ),
                      const SizedBox(height: 16),
                      _ModeCard(
                        title: 'LoreMode',
                        subtitle:
                            'Camera + Voice fusion — unlocks Alternate History',
                        icon: Icons.auto_awesome_outlined,
                        gradient: const [Color(0xFF4A148C), Color(0xFF6A1B9A)],
                        onTap: () => _enterMode(context, ref, LoreMode.lore),
                      ),
                      const SizedBox(height: 16),
                      _ModeCard(
                        title: 'GPS Walking Tour',
                        subtitle: 'Auto-discover landmarks as you walk',
                        icon: Icons.map_outlined,
                        gradient: const [Color(0xFFBF360C), Color(0xFFD84315)],
                        onTap: () => _enterGpsWalkingTour(context, ref),
                      ),
                      const SizedBox(height: 16),
                      _ModeCard(
                        title: 'Voice Mode (Live)',
                        subtitle: 'Direct Gemini Live API — real-time voice',
                        icon: Icons.spatial_audio_outlined,
                        gradient: const [Color(0xFF004D40), Color(0xFF00695C)],
                        onTap: () => Navigator.push(
                          context,
                          MaterialPageRoute(
                            builder: (_) => const NewVoiceModeScreen(),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
              ),

              // Depth dial selector
              const SizedBox(height: 24),
              _DepthDialSelector(currentDial: session.depthDial),

              // Connection status indicator
              const SizedBox(height: 16),
              _ConnectionStatus(isConnected: session.isConnected),
            ],
          ),
        ),
      ),
    );
  }

  void _enterMode(BuildContext context, WidgetRef ref, LoreMode mode) {
    ref.read(sessionProvider.notifier).setMode(mode);
    final screen = switch (mode) {
      LoreMode.sight => const SightModeScreen(),
      LoreMode.voice => const VoiceModeScreen(),
      LoreMode.lore => const LoreModeScreen(),
    };
    Navigator.push(context, MaterialPageRoute(builder: (_) => screen));
  }

  void _enterGpsWalkingTour(BuildContext context, WidgetRef ref) {
    // GPS Walking Tour can work in SightMode or LoreMode
    ref.read(sessionProvider.notifier).setMode(LoreMode.sight);
    Navigator.push(
      context,
      MaterialPageRoute(
        builder: (_) => const GpsWalkingTourScreen(mode: LoreMode.sight),
      ),
    );
  }
}

class _LoreHeader extends StatelessWidget {
  const _LoreHeader();

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'LORE',
          style: Theme.of(context).textTheme.displayLarge?.copyWith(
                color: Colors.white,
                fontWeight: FontWeight.bold,
                letterSpacing: 4,
              ),
        ),
        const SizedBox(height: 4),
        Text(
          'The World Is Your Documentary',
          style: Theme.of(context).textTheme.bodyMedium?.copyWith(
                color: Colors.white54,
                letterSpacing: 1,
              ),
        ),
      ],
    );
  }
}

class _ModeCard extends StatelessWidget {
  final String title;
  final String subtitle;
  final IconData icon;
  final List<Color> gradient;
  final VoidCallback onTap;

  const _ModeCard({
    required this.title,
    required this.subtitle,
    required this.icon,
    required this.gradient,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(24),
        decoration: BoxDecoration(
          gradient: LinearGradient(
            colors: gradient,
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
          ),
          borderRadius: BorderRadius.circular(16),
        ),
        child: Row(
          children: [
            Icon(icon, color: Colors.white, size: 36),
            const SizedBox(width: 20),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 20,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    subtitle,
                    style: TextStyle(
                      color: Colors.white.withAlpha(178),
                      fontSize: 13,
                    ),
                  ),
                ],
              ),
            ),
            const Icon(Icons.arrow_forward_ios, color: Colors.white54, size: 16),
          ],
        ),
      ),
    );
  }
}

class _DepthDialSelector extends ConsumerWidget {
  final DepthDial currentDial;

  const _DepthDialSelector({required this.currentDial});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Row(
      children: [
        Text(
          'Depth Dial:',
          style: TextStyle(color: Colors.white54, fontSize: 13),
        ),
        const SizedBox(width: 12),
        ...DepthDial.values.map((dial) {
          final selected = dial == currentDial;
          return GestureDetector(
            onTap: () => ref.read(sessionProvider.notifier).setDepthDial(dial),
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 200),
              margin: const EdgeInsets.only(right: 8),
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              decoration: BoxDecoration(
                color: selected ? Colors.deepPurple : Colors.white12,
                borderRadius: BorderRadius.circular(20),
              ),
              child: Text(
                dial.name[0].toUpperCase() + dial.name.substring(1),
                style: TextStyle(
                  color: selected ? Colors.white : Colors.white54,
                  fontSize: 12,
                  fontWeight:
                      selected ? FontWeight.bold : FontWeight.normal,
                ),
              ),
            ),
          );
        }),
      ],
    );
  }
}

class _ConnectionStatus extends StatelessWidget {
  final bool isConnected;

  const _ConnectionStatus({required this.isConnected});

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(
            color: isConnected ? Colors.greenAccent : Colors.redAccent,
            shape: BoxShape.circle,
          ),
        ),
        const SizedBox(width: 8),
        Text(
          isConnected ? 'Connected to LORE backend' : 'Disconnected',
          style: TextStyle(
            color: isConnected ? Colors.greenAccent : Colors.redAccent,
            fontSize: 12,
          ),
        ),
      ],
    );
  }
}
