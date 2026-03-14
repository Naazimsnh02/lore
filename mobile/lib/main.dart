/// LORE — The World Is Your Documentary
///
/// Application entry point.
/// Initialises Firebase, sets up Riverpod, and launches the root widget.
library;

import 'package:firebase_core/firebase_core.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:logging/logging.dart';

import 'firebase_options.dart';
import 'providers/app_providers.dart';
import 'screens/home_screen.dart';
import 'services/websocket_service.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // Configure structured logging
  Logger.root.level = Level.ALL;
  Logger.root.onRecord.listen((record) {
    // ignore: avoid_print
    print('[${record.level.name}] ${record.loggerName}: ${record.message}');
  });

  // Initialise Firebase (generated credentials in firebase_options.dart)
  await Firebase.initializeApp(options: DefaultFirebaseOptions.currentPlatform);

  runApp(
    // ProviderScope is required for all Riverpod providers
    const ProviderScope(child: LoreApp()),
  );
}

class LoreApp extends StatelessWidget {
  const LoreApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'LORE',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: Colors.deepPurple,
          brightness: Brightness.dark,
        ),
        useMaterial3: true,
        fontFamily: 'Roboto',
      ),
      home: const _AppRoot(),
    );
  }
}

/// Handles anonymous sign-in before showing the home screen.
class _AppRoot extends ConsumerStatefulWidget {
  const _AppRoot();

  @override
  ConsumerState<_AppRoot> createState() => _AppRootState();
}

class _AppRootState extends ConsumerState<_AppRoot> {
  bool _ready = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _initialise();
  }

  Future<void> _initialise() async {
    try {
      // 1. Obtain Firebase anonymous ID token.
      final authService = ref.read(authServiceProvider);
      final token = await authService.signInAnonymously();

      // 2. Connect the WebSocket gateway.
      //    The URL is injected at build time via --dart-define.
      const gatewayUrl = String.fromEnvironment(
        'WEBSOCKET_GATEWAY_URL',
        defaultValue:
            'ws://10.0.2.2:8080/ws', // Android emulator → host loopback
      );

      final wsService = ref.read(webSocketServiceProvider);

      // Mirror WsConnected/WsDisconnected into the session notifier so the
      // home-screen indicator updates correctly.
      wsService.events.listen((event) {
        if (!mounted) return;
        final notifier = ref.read(sessionProvider.notifier);
        if (event is WsConnectedEvent) {
          notifier.setConnected(true);
        } else if (event is WsDisconnectedEvent) {
          notifier.setConnected(false);
        }
      });

      // Use a mock token when the gateway is running in mock-auth mode.
      final effectiveToken = token ?? 'mock_anonymous';
      await wsService.connect(gatewayUrl, effectiveToken);

      setState(() => _ready = true);
    } catch (e) {
      setState(() => _error = e.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) {
      return Scaffold(
        backgroundColor: Colors.black,
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                const Icon(
                  Icons.error_outline,
                  color: Colors.redAccent,
                  size: 48,
                ),
                const SizedBox(height: 16),
                Text(
                  'Initialisation failed:\n$_error',
                  style: const TextStyle(color: Colors.white70),
                  textAlign: TextAlign.center,
                ),
                const SizedBox(height: 24),
                ElevatedButton(
                  onPressed: () {
                    setState(() {
                      _error = null;
                      _ready = false;
                    });
                    _initialise();
                  },
                  child: const Text('Retry'),
                ),
              ],
            ),
          ),
        ),
      );
    }

    if (!_ready) {
      return const Scaffold(
        backgroundColor: Colors.black,
        body: Center(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Text(
                'LORE',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 48,
                  fontWeight: FontWeight.bold,
                  letterSpacing: 6,
                ),
              ),
              SizedBox(height: 32),
              CircularProgressIndicator(color: Colors.deepPurpleAccent),
            ],
          ),
        ),
      );
    }

    return const HomeScreen();
  }
}
