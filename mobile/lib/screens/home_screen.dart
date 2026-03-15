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
import 'new_gps_mode_screen.dart';

class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final session = ref.watch(sessionProvider);

    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [
              Color(0xFF0D1B3E), // Deep navy blue at top
              Color(0xFF020509), // Black at bottom
            ],
            stops: [0.0, 0.4],
          ),
        ),
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 32),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // App header
                const _LoreHeader(),
                const SizedBox(height: 48),

                // Mode cards
                Expanded(
                  child: SingleChildScrollView(
                    physics: const BouncingScrollPhysics(),
                    child: Column(
                      children: [
                        _ModeCard(
                          title: 'SightMode',
                          subtitle: 'Point your camera at a landmark',
                          icon: Icons.camera_alt_outlined,
                          backgroundImage: 'assets/images/SightMode.png',
                          onTap: () => _enterMode(context, ref, LoreMode.sight),
                        ),
                        const SizedBox(height: 16),
                        _ModeCard(
                          title: 'VoiceMode',
                          subtitle: 'Speak any topic for an instant documentary',
                          icon: Icons.mic_outlined,
                          backgroundImage: 'assets/images/VoiceMode.png',
                          onTap: () => Navigator.push(
                            context,
                            MaterialPageRoute(builder: (_) => const NewVoiceModeScreen()),
                          ),
                        ),
                        const SizedBox(height: 16),
                        _ModeCard(
                          title: 'LoreMode',
                          subtitle: 'Camera + Voice fusion — unlocks Alternate History',
                          icon: Icons.auto_awesome_outlined,
                          backgroundImage: 'assets/images/LoreMode.png',
                          onTap: () => _enterMode(context, ref, LoreMode.lore),
                        ),
                        const SizedBox(height: 16),
                        _ModeCard(
                          title: 'GPS Walking Tour',
                          subtitle: 'Auto-discover landmarks as you walk',
                          icon: Icons.map_outlined,
                          backgroundImage: 'assets/images/GPSMode.png',
                          onTap: () => _enterGpsWalkingTour(context, ref),
                        ),
                      ],
                    ),
                  ),
                ),

                // Depth dial selector
                const SizedBox(height: 32),
                _DepthDialSelector(currentDial: session.depthDial),

                // Connection status indicator
                const SizedBox(height: 24),
                _ConnectionStatus(isConnected: session.isConnected),
              ],
            ),
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
    Navigator.push(
      context,
      MaterialPageRoute(builder: (_) => const NewGpsModeScreen()),
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
                fontWeight: FontWeight.w400,
                fontSize: 64,
                letterSpacing: 2,
              ),
        ),
        const SizedBox(height: 4),
        Text(
          'The World Is Your Documentary',
          style: TextStyle(
            color: Colors.white.withAlpha(153),
            fontSize: 18,
            letterSpacing: 0.5,
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
  final String backgroundImage;
  final VoidCallback onTap;

  const _ModeCard({
    required this.title,
    required this.subtitle,
    required this.icon,
    required this.backgroundImage,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: double.infinity,
        height: 120,
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(28),
          image: DecorationImage(
            image: AssetImage(backgroundImage),
            fit: BoxFit.cover,
          ),
          border: Border.all(
            color: Colors.white.withAlpha(51), // 0.2 opacity
            width: 1.5,
          ),
        ),
        child: Container(
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(28),
            gradient: LinearGradient(
              begin: Alignment.centerLeft,
              end: Alignment.centerRight,
              colors: [
                Colors.black.withAlpha(128),
                Colors.black.withAlpha(0),
              ],
            ),
          ),
          padding: const EdgeInsets.symmetric(horizontal: 24),
          child: Row(
            children: [
              Icon(icon, color: Colors.white, size: 36),
              const SizedBox(width: 20),
              Expanded(
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 24,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      subtitle,
                      style: TextStyle(
                        color: Colors.white.withAlpha(204),
                        fontSize: 14,
                        height: 1.2,
                      ),
                    ),
                  ],
                ),
              ),
              Icon(
                Icons.arrow_forward_ios,
                color: Colors.white.withAlpha(128),
                size: 18,
              ),
            ],
          ),
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
          style: TextStyle(
            color: Colors.white.withAlpha(128),
            fontSize: 16,
          ),
        ),
        const SizedBox(width: 16),
        Expanded(
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: DepthDial.values.map((dial) {
              final selected = dial == currentDial;
              return Expanded(
                child: GestureDetector(
                  onTap: () => ref.read(sessionProvider.notifier).setDepthDial(dial),
                  child: AnimatedContainer(
                    duration: const Duration(milliseconds: 300),
                    margin: const EdgeInsets.symmetric(horizontal: 4),
                    padding: const EdgeInsets.symmetric(vertical: 10),
                    decoration: BoxDecoration(
                      color: selected 
                          ? Colors.white.withAlpha(25) 
                          : Colors.transparent,
                      borderRadius: BorderRadius.circular(24),
                      border: Border.all(
                        color: selected 
                            ? const Color(0xFF9D50FF) 
                            : Colors.white.withAlpha(51),
                        width: 1.5,
                      ),
                      boxShadow: selected ? [
                        BoxShadow(
                          color: const Color(0xFF9D50FF).withAlpha(102),
                          blurRadius: 12,
                          spreadRadius: 1,
                        )
                      ] : null,
                    ),
                    alignment: Alignment.center,
                    child: Text(
                      dial.name[0].toUpperCase() + dial.name.substring(1),
                      style: TextStyle(
                        color: selected ? Colors.white : Colors.white.withAlpha(178),
                        fontSize: 14,
                        fontWeight: selected ? FontWeight.bold : FontWeight.normal,
                      ),
                    ),
                  ),
                ),
              );
            }).toList(),
          ),
        ),
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
          width: 10,
          height: 10,
          decoration: BoxDecoration(
            color: isConnected ? const Color(0xFF4CAF50) : const Color(0xFFE57373),
            shape: BoxShape.circle,
            boxShadow: [
              BoxShadow(
                color: (isConnected ? const Color(0xFF4CAF50) : const Color(0xFFE57373)).withAlpha(128),
                blurRadius: 6,
                spreadRadius: 1,
              )
            ],
          ),
        ),
        const SizedBox(width: 12),
        Text(
          isConnected ? 'Connected to LORE backend' : 'Disconnected',
          style: TextStyle(
            color: (isConnected ? const Color(0xFF4CAF50) : const Color(0xFFE57373)).withAlpha(204),
            fontSize: 14,
            letterSpacing: 0.2,
          ),
        ),
      ],
    );
  }
}
