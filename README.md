# LORE - The World Is Your Documentary

> Transform physical locations and spoken topics into real-time, AI-generated documentaries

LORE is a multimodal AI application that creates immersive documentary experiences by combining camera vision, conversational AI, video generation, and search-grounded facts. Point your camera at a monument, speak a topic of interest, or combine both to unlock rich multimedia storytelling powered by Google's latest AI technologies.

## 🎯 Overview

LORE operates in three intelligent modes:

- **SightMode**: Point your camera at landmarks and locations to instantly generate documentaries about what you're viewing
- **VoiceMode**: Speak any topic to receive comprehensive documentary content without visual input
- **LoreMode**: Combine camera and voice for advanced features like alternate history scenarios and cross-modal queries

## ✨ Key Features

### Core Capabilities
- **Real-Time Documentary Generation**: Seamless streaming of narration, video clips, illustrations, and verified facts
- **GPS Walking Tours**: Automatic location-based content as you explore cities and landmarks
- **Persistent Session Memory**: Build on past explorations with cross-session queries
- **Multilingual Support**: Documentary narration in 24 languages with cultural adaptation

### Advanced Features
- **Affective Narration**: AI-generated voice that adapts tone to emotional context (respectful, enthusiastic, contemplative)
- **Branch Documentaries**: Explore related sub-topics without losing your main thread (up to 3 levels deep)
- **Historical Character Encounters**: Interact with AI personas from historical periods
- **Alternate History Mode**: Explore "what if" scenarios grounded in historical facts
- **Depth Dial**: Adjust content complexity (Explorer/Scholar/Expert) on the fly
- **Chronicle Export**: Save sessions as illustrated PDF documents with citations

### Intelligence & Quality
- **Search-Grounded Facts**: All claims verified against authoritative sources with citations
- **Multi-Agent Orchestration**: Parallel content generation using Google's Agent Development Kit (ADK)
- **Graceful Degradation**: System continues operating when individual components fail
- **Barge-In Support**: Interrupt naturally with questions or topic changes

## 🏗️ Architecture

### Technology Stack

**AI & ML**
- Gemini Live API - Real-time conversational AI with vision and audio
- Gemini 3 Flash Preview - Multi-agent orchestration via ADK
- Veo 3.1 - Cinematic video generation (8-60 second clips)
- Gemini 3.1 Flash Image Preview - Rapid illustration generation
- Google Search Grounding API - Fact verification with source citations

**Backend Services**
- Python 3.11+ (asyncio, FastAPI, ADK)
- Cloud Run - Auto-scaling WebSocket server
- Cloud Pub/Sub - Asynchronous agent messaging
- Firestore - Session memory persistence
- Cloud Storage - Media file storage

**Mobile Frontend**
- Flutter (iOS + Android)
- WebSocket client for real-time streaming
- Camera, microphone, and GPS integration

**Infrastructure**
- Google Cloud Platform
- Cloud Logging & Monitoring
- Google Cloud Identity Platform (authentication)
- Google Maps Platform + Places API (location services)

### System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Mobile Client (Flutter)                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│  │  Camera  │  │   Mic    │  │   GPS    │  │ Local Cache  │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────┘   │
└────────────────────────┬────────────────────────────────────────┘
                         │ WebSocket
┌────────────────────────┼────────────────────────────────────────┐
│              Cloud Run Services (GCP)                            │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │           WebSocket Gateway + Authentication              │  │
│  └──────────────────────┬───────────────────────────────────┘  │
└─────────────────────────┼──────────────────────────────────────┘
                          │
┌─────────────────────────┼──────────────────────────────────────┐
│     ADK Orchestrator (Gemini 3 Flash Preview)                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Task Decomposition → Parallel Dispatch → Stream Assembly│  │
│  └──────────────────────┬───────────────────────────────────┘  │
└─────────────────────────┼──────────────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
┌───────▼────────┐ ┌─────▼──────┐ ┌───────▼────────┐
│   Narration    │ │    Veo     │ │     Nano       │
│    Engine      │ │  Generator │ │  Illustrator   │
│ (Gemini Live)  │ │ (Veo 3.1)  │ │  (Gemini 3.1)  │
└────────────────┘ └────────────┘ └────────────────┘
        │                 │                 │
        └─────────────────┼─────────────────┘
                          │
┌─────────────────────────┼──────────────────────────────────────┐
│                   Storage Layer                                  │
│  ┌──────────────────┐         ┌──────────────────┐             │
│  │    Firestore     │         │  Cloud Storage   │             │
│  │ (Session Memory) │         │  (Media Files)   │             │
│  └──────────────────┘         └──────────────────┘             │
└──────────────────────────────────────────────────────────────────┘
```

### Key Design Principles

1. **Real-Time Streaming**: WebSocket-based bidirectional communication for sub-second latency
2. **Parallel Generation**: ADK orchestrates concurrent execution of all content generation agents
3. **Graceful Degradation**: System continues with available components when failures occur
4. **Stateful Sessions**: Persistent memory enables cross-session queries and continuity
5. **Scalable Infrastructure**: Cloud Run auto-scales from 2 to 100+ instances based on demand

## 🚀 Getting Started

### Prerequisites

- Google Cloud Platform account with billing enabled
- Python 3.11 or higher
- Flutter SDK 3.0 or higher
- Node.js 18+ (for development tools)

### Quick Start

1. **Clone the repository**
   ```bash
   git clone https://github.com/naazimsnh02/lore.git
   cd lore
   ```

2. **Set up Google Cloud Platform**
   ```bash
   # Create GCP project
   gcloud projects create lore-app --name="LORE App"
   gcloud config set project lore-app
   
   # Enable required APIs
   gcloud services enable \
     run.googleapis.com \
     aiplatform.googleapis.com \
     firestore.googleapis.com \
     storage.googleapis.com \
     pubsub.googleapis.com \
     identitytoolkit.googleapis.com
   
   # Set up authentication
   gcloud auth application-default login
   ```

3. **Configure infrastructure**
   ```bash
   cd infrastructure
   ./setup.sh
   ```

4. **Deploy backend services**
   ```bash
   cd backend
   pip install -r requirements.txt
   ./deploy.sh
   ```

5. **Run mobile app**
   ```bash
   cd mobile
   flutter pub get
   flutter run
   ```

### Configuration

Create a `.env` file in the project root:

```env
# GCP Configuration
GCP_PROJECT_ID=your-project-id
GCP_REGION=us-central1

# API Keys
GEMINI_API_KEY=your-gemini-api-key
GOOGLE_MAPS_API_KEY=your-maps-api-key

# Service Endpoints
WEBSOCKET_URL=wss://your-gateway-url.run.app/ws
```

## 📖 Documentation

### Project Structure

```
lore/
├── backend/
│   ├── services/
│   │   ├── orchestrator/       # ADK-based multi-agent coordinator
│   │   ├── websocket_gateway/  # Real-time communication server
│   │   ├── narration_engine/   # Gemini Live API integration
│   │   ├── veo_generator/      # Video generation service
│   │   ├── nano_illustrator/   # Illustration generation service
│   │   └── search_grounder/    # Fact verification service
│   ├── models/                 # Data models and schemas
│   ├── utils/                  # Shared utilities
│   └── tests/                  # Unit and integration tests
├── mobile/
│   ├── lib/
│   │   ├── screens/            # UI screens
│   │   ├── services/           # Device services (camera, mic, GPS)
│   │   ├── models/             # Data models
│   │   └── widgets/            # Reusable UI components
│   └── test/                   # Flutter tests
├── infrastructure/
│   ├── terraform/              # Infrastructure as code
│   └── scripts/                # Deployment scripts
├── docs/
│   ├── api/                    # API documentation
│   ├── architecture/           # Architecture diagrams
│   └── guides/                 # User and developer guides
└── .kiro/specs/                # Detailed specifications
    ├── requirements.md         # 30 functional requirements
    ├── design.md              # Complete system design
    └── tasks.md               # Implementation plan
```

### API Documentation

#### WebSocket Protocol

**Connection**: `wss://your-gateway-url.run.app/ws`

**Authentication**: Bearer token in connection header

**Message Format**: JSON

Example client message:
```json
{
  "type": "mode_select",
  "payload": {
    "mode": "sight",
    "depthDial": "scholar",
    "language": "en"
  }
}
```

Example server message:
```json
{
  "type": "documentary_content",
  "payload": {
    "sequenceId": 1,
    "contentType": "narration",
    "content": {
      "audioUrl": "https://...",
      "transcript": "Welcome to the Colosseum...",
      "duration": 15.5,
      "tone": "enthusiastic"
    }
  }
}
```

## 🧪 Testing

### Run Unit Tests
```bash
# Backend tests
cd backend
pytest tests/ -v

# Mobile tests
cd mobile
flutter test
```

### Run Property-Based Tests
```bash
# Run all property tests (100+ iterations each)
pytest tests/properties/ -v --property-tests
```

### Run Integration Tests
```bash
# End-to-end workflow tests
pytest tests/integration/ -v
```

### Load Testing
```bash
# Simulate 1000+ concurrent users
cd tests/load
locust -f locustfile.py --host=wss://your-gateway-url.run.app
```

## 📊 Performance Metrics

### Latency Targets
- Input to first output: < 3 seconds
- Narration start: < 2 seconds
- WebSocket message: < 100ms
- Media retrieval: < 500ms (95th percentile)
- Video generation: 30-60 seconds (background)
- Illustration generation: < 2 seconds

### Scalability
- Concurrent users: 1000+ simultaneous connections
- Auto-scaling: 2-100 Cloud Run instances
- Session memory: 90-day retention
- Media storage: Unlimited with quota management

## 🔒 Security & Privacy

### Authentication
- Google Cloud Identity Platform with OAuth 2.0
- JWT tokens with 24-hour expiration
- Role-based access control (RBAC)

### Data Protection
- Encryption at rest (Google-managed keys)
- Encryption in transit (TLS 1.3)
- PII anonymization in logs
- User-initiated data deletion (GDPR compliant)

### API Security
- Rate limiting per user and globally
- Input validation and sanitization
- CORS restrictions
- Regular security audits

### Development Workflow

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Code Standards

- Python: Follow PEP 8, use type hints
- Dart/Flutter: Follow official style guide
- Tests: Maintain 80%+ code coverage
- Documentation: Update docs for all API changes

## 📝 License

This project is licensed under the MIT License.

## 🙏 Acknowledgments

Built with cutting-edge AI technologies from Google:
- Gemini Live API for conversational AI
- Veo 3.1 for video generation
- Vertex AI for model hosting
- Google Cloud Platform for infrastructure
- Agent Development Kit (ADK) for multi-agent orchestration

## 🗺️ Roadmap

### Current Version (v1.0)
- ✅ Three operating modes (Sight/Voice/Lore)
- ✅ Real-time documentary streaming
- ✅ GPS walking tours
- ✅ Multilingual support (24 languages)
- ✅ Search-grounded fact verification

### Upcoming Features (v1.1)
- 🔄 Offline mode with local caching
- 🔄 Social sharing and collaboration
- 🔄 Custom documentary templates
- 🔄 AR overlays for enhanced experiences

### Future Vision (v2.0)
- 🎯 VR/AR immersive documentaries
- 🎯 Community-contributed content
- 🎯 Educational institution partnerships
- 🎯 Multi-user collaborative exploration

---

**Made with ❤️ by the LORE Team**

*Transforming the world into your personal documentary, one location at a time.*
