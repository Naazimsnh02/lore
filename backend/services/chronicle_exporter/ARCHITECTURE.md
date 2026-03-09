# Chronicle PDF Export - Architecture

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Client (Flutter App)                         │
│                                                                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  User taps "Export Chronicle" button                          │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                        │
│                              ▼                                        │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  WebSocket Message: { type: 'export_chronicle', ... }         │  │
│  └──────────────────────────────────────────────────────────────┘  │
└───────────────────────────────┬───────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    WebSocket Gateway (Cloud Run)                     │
│                                                                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  MessageRouter.route_message()                                │  │
│  │    → Identifies 'export_chronicle' message type               │  │
│  │    → Forwards to Orchestrator                                 │  │
│  └──────────────────────────────────────────────────────────────┘  │
└───────────────────────────────┬───────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Orchestrator (ADK + Gemini)                       │
│                                                                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  handle_export_request()                                      │  │
│  │    → Creates ChronicleExportRequest                           │  │
│  │    → Calls ChronicleExporter.export_chronicle()               │  │
│  └──────────────────────────────────────────────────────────────┘  │
└───────────────────────────────┬───────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         ChronicleExporter                            │
│                                                                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  1. export_chronicle() [with 30s timeout]                     │  │
│  │     ├─ Load session data                                      │  │
│  │     ├─ Build metadata                                         │  │
│  │     ├─ Prepare content items                                  │  │
│  │     ├─ Generate PDF                                           │  │
│  │     └─ Store and share                                        │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  2. _generate_chronicle()                                     │  │
│  │     │                                                          │  │
│  │     ├─► SessionMemoryManager.load_session()                   │  │
│  │     │   └─ Firestore: sessions/{sessionId}                    │  │
│  │     │                                                          │  │
│  │     ├─► _build_metadata()                                     │  │
│  │     │   ├─ Extract session info                               │  │
│  │     │   ├─ Build branch structure for TOC                     │  │
│  │     │   └─ Calculate statistics                               │  │
│  │     │                                                          │  │
│  │     ├─► _prepare_content_items()                              │  │
│  │     │   ├─ Sort content chronologically                       │  │
│  │     │   ├─ Extract type-specific data                         │  │
│  │     │   └─ Build ChronicleContentItem list                    │  │
│  │     │                                                          │  │
│  │     ├─► _generate_pdf()                                       │  │
│  │     │   ├─ Create ReportLab document                          │  │
│  │     │   ├─ Build title page                                   │  │
│  │     │   ├─ Build table of contents                            │  │
│  │     │   ├─ Build content pages                                │  │
│  │     │   └─ Return PDF bytes                                   │  │
│  │     │                                                          │  │
│  │     └─► _store_chronicle()                                    │  │
│  │         ├─ MediaStoreManager.store_media()                    │  │
│  │         │  └─ Cloud Storage: users/{userId}/sessions/...      │  │
│  │         └─ MediaStoreManager.generate_signed_url()            │  │
│  │            └─ 7-day signed URL                                │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  3. Return ChronicleExportResult                              │  │
│  │     ├─ status: COMPLETED                                      │  │
│  │     ├─ storage_url: gs://...                                  │  │
│  │     ├─ shareable_url: https://...                             │  │
│  │     ├─ file_size_bytes: 2457600                               │  │
│  │     └─ generation_time_seconds: 8.3                           │  │
│  └──────────────────────────────────────────────────────────────┘  │
└───────────────────────────────┬───────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Orchestrator (ADK + Gemini)                       │
│                                                                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Send result to client via WebSocket                          │  │
│  │    → { type: 'chronicle_exported', payload: {...} }           │  │
│  └──────────────────────────────────────────────────────────────┘  │
└───────────────────────────────┬───────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         Client (Flutter App)                         │
│                                                                       │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Display success message with download link                   │  │
│  │    → "Chronicle exported! Tap to download"                    │  │
│  │    → Opens shareable URL in browser                           │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. Session Data Retrieval

```
Firestore (sessions/{sessionId})
    │
    ├─ session_id, user_id, mode, status
    ├─ depth_dial, language
    ├─ start_time_ms, end_time_ms
    ├─ total_duration_seconds
    │
    ├─ locations: [LocationVisit]
    │   ├─ place_id, name, coordinates
    │   ├─ visit_time_ms, duration_seconds
    │   └─ triggered_content_ids
    │
    ├─ interactions: [UserInteraction]
    │   ├─ interaction_id, timestamp_ms
    │   ├─ interaction_type, input, response
    │   └─ processing_time_ms
    │
    ├─ content_references: [ContentRef]
    │   ├─ content_id, content_type
    │   ├─ storage_url, timestamp_ms
    │   ├─ duration_seconds
    │   └─ metadata: ContentRefMetadata
    │       ├─ depth_level, language
    │       ├─ emotional_tone
    │       ├─ sources: [url]
    │       └─ extra: {transcript, claim, ...}
    │
    ├─ branch_structure: [BranchNode]
    │   ├─ branch_id, parent_branch_id
    │   ├─ topic, depth (0-3)
    │   ├─ start_time_ms, end_time_ms
    │   └─ content_ids
    │
    └─ content_count: ContentCount
        ├─ narration_segments
        ├─ video_clips
        ├─ illustrations
        └─ facts
```

### 2. Chronicle Metadata Construction

```
SessionDocument
    │
    ▼
ChronicleMetadata
    │
    ├─ title: "LORE Chronicle - Sight Mode - March 9, 2026"
    ├─ session_id, user_id
    ├─ mode, depth_dial, language
    ├─ start_time_ms, end_time_ms
    ├─ total_duration_seconds
    ├─ location_count
    ├─ content_count: {narration: 10, video: 5, ...}
    │
    └─ sections: [ChronicleSection]
        ├─ Section 1 (depth 0): "Eiffel Tower History"
        │   ├─ Sub-section 1.1 (depth 1): "Construction Era"
        │   └─ Sub-section 1.2 (depth 1): "Modern Renovations"
        └─ Section 2 (depth 0): "Architectural Details"
```

### 3. Content Item Preparation

```
ContentRef (from Firestore)
    │
    ├─ content_type: NARRATION
    │   ├─ storage_url: "gs://lore-media/narration1.wav"
    │   ├─ timestamp_ms: 1709971200000
    │   ├─ duration_seconds: 30.0
    │   └─ metadata.extra.transcript: "Welcome to..."
    │
    ▼
ChronicleContentItem
    │
    ├─ sequence_id: 0
    ├─ timestamp_ms: 1709971200000
    ├─ content_type: "narration"
    ├─ text: "Welcome to..."
    ├─ duration_seconds: 30.0
    └─ metadata: {depth_level: "scholar", ...}
```

### 4. PDF Generation Pipeline

```
ReportLab Document
    │
    ├─ Title Page
    │   ├─ Chronicle Title (24pt, centered)
    │   ├─ Spacer (0.5 inch)
    │   └─ Metadata Table
    │       ├─ Mode: Sight
    │       ├─ Depth Level: Scholar
    │       ├─ Language: EN
    │       ├─ Date: March 9, 2026 at 3:00 PM
    │       ├─ Duration: 60 minutes
    │       └─ Locations Visited: 3
    │
    ├─ Page Break
    │
    ├─ Table of Contents
    │   ├─ Heading: "Table of Contents"
    │   ├─ Spacer (0.3 inch)
    │   └─ Hierarchical List
    │       ├─ • Eiffel Tower History
    │       │   ├─   • Construction Era
    │       │   └─   • Modern Renovations
    │       └─ • Architectural Details
    │
    ├─ Page Break
    │
    └─ Content Pages (chronological)
        │
        ├─ Content Item 1: Narration
        │   ├─ Timestamp: "3:00:15 PM"
        │   ├─ Icon + Text: "🎙️ Welcome to the Eiffel Tower..."
        │   ├─ Duration: "Duration: 30s"
        │   └─ Spacer (0.2 inch)
        │
        ├─ Content Item 2: Illustration
        │   ├─ Timestamp: "3:00:45 PM"
        │   ├─ Icon: "🎨 Illustration"
        │   ├─ [Image placeholder]
        │   └─ Spacer (0.2 inch)
        │
        ├─ Content Item 3: Fact
        │   ├─ Timestamp: "3:01:15 PM"
        │   ├─ Icon + Text: "📚 The Eiffel Tower was built in 1889."
        │   ├─ Sources:
        │   │   └─ • Wikipedia (link)
        │   └─ Spacer (0.2 inch)
        │
        └─ Content Item 4: Video
            ├─ Timestamp: "3:02:00 PM"
            ├─ Icon: "🎬 Video Clip"
            ├─ [Thumbnail placeholder]
            ├─ Link: "Watch Video"
            ├─ Duration: "Duration: 45s"
            └─ Spacer (0.2 inch)
```

### 5. Storage and Sharing

```
PDF Bytes (in memory)
    │
    ▼
MediaStoreManager.store_media()
    │
    ├─ Upload to Cloud Storage
    │   └─ gs://lore-media-prod/users/{userId}/sessions/{sessionId}/chronicles/{chronicleId}.pdf
    │
    ├─ Create Firestore record
    │   └─ media_records/{chronicleId}
    │       ├─ media_id, user_id, session_id
    │       ├─ media_type: CHRONICLE
    │       ├─ gcs_object_name, mime_type
    │       ├─ size_bytes, created_at_ms
    │       └─ expires_at_ms (90 days)
    │
    └─ Return storage_url
        └─ "gs://lore-media-prod/users/..."

MediaStoreManager.generate_signed_url()
    │
    ├─ Generate signed URL with 7-day expiration
    │   └─ Uses Cloud Storage signing API
    │
    └─ Return shareable_url
        └─ "https://storage.googleapis.com/lore-media-prod/...?X-Goog-Signature=..."
```

## Component Interactions

```
┌──────────────────────┐
│  ChronicleExporter   │
└──────────┬───────────┘
           │
           ├─────────────────────────────────┐
           │                                 │
           ▼                                 ▼
┌──────────────────────┐         ┌──────────────────────┐
│ SessionMemoryManager │         │  MediaStoreManager   │
└──────────┬───────────┘         └──────────┬───────────┘
           │                                 │
           ▼                                 ▼
┌──────────────────────┐         ┌──────────────────────┐
│  Firestore Database  │         │   Cloud Storage      │
│  sessions/{id}       │         │   media/{path}       │
└──────────────────────┘         └──────────────────────┘
```

## Error Handling Flow

```
export_chronicle()
    │
    ├─ Try: asyncio.wait_for(timeout=30s)
    │   │
    │   ├─ Success → Return COMPLETED result
    │   │
    │   ├─ TimeoutError → Return FAILED result
    │   │   └─ error: "Export exceeded 30 second timeout"
    │   │
    │   └─ Exception → Return FAILED result
    │       └─ error: str(exception)
    │
    └─ Always: Record generation_time_seconds
```

## Performance Optimization

### Async Operations

```
┌─────────────────────────────────────────────────────────┐
│  Parallel Operations (where possible)                    │
│                                                           │
│  ┌─────────────────┐  ┌─────────────────┐              │
│  │ Load Session    │  │ (Future)        │              │
│  │ from Firestore  │  │ Download Images │              │
│  │                 │  │ from URLs       │              │
│  └─────────────────┘  └─────────────────┘              │
│                                                           │
│  Sequential Operations (required order)                  │
│                                                           │
│  1. Load Session                                         │
│     ↓                                                     │
│  2. Build Metadata                                       │
│     ↓                                                     │
│  3. Prepare Content Items                                │
│     ↓                                                     │
│  4. Generate PDF                                         │
│     ↓                                                     │
│  5. Store in Cloud Storage                               │
│     ↓                                                     │
│  6. Generate Signed URL                                  │
└─────────────────────────────────────────────────────────┘
```

### Timeout Protection

```
Time: 0s ────────────────────────────────────────────► 30s
       │                                               │
       │  ┌──────────────────────────────────────┐   │
       │  │  _generate_chronicle()                │   │
       │  │    ├─ Load session (1-2s)             │   │
       │  │    ├─ Build metadata (<1s)            │   │
       │  │    ├─ Prepare content (<1s)           │   │
       │  │    ├─ Generate PDF (5-20s)            │   │
       │  │    └─ Store & share (2-5s)            │   │
       │  └──────────────────────────────────────┘   │
       │                                               │
       └───────────────────────────────────────────────┘
                    asyncio.wait_for(timeout=30.0)
                    
       If exceeds 30s → TimeoutError → FAILED status
```

## Security Considerations

### 1. Access Control

```
User Request
    │
    ├─ Verify user_id matches session owner
    │   └─ SessionMemoryManager checks ownership
    │
    └─ Generate signed URL with expiration
        └─ 7-day expiration prevents indefinite access
```

### 2. Data Privacy

```
PDF Content
    │
    ├─ Only includes user's own session data
    ├─ No cross-user data leakage
    └─ Stored in user-specific Cloud Storage path
        └─ gs://lore-media/users/{userId}/...
```

### 3. Quota Management

```
MediaStoreManager
    │
    ├─ Track storage usage per user
    ├─ Enforce quota limits
    └─ Automatic cleanup after 90 days
```

## Monitoring and Observability

### Metrics to Track

```
┌─────────────────────────────────────────────────────────┐
│  Chronicle Export Metrics                                │
│                                                           │
│  1. Export Success Rate                                  │
│     └─ COMPLETED / (COMPLETED + FAILED)                  │
│                                                           │
│  2. Average Generation Time                              │
│     └─ Mean of generation_time_seconds                   │
│                                                           │
│  3. Timeout Rate                                         │
│     └─ Timeouts / Total Exports                          │
│                                                           │
│  4. Average PDF Size                                     │
│     └─ Mean of file_size_bytes                           │
│                                                           │
│  5. Storage Usage                                        │
│     └─ Total bytes in chronicles/ folder                 │
└─────────────────────────────────────────────────────────┘
```

### Logging

```
logger.info(f"Chronicle {chronicle_id} generation started")
logger.info(f"Chronicle {chronicle_id} generated in {time:.2f}s")
logger.error(f"Chronicle {chronicle_id} generation failed: {error}")
logger.warning(f"Failed to load video thumbnail: {error}")
```

---

**Architecture Version**: 1.0  
**Last Updated**: March 9, 2026  
**Status**: Production Ready
