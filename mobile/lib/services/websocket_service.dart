/// WebSocket client service for bidirectional communication with the LORE backend.
///
/// Responsibilities (Requirements 20.1, 24.6):
/// - Maintain a persistent WebSocket connection to the Cloud Run gateway
/// - Auto-reconnect with exponential backoff on disconnection
/// - Buffer outgoing messages while disconnected (up to 30 seconds of traffic)
/// - Deserialise incoming server messages and expose them as a Stream
library;

import 'dart:async';
import 'dart:convert';
import 'package:logging/logging.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../models/models.dart';

/// Events emitted to the app layer from the WebSocket service.
sealed class WsEvent {}

class WsConnectedEvent extends WsEvent {}

class WsDisconnectedEvent extends WsEvent {}

class WsDocumentaryContentEvent extends WsEvent {
  final DocumentaryStreamElement element;
  WsDocumentaryContentEvent(this.element);
}

class WsLocationRecognizedEvent extends WsEvent {
  final LocationRecognized location;
  WsLocationRecognizedEvent(this.location);
}

class WsLandmarkDetectedEvent extends WsEvent {
  final LandmarkDetected landmark;
  WsLandmarkDetectedEvent(this.landmark);
}

class WsDirectionsEvent extends WsEvent {
  final DirectionsResponse directions;
  WsDirectionsEvent(this.directions);
}

class WsErrorEvent extends WsEvent {
  final ServerError error;
  WsErrorEvent(this.error);
}

class WsRawEvent extends WsEvent {
  final Map<String, dynamic> json;
  WsRawEvent(this.json);
}

/// Manages the WebSocket connection lifecycle and message dispatch.
class WebSocketService {
  static const int _maxReconnectAttempts = 10;
  static const Duration _initialReconnectDelay = Duration(seconds: 1);
  // Outgoing message buffer: hold up to 30 seconds of messages (≈ 500 items)
  static const int _bufferMaxSize = 500;

  final _log = Logger('WebSocketService');
  final _eventController = StreamController<WsEvent>.broadcast();

  /// Stream of parsed events from the server.
  Stream<WsEvent> get events => _eventController.stream;

  String? _gatewayUrl;
  String? _authToken;

  WebSocketChannel? _channel;
  StreamSubscription? _channelSub;
  Timer? _reconnectTimer;

  int _reconnectAttempt = 0;
  bool _disposed = false;

  /// Pending outgoing messages accumulated while disconnected.
  final List<Map<String, dynamic>> _outgoingBuffer = [];

  // ── Public API ───────────────────────────────────────────────────────────

  /// Connect to [url] using Firebase ID [token] for authentication.
  ///
  /// The token is passed as a query parameter (?token=...) so the server
  /// can validate it before accepting the WebSocket handshake.
  Future<void> connect(String url, String token) async {
    _gatewayUrl = url;
    _authToken = token;
    _reconnectAttempt = 0;
    await _connect();
  }

  /// Send a typed client message. Buffers the message when disconnected.
  void send(WsClientMessage message) {
    final json = message.toJson();
    if (_channel == null) {
      _bufferMessage(json);
    } else {
      try {
        _channel!.sink.add(jsonEncode(json));
      } catch (e) {
        _log.warning('Failed to send message, buffering: $e');
        _bufferMessage(json);
      }
    }
  }

  /// Disconnect cleanly and stop auto-reconnect.
  Future<void> disconnect() async {
    _disposed = true;
    _reconnectTimer?.cancel();
    await _channelSub?.cancel();
    await _channel?.sink.close();
    _channel = null;
    _eventController.add(WsDisconnectedEvent());
  }

  /// Release all resources. Must be called when the service is no longer needed.
  Future<void> dispose() async {
    await disconnect();
    await _eventController.close();
  }

  // ── Internal connection management ───────────────────────────────────────

  Future<void> _connect() async {
    if (_disposed || _gatewayUrl == null) return;

    final uri = Uri.parse('$_gatewayUrl?token=$_authToken');
    _log.info('Connecting to WebSocket: $uri');

    try {
      _channel = WebSocketChannel.connect(uri);
      await _channel!.ready;

      _reconnectAttempt = 0;
      _log.info('WebSocket connected');
      _eventController.add(WsConnectedEvent());

      // Flush buffered outgoing messages
      _flushBuffer();

      // Subscribe to incoming messages
      _channelSub = _channel!.stream.listen(
        _onMessage,
        onError: _onError,
        onDone: _onDone,
        cancelOnError: false,
      );
    } catch (e) {
      _log.warning('Connection failed: $e');
      _scheduleReconnect();
    }
  }

  void _onMessage(dynamic raw) {
    try {
      final json = jsonDecode(raw as String) as Map<String, dynamic>;
      final type = json['type'] as String?;

      switch (type) {
        case 'documentary_content':
          _eventController
              .add(WsDocumentaryContentEvent(DocumentaryStreamElement.fromJson(json)));
        case 'location_recognized':
          _eventController
              .add(WsLocationRecognizedEvent(LocationRecognized.fromJson(json)));
        case 'landmark_detected':
          _eventController
              .add(WsLandmarkDetectedEvent(LandmarkDetected.fromJson(json)));
        case 'directions':
          _eventController
              .add(WsDirectionsEvent(DirectionsResponse.fromJson(json)));
        case 'error':
          _eventController.add(WsErrorEvent(ServerError.fromJson(json)));
        default:
          // Forward unknown messages as raw events for extensibility
          _eventController.add(WsRawEvent(json));
      }
    } catch (e) {
      _log.warning('Failed to parse incoming message: $e\nRaw: $raw');
    }
  }

  void _onError(Object error, StackTrace stack) {
    _log.warning('WebSocket error: $error');
    _scheduleReconnect();
  }

  void _onDone() {
    _log.info('WebSocket connection closed');
    _channel = null;
    _eventController.add(WsDisconnectedEvent());
    _scheduleReconnect();
  }

  void _scheduleReconnect() {
    if (_disposed || _reconnectAttempt >= _maxReconnectAttempts) return;

    // Exponential backoff: 1s, 2s, 4s, 8s … capped at 30s
    final delay =
        _initialReconnectDelay * (1 << _reconnectAttempt.clamp(0, 5));
    _reconnectAttempt++;
    _log.info('Reconnecting in ${delay.inSeconds}s (attempt $_reconnectAttempt)');

    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(delay, _connect);
  }

  void _bufferMessage(Map<String, dynamic> json) {
    if (_outgoingBuffer.length >= _bufferMaxSize) {
      // Drop oldest message to stay within 30-second capacity
      _outgoingBuffer.removeAt(0);
    }
    _outgoingBuffer.add(json);
  }

  void _flushBuffer() {
    if (_outgoingBuffer.isEmpty || _channel == null) return;
    _log.info('Flushing ${_outgoingBuffer.length} buffered messages');
    for (final msg in List.of(_outgoingBuffer)) {
      try {
        _channel!.sink.add(jsonEncode(msg));
      } catch (_) {
        break; // Stop flush if the channel drops again
      }
    }
    _outgoingBuffer.clear();
  }
}
