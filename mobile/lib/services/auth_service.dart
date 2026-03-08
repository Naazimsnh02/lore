/// Firebase anonymous authentication service.
///
/// Requirements 25.1, 25.2:
/// - Obtain a Firebase ID token for WebSocket authentication
/// - Refresh the token before the 24-hour expiry
library;

import 'package:firebase_auth/firebase_auth.dart';
import 'package:logging/logging.dart';

/// Wraps Firebase Auth to provide anonymous sign-in and token refresh.
class AuthService {
  final _log = Logger('AuthService');
  final FirebaseAuth _auth = FirebaseAuth.instance;

  User? get currentUser => _auth.currentUser;

  /// Sign in anonymously if not already signed in.
  ///
  /// Returns the Firebase ID token for use as the WebSocket auth token.
  Future<String?> signInAnonymously() async {
    try {
      if (_auth.currentUser == null) {
        await _auth.signInAnonymously();
        _log.info('Anonymous sign-in successful: ${_auth.currentUser?.uid}');
      }
      return await _auth.currentUser?.getIdToken();
    } catch (e) {
      _log.severe('Anonymous sign-in failed: $e');
      return null;
    }
  }

  /// Retrieve a (possibly refreshed) ID token.
  ///
  /// Pass [forceRefresh: true] when the token is near expiry.
  Future<String?> getToken({bool forceRefresh = false}) async {
    try {
      return await _auth.currentUser?.getIdToken(forceRefresh);
    } catch (e) {
      _log.warning('Token retrieval failed: $e');
      return null;
    }
  }

  /// Sign out and clear the local session.
  Future<void> signOut() async {
    await _auth.signOut();
    _log.info('Signed out');
  }
}
