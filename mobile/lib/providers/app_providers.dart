/// Riverpod providers that wire together the app's services and state.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/models.dart';
import '../services/auth_service.dart';
import '../services/camera_service.dart';

// ── Singleton services ───────────────────────────────────────────────────────

final authServiceProvider = Provider<AuthService>((_) => AuthService());

final cameraServiceProvider = Provider<CameraService>((_) => CameraService());

// ── Preferences ──────────────────────────────────────────────────────────────

final sharedPrefsProvider = FutureProvider<SharedPreferences>(
  (_) => SharedPreferences.getInstance(),
);

// ── Session state ────────────────────────────────────────────────────────────

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

  void addConversationMessage(ConversationMessage message) {
    state = state.copyWith(
      conversationHistory: [...state.conversationHistory, message],
    );
  }

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

  void clearConversation() =>
      state = state.copyWith(conversationHistory: [], branchDepth: 0);

  void setNarrationPlaying(bool playing) =>
      state = state.copyWith(isNarrationPlaying: playing);

  void setBranchDepth(int depth) => state = state.copyWith(branchDepth: depth);
}

final sessionProvider = NotifierProvider<SessionNotifier, SessionState>(
  () => SessionNotifier(),
);
