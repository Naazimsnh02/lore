/// Home screen — mode selection and session entry point.
///
/// Requirement 1.2: System displays mode selection options on launch.
library;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'new_voice_mode_screen.dart';
import 'sight_mode_screen.dart';
import 'lore_mode_screen.dart';
import 'new_gps_mode_screen.dart';

class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [
              Color(0xFF0D1B3E),
              Color(0xFF020509),
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
                const _LoreHeader(),
                const SizedBox(height: 48),
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
                          onTap: () => Navigator.push(context,
                              MaterialPageRoute(builder: (_) => const SightModeScreen())),
                        ),
                        const SizedBox(height: 16),
                        _ModeCard(
                          title: 'VoiceMode',
                          subtitle: 'Speak any topic for an instant documentary',
                          icon: Icons.mic_outlined,
                          backgroundImage: 'assets/images/VoiceMode.png',
                          onTap: () => Navigator.push(context,
                              MaterialPageRoute(builder: (_) => const NewVoiceModeScreen())),
                        ),
                        const SizedBox(height: 16),
                        _ModeCard(
                          title: 'LoreMode',
                          subtitle: 'Camera + Voice fusion — unlocks Alternate History',
                          icon: Icons.auto_awesome_outlined,
                          backgroundImage: 'assets/images/LoreMode.png',
                          onTap: () => Navigator.push(context,
                              MaterialPageRoute(builder: (_) => const LoreModeScreen())),
                        ),
                        const SizedBox(height: 16),
                        _ModeCard(
                          title: 'GPS Walking Tour',
                          subtitle: 'Auto-discover landmarks as you walk',
                          icon: Icons.map_outlined,
                          backgroundImage: 'assets/images/GPSMode.png',
                          onTap: () => Navigator.push(context,
                              MaterialPageRoute(builder: (_) => const NewGpsModeScreen())),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
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
            color: Colors.white.withAlpha(51),
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
