# Chronicle PDF Exporter

Exports documentary sessions as illustrated PDF documents with narration transcripts, illustrations, video thumbnails, source citations, and table of contents.

## Requirements

Implements Requirements 16.1 – 16.7:

- **16.1**: Provide Chronicle export functionality for completed sessions
- **16.2**: Generate illustrated PDF document
- **16.3**: Include narration transcripts, illustrations, video thumbnails with links, and source citations
- **16.4**: Organize content chronologically with timestamps
- **16.5**: Include table of contents with Branch Documentary structure
- **16.6**: Complete export within 30 seconds for sessions up to 1 hour duration
- **16.7**: Store in Media Store and provide shareable link

## Architecture

### Components

1. **ChronicleExporter**: Main exporter class that orchestrates PDF generation
2. **Models**: Pydantic data models for export requests and results
3. **PDF Generation**: Uses ReportLab for professional PDF creation

### Dependencies

- `reportlab>=4.0.0`: PDF generation library
- `Pillow>=10.0.0`: Image processing for thumbnails and illustrations
- `httpx>=0.25.0`: HTTP client for downloading images from URLs

### Data Flow

```
Session Data (Firestore)
    ↓
ChronicleExporter.export_chronicle()
    ↓
1. Load session from SessionMemoryManager
2. Build Chronicle metadata (title, TOC, stats)
3. Prepare content items (chronological order)
4. Generate PDF with ReportLab
5. Store PDF in MediaStoreManager
6. Generate shareable signed URL (7-day expiration)
    ↓
ChronicleExportResult (storage URL, shareable URL, metadata)
```

## Usage

### Basic Export

```python
from backend.services.chronicle_exporter import ChronicleExporter
from backend.services.chronicle_exporter.models import ChronicleExportRequest

# Initialize exporter
exporter = ChronicleExporter(
    session_memory_manager=session_memory,
    media_store_manager=media_store
)

# Create export request
request = ChronicleExportRequest(
    session_id="session_123",
    user_id="user_456",
)

# Export Chronicle
result = await exporter.export_chronicle(request)

if result.status == ChronicleStatus.COMPLETED:
    print(f"Chronicle exported: {result.shareable_url}")
    print(f"File size: {result.file_size_bytes} bytes")
    print(f"Generation time: {result.generation_time_seconds}s")
else:
    print(f"Export failed: {result.error}")
```

### Custom Export Options

```python
request = ChronicleExportRequest(
    session_id="session_123",
    user_id="user_456",
    # Customization options
    include_timestamps=True,
    include_sources=True,
    include_video_thumbnails=True,
    include_toc=True,
    page_size="A4",  # or "Letter"
    font_size=11,
)

result = await exporter.export_chronicle(request)
```

## PDF Structure

### 1. Title Page

- Chronicle title (mode + date)
- Session metadata:
  - Operating mode (SightMode/VoiceMode/LoreMode)
  - Depth dial level (Explorer/Scholar/Expert)
  - Language
  - Date and time
  - Total duration
  - Locations visited
  - Content statistics

### 2. Table of Contents (Optional)

- Hierarchical structure based on Branch Documentary tree
- Shows main topics and sub-topics with depth indentation
- Maximum depth: 3 levels

### 3. Content Pages

Chronologically organized content with:

#### Narration Segments
- 🎙️ Transcript text
- Duration
- Timestamp

#### Video Clips
- 🎬 Video thumbnail (if available)
- Link to video file
- Duration
- Timestamp

#### Illustrations
- 🎨 Illustration image
- Caption
- Timestamp

#### Facts
- 📚 Factual claim
- Source citations with links
- Timestamp

## Performance

### Timing Requirements

- **Short sessions (< 10 min)**: < 5 seconds
- **Medium sessions (10-30 min)**: < 15 seconds
- **Long sessions (30-60 min)**: < 30 seconds (Requirement 16.6)

### Optimization Strategies

1. **Async I/O**: All network operations are async
2. **Timeout Protection**: 30-second hard timeout enforced
3. **Efficient PDF Generation**: ReportLab's platypus for optimized layout
4. **Lazy Image Loading**: Images loaded only when needed
5. **Streaming**: Content processed in chronological order without full buffering

## Storage and Sharing

### Media Store Integration

Chronicles are stored in Cloud Storage with the following structure:

```
gs://lore-media-{env}/
  users/{userId}/
    sessions/{sessionId}/
      chronicles/
        {chronicleId}.pdf
```

### Shareable Links

- Generated using signed URLs
- 7-day expiration by default
- HTTPS-only access
- No authentication required (signed URL provides access)

### Quota Management

Chronicles count toward user storage quota:
- Typical size: 1-5 MB for 1-hour session
- Includes embedded thumbnails and illustrations
- Automatic cleanup after 90 days (configurable)

## Error Handling

### Graceful Degradation

The exporter handles failures gracefully:

1. **Session Not Found**: Returns FAILED status with descriptive error
2. **Timeout**: Returns FAILED status after 30 seconds
3. **Storage Failure**: Returns FAILED status with error details
4. **Missing Content**: Generates PDF with available content only
5. **Image Load Failure**: Continues without images, logs warning

### Error Response

```python
ChronicleExportResult(
    chronicle_id="...",
    session_id="...",
    user_id="...",
    status=ChronicleStatus.FAILED,
    error="Descriptive error message",
    error_details={"exception_type": "ValueError"},
    generation_time_seconds=1.23
)
```

## Testing

### Unit Tests

24 comprehensive unit tests covering:

- ✅ Basic export functionality
- ✅ Session not found handling
- ✅ Timeout enforcement (30s)
- ✅ Metadata building with branch structure
- ✅ Content preparation (chronological order)
- ✅ Content type inclusion (narration, video, illustration, fact)
- ✅ PDF generation (basic, with/without TOC)
- ✅ Export timing (short, medium, long sessions)
- ✅ Storage and shareable URL generation
- ✅ Custom styles setup
- ✅ Content element rendering
- ✅ Error handling (missing content, storage failure)
- ✅ Custom request options
- ✅ Full workflow integration

Run tests:
```bash
cd backend
python -m pytest tests/unit/test_chronicle_exporter.py -v
```

### Test Coverage

All requirements 16.1 – 16.7 are validated by unit tests.

## Integration with Orchestrator

### WebSocket Message Flow

```typescript
// Client requests Chronicle export
{
  type: 'export_chronicle',
  payload: {
    session_id: 'session_123',
    options: {
      include_toc: true,
      include_sources: true
    }
  }
}

// Server responds with export result
{
  type: 'chronicle_exported',
  payload: {
    chronicle_id: 'chronicle_456',
    shareable_url: 'https://storage.googleapis.com/...',
    file_size_bytes: 2457600,
    page_count: 15,
    generation_time_seconds: 8.3
  }
}
```

### Orchestrator Integration

```python
from backend.services.chronicle_exporter import ChronicleExporter

class DocumentaryOrchestrator:
    def __init__(self):
        self.chronicle_exporter = ChronicleExporter(
            session_memory_manager=self.session_memory,
            media_store_manager=self.media_store
        )
    
    async def handle_export_request(self, session_id: str, user_id: str):
        request = ChronicleExportRequest(
            session_id=session_id,
            user_id=user_id
        )
        
        result = await self.chronicle_exporter.export_chronicle(request)
        
        # Send result to client via WebSocket
        await self.send_to_client(user_id, {
            'type': 'chronicle_exported',
            'payload': result.model_dump()
        })
```

## Future Enhancements

### Potential Improvements

1. **Additional Formats**: HTML, EPUB, Markdown
2. **Custom Themes**: User-selectable PDF themes and styles
3. **Interactive PDFs**: Embedded audio/video players
4. **Batch Export**: Export multiple sessions at once
5. **Scheduled Exports**: Automatic weekly/monthly summaries
6. **Collaborative Annotations**: Allow users to add notes to Chronicles
7. **Print Optimization**: Printer-friendly layouts
8. **Accessibility**: Screen reader-friendly PDFs with proper tagging

### Performance Optimizations

1. **Parallel Image Processing**: Download thumbnails concurrently
2. **PDF Compression**: Reduce file size with image optimization
3. **Caching**: Cache frequently accessed session data
4. **Incremental Generation**: Generate PDF pages as content becomes available
5. **Background Processing**: Queue long exports for background processing

## Troubleshooting

### Common Issues

**Issue**: Export times out after 30 seconds
- **Cause**: Session has too much content or slow network
- **Solution**: Reduce content density or increase timeout (not recommended)

**Issue**: Images not appearing in PDF
- **Cause**: Image URLs are inaccessible or invalid
- **Solution**: Verify Cloud Storage permissions and URLs

**Issue**: PDF file size too large
- **Cause**: High-resolution images embedded
- **Solution**: Implement image compression or use thumbnails

**Issue**: Table of contents missing
- **Cause**: Session has no branch structure
- **Solution**: This is expected for linear sessions without branches

## License

Part of the LORE multimodal documentary application.
Copyright © 2025 LORE Team
