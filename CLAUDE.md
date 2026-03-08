# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**LORE** ("The World Is Your Documentary") is a multimodal Live Agent application that transforms physical locations and spoken topics into real-time, AI-generated documentaries. It fuses camera vision + voice input to generate on-demand, interleaved documentary experiences (narration + AI illustrations + Veo 3.1 video clips + search-grounded facts), powered by the full Google Gemini + Cloud stack.

The repository contains comprehensive specifications in `.kiro/specs/lore-multimodal-documentary-app/`:
- **requirements.md** — 30 functional requirements with acceptance criteria and 15 correctness properties
- **design.md** — Complete system architecture, component interfaces, data models, and API specifications
- **tasks.md** — 7-phase implementation plan with 45+ tasks

Implementation follows a spec-driven development methodology with property-based testing for all correctness properties.

## Repository Structure

```
lore-documentary-app/
├── backend/
│   ├── services/
│   │   ├── orchestrator/          # ADK-based multi-agent coordinator
│   │   ├── websocket_gateway/     # FastAPI WebSocket server on Cloud Run
│   │   ├── narration_engine/      # Gemini Live API integration
│   │   ├── veo_generator/         # Veo 3.1 video generation service
│   │   ├── nano_illustrator/      # Gemini 3.1 Flash Image Preview
│   │   ├── search_grounder/       # Google Search Grounding API
│   │   ├── location_recognizer/   # Google Places API integration
│   │   ├── gps_walker/            # GPS walking tour manager
│   │   └── session_memory/        # Firestore session persistence
│   ├── models/                    # Pydantic data models
│   ├── utils/                     # Shared utilities
│   └── tests/                     # Unit, integration, property tests
├── mobile/
│   ├── lib/
│   │   ├── screens/               # Flutter UI screens
│   │   ├── services/              # Camera, mic, GPS, WebSocket
│   │   ├── models/                # Dart data models
│   │   └── widgets/               # Reusable UI components
│   └── test/                      # Flutter tests
├── infrastructure/
│   ├── terraform/                 # IaC for GCP resources
│   └── scripts/                   # Deployment automation
├── .kiro/specs/lore-multimodal-documentary-app/
│   ├── requirements.md            # 30 requirements + properties
│   ├── design.md                  # System architecture
│   └── tasks.md                   # Implementation plan
├── docs/
│   ├── api/                       # API documentation
│   ├── architecture/              # Architecture diagrams
│   └── guides/                    # User and developer guides
└── README.md                      # Project overview
```

## Technology Stack

### Backend (Python)
- **FastAPI + uvicorn** — WebSocket gateway on Cloud Run
- **google-genai Python SDK** — Gemini Live API (NOT REST; SDK handles WebSocket lifecycle)
- **Google ADK** — Multi-agent orchestration (`ParallelAgent` for narration/illustration/video, `SequentialAgent` for branch documentary logic)
- **Firestore** — Session memory persistence
- **Cloud Pub/Sub** — Async decoupling of slow generation (Veo: 15-30s) from fast narration (<400ms)
- **Vertex AI** — Veo 3.1 endpoint, model hosting

### Frontend
- **Flutter** (iOS/Android primary)
- WebSocket client for real-time bidirectional stream with backend

### AI Models & Services
| Model/Service | Role | Performance Target |
|---|---|---|
| **Gemini Live API** | Real-time voice narration, transcription, barge-in handling | < 500ms transcription |
| **Gemini 3 Flash Preview** | ADK orchestration, task decomposition, workflow coordination | < 3s input-to-output |
| **Veo 3.1** | Cinematic video clips (8-60s) with native audio | 30-60s generation |
| **Gemini 3.1 Flash Image Preview** | Rapid illustration generation (1024x1024+) | < 2s per image |
| **Google Search Grounding API** | Fact verification with authoritative source citations | < 1s per claim |
| **Google Places API** | Location recognition from camera frames | < 3s recognition |
| **Google Maps Platform** | GPS walking tour, landmark detection, directions | 10m accuracy |

## Three Operating Modes

- **SightMode** — Camera → location recognized via Maps/Places API → narrated documentary streams
- **VoiceMode** — Voice topic → full interleaved documentary with Branch Documentaries (sub-docs on any claim)
- **LoreMode** — Camera + Voice simultaneously → unlocks **Alternate History** (Veo clip grounded in real camera visual as style reference)

## Key Implementation Patterns

### Gemini Live API Integration
```python
# Real-time voice processing with native audio
from google import genai

client = genai.Client()
config = {
    'model': 'gemini-2.0-flash-exp',
    'audio_config': {
        'sample_rate': 16000,
        'encoding': 'LINEAR16',
        'language_code': 'en-US'
    },
    'response_config': {
        'voice_config': {
            'speaking_rate': 1.0,
            'pitch': 0.0,
            'volume_gain_db': 0.0
        }
    }
}
# SDK handles WebSocket lifecycle, heartbeat, reconnection
```

### ADK Multi-Agent Orchestration
```python
from google.genai import adk

class DocumentaryOrchestrator(adk.Agent):
    def __init__(self):
        self.narration_agent = NarrationAgent()
        self.veo_agent = VeoAgent()
        self.illustration_agent = IllustrationAgent()
        self.search_agent = SearchAgent()
    
    async def generate_documentary(self, request):
        # Parallel execution for fast content generation
        results = await asyncio.gather(
            self.narration_agent.generate(request),
            self.veo_agent.generate(request),
            self.illustration_agent.generate(request),
            self.search_agent.verify(request),
            return_exceptions=True
        )
        return self.assemble_stream(results)
```

### Veo 3.1 Video Generation
```python
# Async video generation with scene chaining
from vertexai.preview.vision_models import VideoGenerationModel

model = VideoGenerationModel.from_pretrained("veo-3.1-generate-preview")
video = model.generate_video(
    prompt="Cinematic view of ancient Roman Colosseum",
    duration=30,  # 8-60 seconds
    resolution="1080p",
    reference_image=last_frame,  # For visual continuity
    include_audio=True
)
# Poll for completion or use callback
```

### Search Grounding for Fact Verification
```python
from google.genai.types import Tool, GoogleSearch

config = genai.types.GenerateContentConfig(
    tools=[Tool(google_search=GoogleSearch())]
)
response = client.models.generate_content(
    model='gemini-3-flash-preview',
    contents=f"Verify this claim: {factual_claim}",
    config=config
)
# Extract sources and citations from grounded response
```

### WebSocket Real-Time Streaming
```python
from fastapi import FastAPI, WebSocket
from fastapi.websockets import WebSocketDisconnect

app = FastAPI()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            # Receive from client
            data = await websocket.receive_json()
            
            # Process and stream back
            async for content in generate_documentary_stream(data):
                await websocket.send_json(content)
    except WebSocketDisconnect:
        # Handle graceful disconnection
        await cleanup_session(websocket)
```

## Key Features & Capabilities

### Core Modes
- **SightMode**: Camera → location recognition → documentary generation
- **VoiceMode**: Voice topic → full documentary with branch exploration
- **LoreMode**: Camera + Voice fusion → alternate history scenarios

### Advanced Features
- **Affective Narration**: Tone adaptation (respectful, enthusiastic, contemplative, neutral)
- **Branch Documentaries**: Nested sub-topics up to 3 levels deep
- **Historical Character Encounters**: AI personas with period-appropriate knowledge
- **Alternate History Engine**: "What if" scenarios grounded in historical facts
- **GPS Walking Tours**: Auto-trigger documentaries within 50m of landmarks
- **Depth Dial**: Content complexity adjustment (Explorer/Scholar/Expert)
- **Multilingual Support**: 24 languages with cultural adaptation
- **Chronicle Export**: Illustrated PDF with citations and timestamps
- **Barge-In Handling**: Natural interruptions with < 200ms response
- **Session Memory**: Cross-session queries and persistent context

### Quality & Reliability
- **Search-Grounded Facts**: All claims verified with authoritative sources
- **Graceful Degradation**: System continues when components fail
- **Rate Limiting**: Per-user and global quota management
- **Error Recovery**: Automatic retries with exponential backoff (up to 3 attempts)
- **Encryption**: Data at rest (Firestore) and in transit (TLS 1.3)

## Development Phases

Follow the 7-phase implementation plan in `.kiro/specs/lore-multimodal-documentary-app/tasks.md`:

### Phase 1: Core Infrastructure (Weeks 1-2)
- GCP project setup (Cloud Run, Vertex AI, Firestore, Cloud Storage, Pub/Sub)
- WebSocket Gateway with authentication
- Session Memory Manager (Firestore)
- Media Store Manager (Cloud Storage)
- Flutter mobile app skeleton

### Phase 2: SightMode Implementation (Weeks 3-4)
- Location Recognizer (Google Places API)
- SightMode handler with frame buffering
- Narration Engine (Gemini Live API)
- Affective Narrator module
- Nano Illustrator (Gemini 3.1 Flash Image Preview)
- Search Grounder (Google Search Grounding API)
- Basic Orchestrator with ADK
- Documentary stream assembly

### Phase 3: VoiceMode Implementation (Weeks 5-6)
- VoiceMode handler with noise cancellation
- Conversation Manager with intent classification
- VoiceMode workflow in Orchestrator
- Multilingual Ghost Guide (24 languages)
- Mobile UI for VoiceMode

### Phase 4: LoreMode and Advanced Features (Weeks 7-9)
- LoreMode fusion handler with FusionEngine
- Alternate History Engine
- Branch Documentary system (3-level depth)
- Depth Dial configuration
- Historical Character encounters
- Mode switching with content preservation

### Phase 5: Video Generation and GPS Walker (Weeks 10-11)
- Veo Generator (Veo 3.1 via Vertex AI)
- Scene chain generation with visual continuity
- GPS Walking Tour Manager
- Landmark detection and auto-triggering
- Mobile UI for GPS Walking Tour

### Phase 6: Polish and Optimization (Weeks 12-13)
- Barge-In Handler (< 200ms response)
- Chronicle PDF export
- Live News integration
- Error handling and graceful degradation
- Rate limiting and quota management
- Monitoring and logging (Cloud Logging/Monitoring)
- Performance optimization (caching, preloading)

### Phase 7: Testing and Deployment (Weeks 14-15)
- Complete unit test suite (80% coverage)
- Property-based test suite (25 properties, 100+ iterations each)
- Load testing (1000+ concurrent users)
- Performance testing (all latency targets)
- Production deployment
- Demo video and documentation

## Testing Requirements

### Property-Based Testing
All 25 correctness properties must be tested with 100+ iterations:
- Use `fast-check` (TypeScript) or `Hypothesis` (Python)
- Tag format: `Feature: lore-multimodal-documentary-app, Property X: [property text]`
- Examples: Mode transition preservation, stream continuity, round-trip serialization

### Performance Targets
- Input to first output: < 3 seconds
- Narration start: < 2 seconds
- WebSocket message: < 100ms
- Media retrieval: < 500ms (95th percentile)
- Video generation: 30-60 seconds (background)
- Illustration generation: < 2 seconds
- GPS location accuracy: within 10 meters
- Barge-in response: < 200ms

### Load Testing
- Simulate 1000+ concurrent WebSocket connections
- Test auto-scaling (2-100 Cloud Run instances)
- Verify buffer management during network interruptions
- Test rate limiting under heavy load


## Specification Files Reference

### requirements.md
Contains 30 functional requirements organized by feature area:
- Core mode selection (Requirement 1)
- Input processing (Requirements 2-4)
- Documentary generation (Requirements 5-7)
- Fact verification (Requirement 8)
- GPS walking tours (Requirement 9)
- Session memory (Requirement 10)
- Advanced features (Requirements 11-19)
- Infrastructure (Requirements 20-26)
- Content parsing (Requirement 28)
- Error handling (Requirement 29)
- Rate limiting (Requirement 30)

Each requirement includes:
- User story
- 5-7 acceptance criteria in EARS format
- Referenced correctness properties

### design.md
Complete system design including:
- High-level architecture with component diagram
- 11 component interfaces with TypeScript signatures
- Data models (Session, DocumentaryContent, User, etc.)
- API specifications (WebSocket protocol, REST endpoints)
- Multi-agent orchestration workflows
- Real-time streaming and content assembly
- Mode-specific implementation logic
- Error handling and graceful degradation
- Testing strategy (unit + property-based)
- 25 correctness properties with formal definitions
- Deployment architecture and cost estimates

### tasks.md
7-phase implementation plan with 45+ tasks:
- Each task references specific requirements
- Sub-tasks for complex components
- Optional property tests marked with `*`
- Checkpoints at end of each phase
- Clear dependencies between tasks
- Technology stack specifications
- Success criteria for each phase

## Code Quality Standards

### Python Backend
- Follow PEP 8 style guide
- Use type hints for all functions and classes
- Async/await for all I/O operations
- Pydantic models for data validation
- Comprehensive docstrings (Google style)
- Error handling with custom exceptions
- Logging with structured context

### Flutter Frontend
- Follow official Dart style guide
- Null safety enabled
- State management with Provider or Riverpod
- Separation of concerns (screens/services/models/widgets)
- Error handling with try-catch and error widgets
- Responsive design for multiple screen sizes

### Testing
- Unit tests: 80%+ code coverage
- Property tests: 100+ iterations per property
- Integration tests: All major workflows
- Mock external services for deterministic tests
- Performance tests: Verify all latency targets

## Common Pitfalls to Avoid

1. **Gemini Live API**: Always use `google-genai` SDK, not REST endpoints
2. **Veo Generation**: Always async with polling or callbacks (15-30s generation time)
3. **WebSocket Buffering**: Implement 30-second buffer for network interruptions
4. **Rate Limiting**: Check limits before API calls, implement exponential backoff
5. **Session State**: Always persist to Firestore, don't rely on in-memory state
6. **Error Handling**: Implement graceful degradation, never fail completely
7. **Property Tests**: Run minimum 100 iterations due to randomization
8. **ADK Workflows**: Use ParallelAgent for concurrent tasks, SequentialAgent for ordered logic
9. **Content Synchronization**: Ensure gaps between stream elements < 1 second
10. **GPS Accuracy**: Handle signal loss gracefully, switch to manual mode

## Useful Commands

```bash
# Backend development
cd backend
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
pytest tests/ -v

# Run property tests
pytest tests/properties/ -v --property-tests

# Flutter development
cd mobile
flutter pub get
flutter run
flutter test

# Deploy to GCP
cd infrastructure
terraform init
terraform plan
terraform apply

# Run load tests
cd tests/load
locust -f locustfile.py --host=wss://your-gateway-url.run.app
```

## Getting Help

- **Specifications**: Check `.kiro/specs/lore-multimodal-documentary-app/` for detailed requirements and design
- **Architecture**: See `design.md` for component interfaces and data flows
- **Implementation**: Follow `tasks.md` for step-by-step guidance
- **API Docs**: See `docs/api/` for WebSocket protocol and REST endpoints
- **Examples**: Check `backend/tests/` for code examples and patterns
