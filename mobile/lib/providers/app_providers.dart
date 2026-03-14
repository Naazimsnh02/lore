/// Riverpod providers that wire together the app's services and state.
///
/// All providers are declared as `final` so they can be safely shared
/// across the widget tree without reinitialisation.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/models.dart';
import '../services/audio_playback_service.dart';
import '../services/auth_service.dart';
import '../services/camera_service.dart';
import '../services/gps_service.dart';
import '../services/microphone_service.dart';
import '../services/websocket_service.dart';

// ── Singleton services ───────────────────────────────────────────────────────

final authServiceProvider = Provider<AuthService>((_) => AuthService());

final cameraServiceProvider = Provider<CameraService>((_) => CameraService());

final microphoneServiceProvider = Provider<MicrophoneService>(
  (_) => MicrophoneService(),
);

final gpsServiceProvider = Provider<GpsService>((_) => GpsService());

final webSocketServiceProvider = Provider<WebSocketService>(
  (_) => WebSocketService(),
);

final audioPlaybackServiceProvider = Provider<AudioPlaybackService>(
  (_) => AudioPlaybackService(),
);

// ── Preferences ──────────────────────────────────────────────────────────────

/// Async provider for [SharedPreferences] — loaded once at startup.
final sharedPrefsProvider = FutureProvider<SharedPreferences>(
  (_) => SharedPreferences.getInstance(),
);

// ── Session state ────────────────────────────────────────────────────────────

/// The central session state notifier.
class SessionNotifier extends Notifier<SessionState> {
  @override
  SessionState build() => const SessionState();

  void setMode(LoreMode mode) => state = state.copyWith(activeMode: mode);

  void setDepthDial(DepthDial dial) => state = state.copyWith(depthDial: dial);

  void setLanguage(String language) =>
      state = state.copyWith(language: language);

  void setConnected(bool connected) =>
      state = state.copyWith(isConnected: connected);

  void setError(String? message) =>
      state = state.copyWith(errorMessage: message);

  void addStreamElement(DocumentaryStreamElement element) {
    state = state.copyWith(streamElements: [...state.streamElements, element]);
  }

  void clearStream() => state = state.copyWith(streamElements: []);

  void setSessionId(String id) => state = state.copyWith(sessionId: id);

  // ── Conversation management ────────────────────────────────────────────

  /// Add a message to the conversation history.
  void addConversationMessage(ConversationMessage message) {
    state = state.copyWith(
      conversationHistory: [...state.conversationHistory, message],
    );
  }

  /// Append text to the last assistant message in place (for partial streaming).
  ///
  /// If the last message is already an assistant message, its text is extended.
  /// Otherwise a new assistant message is created. This mirrors the reference
  /// script's print(text, end="") behaviour — one text box that grows as the
  /// model speaks rather than a new bubble per word.
  void appendToLastAssistantMessage(
    String text, {
    String? topic,
    int branchDepth = 0,
  }) {
    final history = state.conversationHistory;
    if (history.isNotEmpty && history.last.role == ConversationRole.assistant) {
      final updated = history.last.copyWith(
        text: history.last.text + text,
        topic: topic ?? history.last.topic,
        branchDepth: branchDepth,
      );
      state = state.copyWith(
        conversationHistory: [...history.sublist(0, history.length - 1), updated],
      );
    } else {
      // No existing assistant message — create one
      state = state.copyWith(
        conversationHistory: [
          ...history,
          ConversationMessage(
            id: DateTime.now().millisecondsSinceEpoch.toString(),
            role: ConversationRole.assistant,
            text: text,
            timestamp: DateTime.now(),
            topic: topic,
            branchDepth: branchDepth,
          ),
        ],
      );
    }
  }

  /// Clear the conversation history (e.g. on session reset).
  void clearConversation() =>
      state = state.copyWith(conversationHistory: [], branchDepth: 0);

  /// Update whether narration audio is currently playing.
  void setNarrationPlaying(bool playing) =>
      state = state.copyWith(isNarrationPlaying: playing);

  /// Update the current branch depth.
  void setBranchDepth(int depth) => state = state.copyWith(branchDepth: depth);
}

final sessionProvider = NotifierProvider<SessionNotifier, SessionState>(
  () => SessionNotifier(),
);
