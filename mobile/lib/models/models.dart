// Core data models for the LORE documentary app.
//
// These models mirror the backend Pydantic models and the WebSocket
// message protocol defined in design.md.

// ─── Enums ──────────────────────────────────────────────────────────────────

/// The three operating modes of the LORE app.
enum LoreMode { sight, voice, lore }

/// Depth-dial complexity levels for documentary content.
enum DepthDial { explorer, scholar, expert }

/// Types of content in a documentary stream.
enum ContentType { narration, video, illustration, fact, transition }

// ─── WebSocket message models ────────────────────────────────────────────────

/// Base class for all outgoing WebSocket messages (client → server).
abstract class WsClientMessage {
  String get type;
  Map<String, dynamic> toJson();
}

/// Sent once per session to select operating mode and preferences.
class ModeSelectMessage implements WsClientMessage {
  @override
  final String type = 'mode_select';

  final LoreMode mode;
  final DepthDial depthDial;
  final String language;

  const ModeSelectMessage({
    required this.mode,
    required this.depthDial,
    required this.language,
  });

  @override
  Map<String, dynamic> toJson() => {
        'type': type,
        'payload': {
          'mode': mode.name,
          'depthDial': depthDial.name,
          'language': language,
        },
      };
}

/// Sends a base-64-encoded JPEG camera frame to the server.
class CameraFrameMessage implements WsClientMessage {
  @override
  final String type = 'camera_frame';

  final String imageData; // base64 JPEG
  final int timestamp;
  final double? latitude;
  final double? longitude;

  const CameraFrameMessage({
    required this.imageData,
    required this.timestamp,
    this.latitude,
    this.longitude,
  });

  @override
  Map<String, dynamic> toJson() => {
        'type': type,
        'payload': {
          'imageData': imageData,
          'timestamp': timestamp,
          if (latitude != null && longitude != null)
            'gpsLocation': {'latitude': latitude, 'longitude': longitude},
        },
      };
}

/// Sends base-64-encoded PCM audio to the server.
class VoiceInputMessage implements WsClientMessage {
  @override
  final String type = 'voice_input';

  final String audioData; // base64 PCM
  final int sampleRate;
  final int timestamp;

  const VoiceInputMessage({
    required this.audioData,
    required this.sampleRate,
    required this.timestamp,
  });

  @override
  Map<String, dynamic> toJson() => {
        'type': type,
        'payload': {
          'audioData': audioData,
          'sampleRate': sampleRate,
          'timestamp': timestamp,
        },
      };
}

/// Sends the current GPS position to the server.
class GpsUpdateMessage implements WsClientMessage {
  @override
  final String type = 'gps_update';

  final double latitude;
  final double longitude;
  final double accuracy;
  final int timestamp;

  const GpsUpdateMessage({
    required this.latitude,
    required this.longitude,
    required this.accuracy,
    required this.timestamp,
  });

  @override
  Map<String, dynamic> toJson() => {
        'type': type,
        'payload': {
          'latitude': latitude,
          'longitude': longitude,
          'accuracy': accuracy,
          'timestamp': timestamp,
        },
      };
}

/// Signals a voice interruption (barge-in) during documentary playback.
class BargeInMessage implements WsClientMessage {
  @override
  final String type = 'barge_in';

  final String audioData;
  final int streamPosition;
  final int timestamp;

  const BargeInMessage({
    required this.audioData,
    required this.streamPosition,
    required this.timestamp,
  });

  @override
  Map<String, dynamic> toJson() => {
        'type': type,
        'payload': {
          'audioData': audioData,
          'streamPosition': streamPosition,
          'timestamp': timestamp,
        },
      };
}

/// Requests the server to change the depth-dial level.
class DepthDialChangeMessage implements WsClientMessage {
  @override
  final String type = 'depth_dial_change';

  final DepthDial newLevel;
  final int timestamp;

  const DepthDialChangeMessage({
    required this.newLevel,
    required this.timestamp,
  });

  @override
  Map<String, dynamic> toJson() => {
        'type': type,
        'payload': {
          'newLevel': newLevel.name,
          'timestamp': timestamp,
        },
      };
}

// ─── Server → Client message models ─────────────────────────────────────────

/// A single element in the documentary stream.
class DocumentaryStreamElement {
  final int sequenceId;
  final ContentType contentType;
  final Map<String, dynamic> content;
  final int timestamp;

  const DocumentaryStreamElement({
    required this.sequenceId,
    required this.contentType,
    required this.content,
    required this.timestamp,
  });

  factory DocumentaryStreamElement.fromJson(Map<String, dynamic> json) {
    final payload = json['payload'] as Map<String, dynamic>;
    return DocumentaryStreamElement(
      sequenceId: payload['sequenceId'] as int,
      contentType: ContentType.values.firstWhere(
        (e) => e.name == payload['contentType'],
        orElse: () => ContentType.narration,
      ),
      content: payload['content'] as Map<String, dynamic>,
      timestamp: payload['timestamp'] as int,
    );
  }
}

/// Location recognised by the server in SightMode.
class LocationRecognized {
  final Map<String, dynamic> place;
  final double confidence;
  final int timestamp;

  const LocationRecognized({
    required this.place,
    required this.confidence,
    required this.timestamp,
  });

  factory LocationRecognized.fromJson(Map<String, dynamic> json) {
    final payload = json['payload'] as Map<String, dynamic>;
    return LocationRecognized(
      place: payload['place'] as Map<String, dynamic>,
      confidence: (payload['confidence'] as num).toDouble(),
      timestamp: payload['timestamp'] as int,
    );
  }
}

/// GPS landmark detected by the server.
class LandmarkDetected {
  final Map<String, dynamic> landmark;
  final double distance;
  final bool autoTrigger;
  final int timestamp;

  const LandmarkDetected({
    required this.landmark,
    required this.distance,
    required this.autoTrigger,
    required this.timestamp,
  });

  factory LandmarkDetected.fromJson(Map<String, dynamic> json) {
    final payload = json['payload'] as Map<String, dynamic>;
    return LandmarkDetected(
      landmark: payload['landmark'] as Map<String, dynamic>,
      distance: (payload['distance'] as num).toDouble(),
      autoTrigger: payload['autoTrigger'] as bool,
      timestamp: payload['timestamp'] as int,
    );
  }
}

/// A server error notification.
class ServerError {
  final String errorCode;
  final String message;
  final bool degraded;

  const ServerError({
    required this.errorCode,
    required this.message,
    required this.degraded,
  });

  factory ServerError.fromJson(Map<String, dynamic> json) {
    final payload = json['payload'] as Map<String, dynamic>;
    return ServerError(
      errorCode: payload['errorCode'] as String,
      message: payload['message'] as String,
      degraded: payload['degraded'] as bool? ?? false,
    );
  }
}

// ─── App state models ────────────────────────────────────────────────────────

/// The overall app session state.
class SessionState {
  final String? sessionId;
  final LoreMode activeMode;
  final DepthDial depthDial;
  final String language;
  final List<DocumentaryStreamElement> streamElements;
  final bool isConnected;
  final String? errorMessage;

  const SessionState({
    this.sessionId,
    this.activeMode = LoreMode.sight,
    this.depthDial = DepthDial.scholar,
    this.language = 'en',
    this.streamElements = const [],
    this.isConnected = false,
    this.errorMessage,
  });

  SessionState copyWith({
    String? sessionId,
    LoreMode? activeMode,
    DepthDial? depthDial,
    String? language,
    List<DocumentaryStreamElement>? streamElements,
    bool? isConnected,
    String? errorMessage,
  }) {
    return SessionState(
      sessionId: sessionId ?? this.sessionId,
      activeMode: activeMode ?? this.activeMode,
      depthDial: depthDial ?? this.depthDial,
      language: language ?? this.language,
      streamElements: streamElements ?? this.streamElements,
      isConnected: isConnected ?? this.isConnected,
      errorMessage: errorMessage,
    );
  }
}
