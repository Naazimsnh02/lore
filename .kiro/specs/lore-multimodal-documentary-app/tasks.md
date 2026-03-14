# Implementation Plan: LORE - The World Is Your Documentary

## Overview

This implementation plan breaks down the LORE multimodal documentary app into actionable coding tasks across 7 phases. The system uses Python for backend services (WebSocket Gateway, Orchestrator, Generation Agents), Dart/Flutter for mobile frontend, and integrates with Google Cloud Platform services (Gemini Live API, Vertex AI, Firestore, Cloud Storage, Cloud Pub/Sub).

The implementation follows the design document's 7-phase roadmap, with each phase building incrementally on previous work. Tasks reference specific requirements and include property-based testing for all 25 correctness properties.

## Implementation Language

- **Backend Services**: Python 3.11+ (asyncio, FastAPI, ADK)
- **Mobile Frontend**: Dart/Flutter
- **Infrastructure**: Google Cloud Platform (Cloud Run, Vertex AI, Firestore, Cloud Storage)

## Tasks

### Phase 1: Core Infrastructure (Weeks 1-2)

- [x] 1. Set up Google Cloud Platform project and infrastructure (gloud cli installed)
  - Create GCP project with billing enabled
  - Enable required APIs: Cloud Run, Vertex AI, Firestore, Cloud Storage, Pub/Sub, Identity Platform
  - Set up service accounts with appropriate IAM roles
  - Configure Cloud Logging and Cloud Monitoring
  - Create Firestore database in multi-region mode
  - Create Cloud Storage bucket for media files
  - Set up Cloud Pub/Sub topics: narration-tasks, video-tasks, illustration-tasks, search-tasks
  - _Requirements: 20.1, 21.6, 22.1, 23.1, 26.1_
  - **Firebase setup (done via CLI/API):**
    - Firebase linked to GCP project via `firebase projects:addfirebase geminiliveagent-487800`
    - Firebase Android app created (package: com.lore.documentary, appId: 1:469176118408:android:2736c8f0825026f05c833d)
    - Firebase iOS app created (bundleId: com.lore.documentary)
    - Firebase Web API Key: AIzaSyAfVAWEEU8xszrDhJbz7naH6E1GMGxUJd8
  - **✅ Anonymous Auth fully configured:**
    - Firebase Console Authentication initialized (manual "Get started" click done)
    - Anonymous provider enabled via Identity Platform REST API (PATCH config)
    - Verified: anonymous sign-in returns valid Firebase ID tokens
    - API Key: AIzaSyAfVAWEEU8xszrDhJbz7naH6E1GMGxUJd8

- [x] 2. Implement WebSocket Gateway service
  - [x] 2.1 Create FastAPI WebSocket server with Cloud Run deployment
    - Set up FastAPI application with WebSocket endpoint
    - Implement connection management (connect, disconnect, message routing)
    - Add health check and readiness endpoints
    - Create Dockerfile for Cloud Run deployment
    - Configure auto-scaling (min: 2, max: 100 instances)
    - _Requirements: 20.1, 20.2, 20.6_

  - [x]* 2.2 Write property test for WebSocket message latency
    - **Property 18: WebSocket Message Latency**
    - **Validates: Requirements 20.7**
    - Test that message latency < 100ms under normal conditions
    - Use Hypothesis to generate random message payloads
    - Measure round-trip time for 100+ message samples

  - [x] 2.3 Implement authentication using Google Cloud Identity Platform
    - Integrate Identity Platform SDK
    - Validate JWT tokens on WebSocket connection
    - Implement session timeout (24 hours)
    - Add user ID extraction from tokens
    - _Requirements: 25.1, 25.2, 25.6_

  - [x]* 2.4 Write property test for authentication security
    - **Property 21: Authentication Security**
    - **Validates: Requirements 25.7**
    - Test that all auth operations use HTTPS/TLS
    - Verify token validation rejects invalid tokens
    - Test session timeout enforcement

  - [x] 2.5 Implement message buffering for connection failures
    - Create buffer with 30-second capacity
    - Implement buffer flush on reconnection
    - Add buffer overflow handling
    - _Requirements: 20.4, 20.5_

- [x] 3. Implement Session Memory Manager with Firestore
  - [x] 3.1 Create Firestore schema and data models
    - Define SessionDocument, LocationVisit, UserInteraction, ContentRef, BranchNode models
    - Implement Pydantic models for validation
    - Create indexes for userId and sessionId
    - _Requirements: 10.1, 10.2_

  - [x] 3.2 Implement SessionMemoryManager class
    - Implement createSession, loadSession, updateSession, deleteSession methods
    - Add cross-session query support
    - Implement user data deletion
    - Add encryption for data at rest and in transit
    - _Requirements: 10.3, 10.4, 10.5, 10.6, 10.7_

  - [x]* 3.3 Write property test for session memory completeness
    - **Property 14: Session Memory Completeness**
    - **Validates: Requirements 10.1**
    - Test that all interactions, locations, and content are stored
    - Generate random session data and verify persistence
    - Test round-trip: create → store → retrieve → verify

  - [x]* 3.4 Write property test for session memory encryption
    - **Property 15: Session Memory Encryption**
    - **Validates: Requirements 10.7**
    - Test that data is encrypted at rest in Firestore
    - Test that data is encrypted in transit (TLS)
    - Verify encryption keys are properly managed

- [x] 4. Implement Media Store Manager with Cloud Storage
  - [x] 4.1 Create MediaStoreManager class
    - Implement storeMedia, retrieveMedia, deleteMedia methods
    - Implement signed URL generation with expiration
    - Add quota management (getUserQuota, cleanupOldMedia)
    - Organize files by userId/sessionId structure
    - _Requirements: 22.2, 22.3, 22.4, 22.5, 22.6_

  - [x]* 4.2 Write property test for media retrieval latency
    - **Property 20: Media Retrieval Latency**
    - **Validates: Requirements 22.7**
    - Test that 95% of retrievals complete within 500ms
    - Generate random media files and measure retrieval times
    - Run 100+ iterations to verify percentile

- [x] 5. Create Flutter mobile app skeleton
  - [x] 5.1 Set up Flutter project with required dependencies
    - Initialize Flutter project with null safety
    - Add dependencies: camera, microphone, geolocator, web_socket_channel
    - Configure iOS and Android permissions for camera, mic, GPS
    - Set up state management (Riverpod)
    - _Requirements: 24.1, 24.2, 24.3, 24.4_
    - **Files**: mobile/pubspec.yaml, mobile/lib/firebase_options.dart
    - **Android**: mobile/android/app/build.gradle.kts (minSdk=21), AndroidManifest.xml
    - **iOS**: mobile/ios/Runner/Info.plist (NSCamera/Microphone/LocationUsageDescription)

  - [x] 5.2 Implement camera, microphone, and GPS access
    - Create CameraService for frame capture at 1 fps
    - Create MicrophoneService for audio streaming (PCM 16kHz mono)
    - Create GPSService for location monitoring (< 10 m accuracy)
    - Add permission request flows (permission_handler)
    - _Requirements: 24.2, 24.3, 24.4_
    - **Files**: mobile/lib/services/camera_service.dart, microphone_service.dart, gps_service.dart

  - [x] 5.3 Implement WebSocket client connection
    - Create WebSocketService for bidirectional communication
    - Implement connection management with auto-reconnect (exponential backoff)
    - Add message serialization/deserialization (typed WsClientMessage / WsEvent)
    - Implement outgoing message buffer (500 msgs / 30 s capacity) for offline support
    - _Requirements: 20.1, 24.6_
    - **Files**: mobile/lib/services/websocket_service.dart
    - **Screens**: home_screen.dart, sight_mode_screen.dart, voice_mode_screen.dart, lore_mode_screen.dart
    - **Widgets**: documentary_stream_widget.dart, mode_switcher_widget.dart
    - **Providers**: app_providers.dart (Riverpod StateNotifier for SessionState)
    - **Models**: models.dart (enums, WS message types, SessionState)

- [x] 6. Checkpoint - Verify core infrastructure
  - Ensure all tests pass, ask the user if questions arise.


### Phase 2: SightMode Implementation (Weeks 3-4)

- [x] 7. Implement Location Recognizer service
  - [x] 7.1 Create LocationRecognizer class with Google Places API integration
    - Implement recognizeLocation method for visual recognition
    - Integrate Google Places API for location details
    - Add confidence scoring for matches
    - Implement timeout handling (3 seconds)
    - _Requirements: 2.2, 2.4_
    - **Files**: `backend/services/location_recognizer/recognizer.py`, `models.py`, `__init__.py`, `requirements.txt`
    - Two-stage pipeline: Gemini Vision (feature extraction) → Places API v1 text search
    - Confidence = Gemini score (40%) + name similarity (40%) + landmark type bonus (20%)
    - asyncio.wait_for enforces 3 s hard timeout; all API errors handled gracefully

  - [x]* 7.2 Write property test for camera frame processing latency
    - **Property 2: Camera Frame Processing Latency**
    - **Validates: Requirements 2.2**
    - Test that location identification completes within 3 seconds
    - Generate random camera frames and measure processing time
    - Run 10+ iterations to verify latency constraint
    - **Files**: `backend/tests/properties/test_location_recognizer_latency.py`
    - **Unit tests**: `backend/tests/unit/test_location_recognizer.py` (20 tests, all passing)

- [x] 8. Implement SightMode handler
  - [x] 8.1 Create SightModeHandler class
    - Implement camera frame processing pipeline
    - Implement lighting condition checks
    - Add flash suggestion logic
    - Implement voice clarification prompt after 5 seconds
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_
    - **Files**: `backend/services/sight_mode/handler.py`, `models.py`, `__init__.py`, `requirements.txt`
    - SightModeHandler: process_frame() → SightModeResponse (DOCUMENTARY_TRIGGER / FLASH_SUGGESTION / VOICE_CLARIFICATION / FRAME_BUFFERED)
    - Duplicate trigger suppression (30 s window for same place_id)
    - Voice clarification prompt after 5 s of continuous non-recognition (sent once)
    - Optional async callback on_documentary_trigger for Orchestrator integration
    - Delegates recognition to LocationRecognizer (Task 7)

  - [x] 8.1.1 Implement FrameBuffer for improved recognition
    - Create FrameBuffer class with 5-frame capacity
    - Implement frame quality scoring
    - Add get_best_frame method for recognition
    - Integrate with SightModeHandler
    - _Requirements: 2.2, 2.4_
    - **Files**: `backend/services/sight_mode/frame_buffer.py`
    - Quality score = 60% brightness + 40% file size heuristic
    - Brightness estimation from sampled bytes (lightweight, no PIL/OpenCV dep)
    - check_lighting() with configurable threshold (default 30)

  - [x]* 8.2 Write property test for frame rate maintenance
    - **Property 25: Frame Rate Maintenance**
    - **Validates: Requirements 2.1**
    - Test that frames are captured at minimum 1 fps
    - Measure frame capture intervals over time
    - Verify consistency across different devices
    - **Files**: `backend/tests/properties/test_frame_rate_maintenance.py`
    - **Unit tests**: `backend/tests/unit/test_sight_mode.py` (25 tests, all passing)

- [x] 9. Implement Narration Engine with Gemini Live API
  - [x] 9.1 Create NarrationEngine class
    - Integrate Gemini Live API for voice synthesis
    - Implement generateScript method with depth dial support
    - Implement synthesizeSpeech with native audio output
    - Add streaming support for real-time narration
    - _Requirements: 3.1, 3.2, 5.2, 11.5_
    - **Files**: `backend/services/narration_engine/engine.py`, `models.py`
    - **Models**: NarrationEngine (generate_script, synthesize_speech, translate_script, generate_narration)
    - **Live API model**: `gemini-live-2.5-flash-native-audio` (GA)
    - **Script model**: `gemini-3-flash-preview`
    - Audio output: 24 kHz / 16-bit / mono PCM, streamed as AudioChunk objects

  - [x] 9.2 Implement Affective Narrator module
    - Create AffectiveNarrator class with tone profiles
    - Implement determineEmotionalTone based on context
    - Add tone adaptation (respectful, enthusiastic, contemplative, neutral)
    - Apply voice parameters (speaking rate, pitch, volume)
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.6_
    - **Files**: `backend/services/narration_engine/affective_narrator.py`
    - **Voices**: Charon (respectful), Puck (enthusiastic), Enceladus (contemplative), Kore (neutral)
    - Tone via prompt engineering (system instruction) + voice name selection

  - [x]* 9.3 Write unit tests for affective narration
    - Test tone detection for different location types
    - Test voice parameter application
    - Test sentiment analysis accuracy
    - _Requirements: 11.1, 11.2, 11.3_
    - **Unit tests**: `backend/tests/unit/test_narration_engine.py` (56 tests, all passing)

- [x] 10. Implement Nano Illustrator service
  - [x] 10.1 Create NanoIllustrator class with Vertex AI integration
    - Integrate Gemini 3.1 Flash Image Preview API
    - Implement generateIllustration method
    - Add style management for consistency
    - Implement period-appropriate style generation
    - Store illustrations in Media Store
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_
    - **Files**: `backend/services/nano_illustrator/illustrator.py`, `models.py`, `__init__.py`, `requirements.txt`
    - Uses `google-genai` SDK `generate_content` with `response_modalities=['IMAGE']` and `image_size='1K'`
    - Per-session style cache ensures consistency (Req 7.6)
    - Style determination: historical period → place type → majority vote → ILLUSTRATED default
    - Graceful degradation on timeout/error (returns IllustrationResult with error field)
    - Optional MediaStoreManager integration for Cloud Storage persistence (Req 7.5)

  - [x]* 10.2 Write property test for illustration generation latency
    - **Property 8: Illustration Generation Latency**
    - **Validates: Requirements 7.2**
    - Test that generation completes within 2 seconds
    - Generate random concept descriptions
    - Measure generation time for 100+ samples
    - **Files**: `backend/tests/properties/test_illustration_latency.py`

  - [x]* 10.3 Write property test for illustration quality constraints
    - **Property 9: Illustration Quality Constraints**
    - **Validates: Requirements 7.3**
    - Test that all illustrations are minimum 1024x1024 pixels
    - Verify resolution for randomly generated illustrations
    - Check image format and quality
    - **Files**: `backend/tests/properties/test_illustration_quality.py`

  - [x]* 10.4 Write property test for illustration style consistency
    - **Property 10: Illustration Style Consistency**
    - **Validates: Requirements 7.6**
    - Test that all illustrations in a session have consistent style
    - Generate multiple illustrations per session
    - Verify style parameters match across illustrations
    - **Files**: `backend/tests/properties/test_illustration_style_consistency.py`
    - **Unit tests**: `backend/tests/unit/test_nano_illustrator.py` (39 tests, all passing)

- [x] 11. Implement Search Grounder service
  - [x] 11.1 Create SearchGrounder class with Google Search Grounding API
    - Integrate Google Search Grounding API
    - Implement verifyFact and verifyBatch methods
    - Add source ranking by authority
    - Implement conflict detection and multiple perspectives
    - _Requirements: 8.1, 8.2, 8.3, 8.5, 8.6_
    - **Unit tests**: `backend/tests/unit/test_search_grounder.py` (41 tests, all passing)

  - [x]* 11.2 Write property test for fact verification completeness
    - **Property 11: Fact Verification Completeness**
    - **Validates: Requirements 8.1, 8.2**
    - Test that all claims are either verified or marked unverified
    - Generate random factual claims
    - Verify each has verification status and sources (if verified)
    - **Property tests**: `backend/tests/properties/test_fact_verification_completeness.py` (4 tests × 100 iterations, all passing)

- [x] 12. Implement Orchestrator with ADK
  - [x] 12.1 Create Orchestrator class using ADK framework
    - Set up ADK agent with Gemini 3 Flash Preview
    - Implement processRequest method
    - Add task decomposition logic
    - Implement parallel task dispatch via asyncio.gather
    - Add result assembly and stream creation
    - _Requirements: 21.1, 21.2, 21.3_
    - **Files**: `backend/services/orchestrator/orchestrator.py`, `models.py`, `stream_assembler.py`, `__init__.py`, `requirements.txt`
    - DocumentaryOrchestrator: process_request() routes to mode-specific workflows
    - Parallel execution: narration + illustration + search via asyncio.gather (Req 21.2)
    - Retry with exponential backoff: 3 attempts, 0.5s→1s→2s (Req 21.5)
    - Graceful degradation: failed services don't crash the pipeline (Req 29.1)
    - StreamAssembler: interleaves narration/illustration/fact elements (Req 5.1, 5.3)
    - on_stream_element callback for real-time WebSocket push

  - [x] 12.2 Implement SightMode workflow in Orchestrator
    - Add sight_mode_workflow method
    - Integrate LocationRecognizer, NarrationEngine, NanoIllustrator, SearchGrounder
    - Implement parallel content generation
    - Add error handling with retries
    - _Requirements: 2.1, 2.2, 2.3, 5.1_

  - [x] 12.6 Implement all workflow methods in Orchestrator
    - Implement sight_mode_workflow method
    - Implement voice_mode_workflow method
    - Implement lore_mode_workflow method
    - Implement alternate_history_workflow method
    - Implement branch_documentary_workflow method (depth ≤ 3, Req 13.4)
    - Add workflow routing based on mode and context
    - Add mode determination from inputs (camera+voice → LORE)
    - _Requirements: 1.1, 2.1, 3.1, 4.1, 13.1, 15.1_

  - [x]* 12.3 Write property test for input-to-output latency
    - **Property 5: Input-to-Output Latency**
    - **Validates: Requirements 5.7**
    - Test that first output appears within 3 seconds of input
    - Generate random documentary requests
    - Measure end-to-end latency for 100+ samples
    - **Files**: `backend/tests/properties/test_orchestrator_properties.py`

  - [x]* 12.4 Write property test for agent retry behavior
    - **Property 19: Agent Retry Behavior**
    - **Validates: Requirements 21.5, 30.6**
    - Test that failed tasks are retried up to 3 times
    - Simulate agent failures
    - Verify exponential backoff timing
    - **Files**: `backend/tests/properties/test_orchestrator_properties.py`
    - **Unit tests**: `backend/tests/unit/test_orchestrator.py` (42 tests, all passing)

- [x] 13. Implement documentary stream assembly
  - [x] 13.1 Create StreamAssembler class
    - Implement interleaved stream assembly logic
    - Create timeline from narration, video, illustrations, facts
    - Add StreamBuffer class for smooth playback (5 second buffer)
    - Implement ContentSynchronizer for narration/illustration/video sync
    - Add gap prevention logic to ensure continuity < 1 second
    - Implement natural break detection for video insertion
    - _Requirements: 5.1, 5.3, 5.4, 5.5, 5.6_
    - **Files**: `backend/services/orchestrator/stream_assembler.py`
    - **Unit tests**: `backend/tests/unit/test_stream_assembler.py` (51 tests, all passing)

  - [x]* 13.2 Write property test for documentary stream continuity
    - **Property 4: Documentary Stream Continuity**
    - **Validates: Requirements 5.3**
    - Test that gaps between elements never exceed 1 second
    - Generate random documentary streams
    - Measure inter-element timing for 100+ streams
    - **Files**: `backend/tests/properties/test_stream_continuity.py` (5 property tests, 150+ iterations each)

- [x] 14. Checkpoint - Verify SightMode functionality
  - Ensure all tests pass, ask the user if questions arise.


### Phase 3: VoiceMode Implementation (Weeks 5-6)

- [x] 15. Implement VoiceMode handler
  - [x] 15.1 Create VoiceModeHandler class
    - Implement voice input processing pipeline
    - Add noise cancellation for ambient noise > 70 dB
    - Integrate language detection (24 languages)
    - Implement topic parsing from transcription
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_
    - **Files**: `backend/services/voice_mode/handler.py`, `models.py`, `__init__.py`, `requirements.txt`
    - VoiceModeHandler: process_voice_input() → VoiceModeResponse (TOPIC_DETECTED / SILENCE_DETECTED / NOISE_WARNING / INPUT_BUFFERED / ERROR)
    - Audio pipeline: decode base64 → analyse metadata (RMS→dB) → noise classification → silence detection → transcribe via Gemini → parse topic
    - Noise cancellation flag at >70 dB (Req 3.5); silence threshold at <20 dB
    - Topic parsing: strips conversational prefixes ("tell me about", "what is", etc.)
    - 24 supported languages with persistent language detection across calls
    - Optional async callback on_topic_detected for Orchestrator integration
    - Graceful degradation: API errors → SILENCE_DETECTED, never crashes

  - [x]* 15.2 Write property test for voice transcription latency
    - **Property 3: Voice Transcription Latency**
    - **Validates: Requirements 3.2**
    - Test that transcription completes within 500ms
    - Generate random audio samples
    - Measure transcription time for 100+ samples
    - **Files**: `backend/tests/properties/test_voice_transcription_latency.py` (3 property tests, 120 iterations each)
    - **Unit tests**: `backend/tests/unit/test_voice_mode.py` (42 tests, all passing)

- [x] 16. Implement conversation management
  - [x] 16.1 Create ConversationManager class
    - Implement conversation history tracking
    - Add context window management (last 10 interactions)
    - Implement intent classification (new_topic, follow_up, branch, question, command)
    - Add continuous conversation without wake words
    - Implement get_context and classify_intent methods
    - Add handlers for each intent type
    - _Requirements: 3.4, 13.1, 13.2_
    - **Files**: `backend/services/voice_mode/conversation_manager.py`
    - ConversationManager: handle_input() → IntentClassification, tracks ConversationState
    - Intent classification via keyword heuristics (command > branch > question > follow_up > new_topic)
    - Branch depth capped at 3 (Req 13.4); branch_stack tracks nested topics
    - Branch exit via "go back"/"exit branch" commands decrements depth
    - Over-depth branch requests downgraded to FOLLOW_UP
    - 5-minute inactivity timeout detection
    - Context window: configurable, default 10 turns

  - [x]* 16.2 Write unit tests for conversation management
    - Test conversation history persistence
    - Test intent classification accuracy
    - Test context window sliding
    - _Requirements: 3.4, 13.1_
    - **Files**: `backend/tests/properties/test_conversation_properties.py` (7 property tests, 100-150 iterations each)
    - **Unit tests**: `backend/tests/unit/test_conversation_manager.py` (36 tests, all passing)

- [x] 17. Implement VoiceMode workflow in Orchestrator
  - [x] 17.1 Add voice_mode_workflow method to Orchestrator
    - Implement voice input transcription and topic parsing
    - Add parallel content generation for voice topics
    - Integrate with NarrationEngine, NanoIllustrator, SearchGrounder
    - Handle follow-up questions and topic changes
    - _Requirements: 3.1, 3.2, 3.3, 5.1_

- [x] 18. Implement multilingual Ghost Guide
  - [x] 18.1 Add multilingual support to NarrationEngine
    - Implement language selection and switching
    - Add translation while preserving factual accuracy
    - Implement culturally appropriate narration styles
    - Support 24 languages
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5, 17.6_

  - [x]* 18.2 Write property test for language translation accuracy
    - **Property 12: Language Translation Accuracy Invariant**
    - **Validates: Requirements 17.2**
    - Test that translated content preserves factual accuracy
    - Generate random factual content
    - Translate to multiple languages and verify with Search Grounder

- [x] 19. Implement mobile UI for VoiceMode
  - [x] 19.1 Create VoiceMode UI screens in Flutter
    - Design voice input screen with waveform visualization
    - Add conversation history display
    - Implement documentary content display (narration, illustrations, facts)
    - Add mode switching UI
    - _Requirements: 1.2, 1.4, 24.5_

  - [x] 19.2 Implement audio playback with background support
    - Add audio player for narration
    - Implement background audio playback
    - Add playback controls (pause, resume, seek)
    - _Requirements: 24.7_

- [x] 20. Checkpoint - Verify VoiceMode functionality
  - Ensure all tests pass, ask the user if questions arise.


### Phase 4: LoreMode and Advanced Features (Weeks 7-9)

- [x] 21. Implement LoreMode fusion handler
  - [x] 21.1 Create LoreModeHandler class
    - Implement multimodal input processing (camera + voice)
    - Create FusionEngine class for context fusion
    - Implement fuse method combining visual, verbal, and GPS contexts
    - Add find_connections method for cross-modal connection detection
    - Implement processing priority (voice > camera when overloaded)
    - Add semantic similarity calculation for connections
    - _Requirements: 4.1, 4.2, 4.5, 4.6_

  - [x] 21.2 Add lore_mode_workflow to Orchestrator
    - Implement parallel processing of camera and voice inputs
    - Add context fusion logic
    - Enable advanced features (alternate history, historical characters)
    - _Requirements: 4.1, 4.2, 4.3_

  - [x]* 21.3 Write unit tests for context fusion
    - Test visual + verbal context fusion
    - Test cross-modal connection detection
    - Test processing priority under load
    - _Requirements: 4.2, 4.5, 4.6_

- [x] 22. Implement Alternate History Engine
  - [x] 22.1 Create AlternateHistoryEngine class
    - Create AlternateHistoryDetector for what-if question detection
    - Implement scenario extraction and parsing
    - Add historical fact grounding via SearchGrounder
    - Implement plausible alternative narrative generation
    - Add causal reasoning explanation
    - Mark content as speculative with clear labeling
    - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6_

  - [x] 22.2 Add alternate_history_workflow to Orchestrator
    - Integrate AlternateHistoryEngine
    - Generate speculative video content via Veo
    - Add speculative content labeling
    - _Requirements: 4.3, 4.4, 15.1_

  - [x]* 22.3 Write unit tests for alternate history detection
    - Test what-if question pattern matching
    - Test scenario extraction accuracy
    - Test historical grounding verification
    - _Requirements: 15.1, 15.2, 15.3_

- [x] 23. Implement Branch Documentary system
  - [x] 23.1 Create BranchDocumentaryManager class
    - Implement branch creation with depth tracking
    - Add branch stack management
    - Implement return to parent functionality
    - Enforce maximum depth limit (3 levels)
    - Store branch structure in Session Memory
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6_

  - [x]* 23.2 Write property test for branch documentary depth limit
    - **Property 16: Branch Documentary Depth Limit**
    - **Validates: Requirements 13.4**
    - Test that depth 3 branches reject further nesting
    - Generate random branch creation sequences
    - Verify depth enforcement for 100+ scenarios

  - [x] 23.3 Add branch detection and workflow to Orchestrator
    - Implement branch request detection
    - Add branch_documentary_workflow method
    - Integrate with BranchDocumentaryManager
    - _Requirements: 13.1, 13.2_

- [x] 24. Implement Depth Dial configuration
  - [x] 24.1 Create DepthDialManager class
    - Define complexity levels (Explorer, Scholar, Expert)
    - Implement content adaptation for each level
    - Add simplification for Explorer level
    - Add context enhancement for Scholar level
    - Add technical depth for Expert level
    - Support runtime depth dial changes
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6_

  - [x]* 24.2 Write unit tests for depth dial content adaptation
    - Test content simplification for Explorer
    - Test context addition for Scholar
    - Test technical depth for Expert
    - Verify complexity ordering: Explorer < Scholar < Expert
    - _Requirements: 14.2, 14.3, 14.4_

- [x] 25. Implement Historical Character encounters
  - [x] 25.1 Create HistoricalCharacterManager class
    - Create HistoricalCharacter data model
    - Implement character database and relevance search
    - Create character persona generation
    - Implement interactive conversation with historical accuracy verification
    - Add period-appropriate language and knowledge constraints
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6_

  - [x]* 25.2 Write unit tests for historical character accuracy
    - Test knowledge cutoff enforcement
    - Test period-appropriate language
    - Test historical accuracy verification
    - _Requirements: 12.4, 12.5_

- [x] 26. Implement mode switching with content preservation
  - [x] 26.1 Add mode transition logic to Orchestrator
    - Implement mode switching during active sessions
    - Preserve session memory across mode changes
    - Update UI to reflect current mode
    - _Requirements: 1.6, 1.7_

  - [x]* 26.2 Write property test for mode transition content preservation
    - **Property 1: Mode Transition Content Preservation**
    - **Validates: Requirements 1.6, 1.7**
    - Test that all content is preserved when switching modes
    - Generate random mode transitions with content
    - Verify content integrity after transitions

- [x] 27. Checkpoint - Verify LoreMode and advanced features
  - Ensure all tests pass, ask the user if questions arise.

- [x] 27.1 Implement Content Parser and Serializer
  - [x] 27.1.1 Define Documentary_Content_Format grammar
    - Create formal grammar specification for content representation
    - Define structure for narration, video, illustration, fact, transition elements
    - Document serialization format
    - _Requirements: 28.1_

  - [x] 27.1.2 Implement Content_Parser class
    - Implement parse method for Documentary_Content_Format strings
    - Add validation for required fields
    - Implement error handling with descriptive messages
    - Add support for all content types
    - _Requirements: 28.2, 28.6, 28.7_

  - [x] 27.1.3 Implement Content_Serializer class
    - Implement serialize method for DocumentaryContent objects
    - Format objects into valid Documentary_Content_Format strings
    - Ensure lossless serialization
    - _Requirements: 28.3, 28.4_

  - [x] 27.1.4 Add round-trip validation
    - Implement validation that parse(serialize(C)) equals C
    - Add unit tests for round-trip property
    - Test with various content types
    - _Requirements: 28.5_


### Phase 5: Video Generation and GPS Walker (Weeks 10-11)

- [x] 28. Implement Veo Generator service
  - [x] 28.1 Create VeoGenerator class with Vertex AI integration
    - Integrate Veo 3.1 API via Vertex AI
    - Implement generateClip method for 8-60 second clips
    - Add scene chain generation for multiple clips
    - Ensure visual continuity across clips
    - Include native audio in video clips
    - Store clips in Media Store
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.7_

  - [x]* 28.2 Write property test for video clip duration constraints
    - **Property 6: Video Clip Duration Constraints**
    - **Validates: Requirements 6.2**
    - Test that all clips are between 8-60 seconds
    - Generate random scene descriptions
    - Verify duration for 100+ generated clips

  - [x]* 28.3 Write property test for video quality constraints
    - **Property 7: Video Quality Constraints**
    - **Validates: Requirements 6.5**
    - Test that all clips are minimum 1080p resolution
    - Verify resolution metadata for generated clips
    - Check video format and codec

  - [x] 28.4 Integrate VeoGenerator into Orchestrator workflows
    - Add video generation to sight_mode_workflow
    - Add video generation to voice_mode_workflow
    - Add video generation to lore_mode_workflow
    - Implement graceful degradation when video fails
    - _Requirements: 5.4, 6.6_

  - [x]* 28.5 Write property test for graceful video degradation
    - **Property 23: Graceful Video Degradation**
    - **Validates: Requirements 29.1**
    - Test that system continues without video when Veo fails
    - Simulate video generation failures
    - Verify narration and illustrations continue

- [x] 29. Implement GPS Walker service
  - [x] 29.1 Create GPSWalkingTourManager class
    - Implement GPS location monitoring
    - Add nearby landmark detection (50 meter radius)
    - Implement landmark prioritization by proximity and user interest
    - Add auto-trigger logic with minimum interval (5 minutes)
    - Integrate Google Maps Platform and Places API
    - Provide directional guidance to POIs
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_
    - **Files**: `backend/services/gps_walker/manager.py`, `models.py`, `__init__.py`, `requirements.txt`
    - **Implementation**: GPSWalkingTourManager with monitor_location(), detect_nearby_landmarks(), should_trigger_documentary(), trigger_documentary(), get_directions(), find_nearby_pois(), prioritize_landmarks()
    - **Algorithms**: Haversine distance calculation, compass bearing calculation, proximity scoring (linear decay), interest scoring (user history), recency penalty (5-min interval)
    - **APIs**: Google Places API v1 Nearby Search, Google Maps Directions API
    - **Trigger logic**: 50m radius, priority = 50% proximity + 30% interest + 20% recency
    - **GPS accuracy**: 10m threshold (Req 9.6), degraded status for >10m
    - **Graceful degradation**: Handles GPS unavailable, API errors, network failures

  - [x]* 29.2 Write property test for GPS proximity triggering
    - **Property 12: GPS Proximity Triggering**
    - **Validates: Requirements 9.2**
    - Test that landmarks within 50m trigger documentaries
    - Generate random GPS coordinates and landmarks
    - Verify triggering behavior for 100+ scenarios
    - **Files**: `backend/tests/properties/test_gps_proximity_triggering.py` (7 property tests, 100-150 iterations each)

  - [x]* 29.3 Write property test for GPS location accuracy
    - **Property 13: GPS Location Accuracy**
    - **Validates: Requirements 9.6**
    - Test that GPS readings are within 10 meters accuracy
    - Measure accuracy across different conditions
    - Verify accuracy for 100+ readings
    - **Files**: `backend/tests/properties/test_gps_location_accuracy.py` (9 property tests, 100+ iterations each)

  - [x] 29.4 Handle GPS signal loss gracefully
    - Implement GPS unavailability detection
    - Switch to manual location input mode
    - Notify user of degraded functionality
    - _Requirements: 9.7, 29.4_
    - **Implementation**: GPSStatus enum (AVAILABLE/DEGRADED/UNAVAILABLE), GPSUpdate model with error messages
    - **Unit tests**: `backend/tests/unit/test_gps_walker.py` (46 tests, all passing)

- [x] 30. Implement mobile UI for GPS Walking Tour
  - [x] 30.1 Create GPS Walking Tour UI in Flutter
    - Add map view with user location and nearby landmarks
    - Display landmark detection notifications
    - Show directional guidance to POIs
    - Add auto-trigger indicators
    - _Requirements: 9.2, 9.4_
    - **Files**: `mobile/lib/screens/gps_walking_tour_screen.dart`, `mobile/lib/screens/GPS_WALKING_TOUR_README.md`
    - **Implementation**: Full-featured GPS Walking Tour screen with:
      - Google Maps integration showing user location and landmarks
      - Real-time GPS position tracking with <10m accuracy
      - Marker system (green for auto-triggered, blue for nearby)
      - Horizontal scrollable landmark list with distance and auto-trigger indicators
      - Landmark detail card with directions (distance, time, route polyline)
      - GPS signal loss detection with warning banner
      - Graceful degradation to manual mode when GPS unavailable
      - Documentary stream widget at bottom
      - WebSocket integration for landmark_detected and directions events
      - Polyline route display on map
      - Mode switcher support (SightMode/LoreMode)
    - **Dependencies**: Added `google_maps_flutter: ^2.10.0` and `flutter_polyline_points: ^2.1.0`
    - **Models**: Added `DirectionsResponse` to models.dart
    - **WebSocket**: Added `WsDirectionsEvent` to websocket_service.dart
    - **Navigation**: Added GPS Walking Tour button to home screen

- [x] 31. Checkpoint - Verify video generation and GPS Walker
  - Ensure all tests pass, ask the user if questions arise.


### Phase 6: Polish and Optimization (Weeks 12-13)

- [x] 32. Implement Barge-In Handler
  - [x] 32.1 Create BargeInHandler class
    - Implement voice interruption detection during playback
    - Add playback pause within 200ms of speech detection
    - Implement interjection processing (questions, topic changes, commands)
    - Add resume from interruption point
    - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5, 19.6_
    - **Files**: `backend/services/barge_in/handler.py`, `models.py`, `__init__.py`, `requirements.txt`
    - **Integration**: WebSocket Gateway message_router.py updated with BargeInHandler
    - **Features**:
      - Pause playback within 200ms (Req 19.2) - verified by property tests
      - Transcribe audio via VoiceModeHandler
      - Classify interjections via ConversationManager (question/topic_change/branch/command/follow_up)
      - Heuristic fallback classification when ConversationManager unavailable
      - Playback state tracking per client
      - Resume from exact interruption point (Req 19.6)
      - Pause/resume callbacks for Orchestrator integration
    - **Unit tests**: `backend/tests/unit/test_barge_in.py` (32 tests, all passing)
    - **Property tests**: `backend/tests/properties/test_barge_in_latency.py` (6 tests, all passing)

  - [x]* 32.2 Write property test for barge-in response latency
    - **Property 17: Barge-In Response Latency**
    - **Validates: Requirements 19.2**
    - Test that playback pauses within 200ms
    - Generate random interruption timings
    - Measure pause latency for 100+ interruptions
    - **Files**: `backend/tests/properties/test_barge_in_latency.py`
    - **Results**: All tests passing, mean latency well below 200ms requirement

- [x] 33. Implement Chronicle PDF export (Chronicle was used in old react native plan, use flutter alternative if this is not available)
  - [x] 33.1 Create ChronicleExporter class
    - Generate illustrated PDF from session content
    - Include narration transcripts, illustrations, video thumbnails with links
    - Add source citations and timestamps
    - Create table of contents with branch structure
    - Store in Media Store with shareable link
    - Complete export within 30 seconds for 1-hour sessions
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7_
    - **Files**: `backend/services/chronicle_exporter/exporter.py`, `models.py`, `__init__.py`, `requirements.txt`, `README.md`
    - **Implementation**: ChronicleExporter with ReportLab PDF generation
    - **Features**:
      - Async export with 30-second timeout enforcement (Req 16.6)
      - Illustrated PDF with title page, TOC, and content pages (Req 16.2, 16.5)
      - Chronological content organization with timestamps (Req 16.4)
      - Includes narration transcripts, illustrations, video thumbnails, source citations (Req 16.3)
      - Hierarchical table of contents from branch structure (Req 16.5)
      - Storage in Media Store with 7-day shareable signed URLs (Req 16.7)
      - Custom PDF styles (title, section, timestamp, narration, source)
      - Graceful error handling and degradation
      - Support for custom options (page size, font size, include/exclude elements)
    - **Performance**: Tested with 1-hour sessions, completes well under 30s

  - [x]* 33.2 Write unit tests for Chronicle export
    - Test PDF generation completeness
    - Test table of contents structure
    - Test export timing for various session lengths
    - _Requirements: 16.2, 16.3, 16.6_
    - **Files**: `backend/tests/unit/test_chronicle_exporter.py` (24 tests, all passing)
    - **Coverage**:
      - Basic export functionality (success, session not found, timeout)
      - Metadata building with branch structure
      - Content preparation (chronological order, all types)
      - PDF generation (basic, with/without TOC)
      - Export timing (short, medium, long sessions)
      - Storage and shareable URL generation
      - Custom styles and content rendering
      - Error handling (missing content, storage failure)
      - Custom request options
      - Full workflow integration

- [ ] 34. Implement Live News integration (Post-Launch Enhancement)
  - [ ] 34.1 Create LiveNewsIntegrator class
    - Monitor real-time news feeds for current topics
    - Detect relevant breaking news
    - Offer news integration to user
    - Insert news segments into documentary stream
    - Verify news content with SearchGrounder
    - Distinguish news from historical content
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6_

  - [ ]* 34.2 Write unit tests for live news integration
    - Test news relevance detection
    - Test news verification
    - Test content distinction (news vs historical)
    - _Requirements: 18.2, 18.4, 18.5_

- [ ] 35. Implement error handling and graceful degradation
  - [ ] 35.1 Create GracefulDegradationManager class
    - Track component status (operational, degraded, failed)
    - Implement component failure handlers
    - Add user notification for degraded functionality
    - Implement degradation strategies for each component
    - _Requirements: 29.1, 29.2, 29.3, 29.4, 29.5, 29.6, 29.7_

  - [ ]* 35.2 Write property test for component failure logging
    - **Property 24: Component Failure Logging**
    - **Validates: Requirements 29.7**
    - Test that all failures are logged
    - Test that users are notified of degradation
    - Simulate random component failures

  - [ ] 35.3 Create RetryManager class
    - Implement exponential backoff retry logic
    - Add retryable error detection
    - Set maximum retry attempts (3)
    - _Requirements: 21.5, 30.6_

  - [ ] 35.4 Create ErrorRecoveryManager class
    - Implement session recovery after connection loss
    - Add memory store failure recovery with local cache
    - Add media store failure recovery with regeneration
    - _Requirements: 29.5, 29.6_

- [ ] 36. Implement rate limiting and quota management
  - [ ] 36.1 Create RateLimitManager class
    - Define rate limits for all services (Gemini, Veo, Illustration, Search, Maps)
    - Implement usage tracking per service
    - Add rate limit checking before API calls
    - Handle rate limit exceeded scenarios
    - Implement request queuing for non-critical operations
    - _Requirements: 30.1, 30.2, 30.3, 30.4, 30.5, 30.6_

  - [ ]* 36.2 Write unit tests for rate limiting
    - Test rate limit enforcement
    - Test usage counter reset timing
    - Test queue behavior for rate-limited requests
    - _Requirements: 30.1, 30.2, 30.5_

- [ ] 37. Implement monitoring and logging
  - [ ] 37.1 Create ErrorMonitor class
    - Implement structured error logging to Cloud Logging
    - Track error rates per component
    - Detect error rate threshold violations (5%)
    - Trigger alerts when thresholds exceeded
    - _Requirements: 26.1, 26.2, 26.4, 26.5_

  - [ ] 37.2 Set up Cloud Monitoring dashboards
    - Create dashboard for latency metrics
    - Add dashboard for error rates and component status
    - Add dashboard for API quota usage
    - Configure alerts for critical thresholds
    - _Requirements: 26.2, 26.3, 26.4_

  - [ ] 37.3 Implement distributed tracing
    - Add trace IDs to all requests
    - Implement trace correlation across services
    - Log trace IDs with all operations
    - _Requirements: 26.5_

- [ ] 38. Optimize performance
  - [ ] 38.1 Create LatencyOptimizer class
    - Implement content caching for frequently accessed locations
    - Add preloading for nearby landmarks in GPS Walker
    - Implement lazy loading for high-resolution media
    - Optimize parallel generation timing
    - _Requirements: 5.7, 22.7_

  - [ ] 38.3 Implement LatencyOptimizer with caching and preloading
    - Create ContentCache class for location and topic caching
    - Implement ContentPreloader for GPS Walker landmark preloading
    - Add optimize_generation method with fast/slow task separation
    - Implement cache hit/miss tracking
    - Add cache invalidation logic
    - _Requirements: 5.7, 22.7_

  - [ ]* 38.2 Write unit tests for caching and preloading
    - Test cache hit/miss behavior
    - Test preloading effectiveness
    - Test lazy loading triggers
    - _Requirements: 5.7_

- [ ] 39. Checkpoint - Verify polish and optimization
  - Ensure all tests pass, ask the user if questions arise.


### Phase 7: Testing and Deployment (Weeks 14-15)

- [ ] 40. Complete unit test suite
  - [ ] 40.1 Write unit tests for WebSocket Gateway
    - Test connection management
    - Test message routing
    - Test authentication
    - Test buffer management
    - _Requirements: 20.1, 20.2, 20.4_

  - [ ] 40.2 Write unit tests for Orchestrator
    - Test task decomposition
    - Test parallel execution
    - Test result assembly
    - Test mode workflows
    - _Requirements: 21.1, 21.2, 21.3_

  - [ ] 40.3 Write unit tests for all generation agents
    - Test NarrationEngine
    - Test VeoGenerator
    - Test NanoIllustrator
    - Test SearchGrounder
    - _Requirements: 6.1, 7.1, 8.1, 11.5_

  - [ ] 40.4 Write unit tests for Session Memory and Media Store
    - Test CRUD operations
    - Test encryption
    - Test quota management
    - _Requirements: 10.1, 22.2_

  - [ ] 40.5 Write integration tests for end-to-end workflows
    - Test SightMode end-to-end
    - Test VoiceMode end-to-end
    - Test LoreMode end-to-end
    - Test GPS Walking Tour end-to-end
    - _Requirements: 1.1, 2.1, 3.1, 4.1, 9.1_

- [ ] 41. Complete property-based test suite
  - [ ] 41.1 Verify all 25 correctness properties have tests
    - Review property test coverage
    - Add missing property tests
    - Ensure all tests run 100+ iterations
    - Tag all tests with property numbers
    - _Requirements: All correctness properties_

  - [ ]* 41.2 Write property test for content serialization round-trip
    - **Property 22: Content Serialization Round-Trip**
    - **Validates: Requirements 28.5**
    - Test that parse(serialize(C)) equals C
    - Generate random DocumentaryContent objects
    - Verify lossless round-trip for 100+ objects

- [ ] 42. Perform load and performance testing
  - [ ] 42.1 Set up load testing infrastructure
    - Create load testing scripts with Locust or k6
    - Simulate 1000+ concurrent WebSocket connections
    - Generate realistic documentary generation load
    - _Requirements: 20.6_

  - [ ] 42.2 Execute load tests and measure performance
    - Test input-to-output latency under load
    - Test narration start latency under load
    - Test WebSocket message latency under load
    - Test media retrieval latency under load
    - Verify all latency targets met
    - _Requirements: 5.7, 7.2, 20.7, 22.7_

  - [ ] 42.3 Execute stress tests
    - Test maximum branch depth enforcement
    - Test rate limiting behavior under heavy load
    - Test quota management
    - Test buffer overflow handling
    - _Requirements: 13.4, 30.1, 30.2_

  - [ ] 42.4 Optimize based on test results
    - Identify and fix performance bottlenecks
    - Tune auto-scaling parameters
    - Optimize database queries
    - Adjust buffer sizes and timeouts
    - _Requirements: 20.6, 26.2_

- [ ] 43. Deploy to production
  - [ ] 43.1 Set up production GCP environment
    - Create production project with proper IAM
    - Configure production Firestore and Cloud Storage
    - Set up production Cloud Run services
    - Configure production monitoring and logging
    - _Requirements: 26.1, 26.2, 26.3_

  - [ ] 43.2 Deploy backend services to Cloud Run
    - Build and push Docker images
    - Deploy WebSocket Gateway
    - Deploy Orchestrator service
    - Configure auto-scaling and health checks
    - _Requirements: 20.1, 21.1_

  - [ ] 43.3 Deploy mobile apps to app stores
    - Build iOS app and submit to App Store
    - Build Android app and submit to Google Play
    - Configure app signing and certificates
    - _Requirements: 24.1_

  - [ ] 43.4 Configure production monitoring and alerts
    - Set up error rate alerts (> 5%)
    - Set up latency alerts
    - Set up quota usage alerts
    - Configure on-call rotation
    - _Requirements: 26.4_

- [ ] 44. Create demo video and documentation
  - [ ] 44.1 Record demo video (< 4 minutes)
    - Demonstrate SightMode with landmark recognition
    - Demonstrate VoiceMode with voice topics
    - Demonstrate LoreMode with alternate history
    - Demonstrate GPS Walking Tour
    - Show advanced features (branches, depth dial, historical characters)
    - _Requirements: 27.5_

  - [ ] 44.2 Create architecture diagram
    - Show all components and data flows
    - Include GCP services
    - Show multi-agent orchestration
    - _Requirements: 27.6_

  - [ ] 44.3 Write deployment documentation
    - Create README with project overview
    - Document GCP setup instructions
    - Document local development setup
    - Document API endpoints and WebSocket protocol
    - _Requirements: 27.7_

  - [ ] 44.4 Verify all Hackathon Compliance requirements
    - Verify Gemini Live API is primary conversational interface (27.1)
    - Verify ADK is used for multi-agent orchestration (27.2)
    - Verify all services deployed on GCP (27.3)
    - Verify multimodal capabilities demonstrated (27.4)
    - Verify demo video is under 4 minutes (27.5)
    - Verify architecture diagram is complete (27.6)
    - Verify public repository with spin-up instructions (27.7)
    - _Requirements: 27.1, 27.2, 27.3, 27.4, 27.5, 27.6, 27.7_

- [ ] 45. Final checkpoint - Production readiness verification
  - Ensure all tests pass, ask the user if questions arise.
  - Verify all 30 requirements are implemented
  - Verify all 25 correctness properties are tested
  - Verify all latency targets are met
  - Verify production deployment is stable


## Notes

- Tasks marked with `*` are optional property-based and unit tests that can be skipped for faster MVP delivery
- Each task references specific requirements for traceability
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples, edge cases, and integration points
- Checkpoints ensure incremental validation at the end of each phase
- The implementation uses Python for backend services, Dart/Flutter for mobile, and Google Cloud Platform for infrastructure
- All property tests should run minimum 100 iterations due to randomization
- Property tests use the tag format: `Feature: lore-multimodal-documentary-app, Property {number}: {property_text}`

## Implementation Strategy

1. **Incremental Development**: Each phase builds on previous phases with clear dependencies
2. **Parallel Work**: Within each phase, many tasks can be executed in parallel (e.g., different generation agents)
3. **Test-Driven**: Property tests and unit tests are integrated throughout implementation, not saved for the end
4. **Graceful Degradation**: Error handling and fallback strategies are built into each component
5. **Performance First**: Latency targets are validated continuously, not just at the end
6. **Cloud-Native**: All services designed for Cloud Run with auto-scaling and monitoring

## Success Criteria

- All 30 requirements implemented and verified
- All 25 correctness properties tested with property-based tests
- All latency targets met: input-to-output < 3s, narration < 2s, WebSocket < 100ms, media retrieval < 500ms
- Load testing passes with 1000+ concurrent users
- Production deployment stable with monitoring and alerts
- Demo video completed (< 4 minutes)
- Architecture diagram and documentation complete
