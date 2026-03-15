# LORE — The World Is Your Documentary

> Transform physical locations and spoken topics into real-time, AI-generated documentaries

LORE is a multimodal AI app that creates immersive documentary experiences by combining live camera vision, conversational AI, video generation, and search-grounded facts — powered by Google's Gemini Live API.

---

## Modes

| Mode | What it does |
|------|-------------|
| SightMode | Point your camera at a landmark — Gemini narrates what it sees in real time |
| VoiceMode | Speak any topic — get narration, AI-generated images, and Veo video clips |
| LoreMode | Camera + voice + GPS fusion — full documentary with location awareness |
| GPS Walking Tour | Walk around a city — Gemini auto-discovers landmarks and narrates as you move |

---

## Architecture

### Backend (3 active services)

```
gemini_live_proxy  :8090  — WebSocket proxy to Gemini Live API (all 4 modes)
nano_illustrator   :8091  — HTTP image generation (VoiceMode + LoreMode)
veo_generator      :8092  — HTTP video generation (VoiceMode)
```

### Mobile (Flutter)

```
screens/
  home_screen.dart           — mode selection
  sight_mode_screen.dart     — live camera + audio → Gemini Live
  new_voice_mode_screen.dart — voice + image/video generation
  lore_mode_screen.dart      — camera + voice + GPS → Gemini Live
  new_gps_mode_screen.dart   — GPS walking tour + Directions API

services/
  camera_service.dart        — camera frame capture
```

### AI Models

| Role | Model |
|------|-------|
| Live narration (all modes) | `gemini-2.5-flash-native-audio-preview-12-2025` |
| Image generation | `gemini-3.1-flash-image-preview` |
| Video generation | `veo-3.1-generate-preview` |

---

## Quick Start

### Prerequisites

- Python 3.11+
- Flutter 3.x
- Google Cloud SDK (`gcloud`)
- A `GEMINI_API_KEY` from [AI Studio](https://aistudio.google.com/apikey)
- A Google Maps API key (for GPS mode)

### 1. Configure environment

```bash
cp .env.example .env
# Fill in GEMINI_API_KEY, GOOGLE_MAPS_API_KEY
```

### 2. Start backend services (3 terminals from project root)

```powershell
# Terminal 1 — Gemini Live proxy (required for all modes)
python backend/services/gemini_live_proxy/server.py

# Terminal 2 — Image server (VoiceMode + LoreMode)
python backend/services/nano_illustrator/image_server.py

# Terminal 3 — Video server (VoiceMode)
python backend/services/veo_generator/video_server.py
```

### 3. Configure Flutter

Create `mobile/dart-defines.json`:

```json
{
  "GEMINI_PROXY_URL": "ws://10.0.2.2:8090",
  "GOOGLE_MAPS_API_KEY": "<your-maps-api-key>"
}
```

Add to `mobile/android/local.properties`:

```properties
GOOGLE_MAPS_API_KEY=<your-maps-api-key>
```

### 4. Run the app

```bash
cd mobile
flutter pub get
flutter run --dart-define-from-file=dart-defines.json
```

> Use your machine's LAN IP instead of `10.0.2.2` when running on a physical device.

---

## Setup & Deployment

See [`SETUP.md`](SETUP.md) for the full guide including:
- Cloud Run deployment for all 4 services
- Secret Manager setup
- Production dart-defines configuration
- Pre-publish checklist

### Services deployed to Cloud Run

```
lore-gemini-proxy        — wss://lore-gemini-proxy-HASH-uc.a.run.app
lore-nano-illustrator    — https://lore-nano-illustrator-HASH-uc.a.run.app
lore-veo-generator       — https://lore-veo-generator-HASH-uc.a.run.app
```

---

## Notes

- **Veo video with audio** requires Vertex AI (`GOOGLE_GENAI_USE_VERTEXAI=true`). AI Studio produces video without audio.
- **GPS mode** requires a physical device — emulator GPS is unreliable.
- **Local dev** uses AI Studio by default. Do not set `GCP_PROJECT_ID` in `dart-defines.json` for local dev.
- The Maps API key appears in `AndroidManifest.xml` by design (required by Maps SDK) — restrict it by Android app signature in GCP Console.

---

## Switching AI Modes (AI Studio vs Vertex AI)

LORE supports both **Google AI Studio** (for fast local prototyping) and **Vertex AI** (for production-grade features like Veo video with audio). Switching between them only requires touching two files:

### 1. Flutter (`mobile/dart-defines.json`)
```json
{
  "GOOGLE_GENAI_USE_VERTEXAI": "true",   // Use "false" for AI Studio
  "GCP_PROJECT_ID": "your-project-id"    // Omit or leave empty for AI Studio
}
```
*Note: After changing `dart-defines.json`, you MUST perform a full rebuild: `flutter run --dart-define-from-file=dart-defines.json`.*

### 2. Backend (`.env`)
```bash
GOOGLE_GENAI_USE_VERTEXAI=true   # or false

# Use the appropriate model ID for the selected mode:
# Vertex AI:
GEMINI_LIVE_MODEL=gemini-live-2.5-flash-native-audio
# AI Studio:
# GEMINI_LIVE_MODEL=gemini-2.5-flash-native-audio-preview-12-2025
```

---

## Tech Stack

- **Gemini Live API** — real-time bidirectional audio/video/text streaming
- **Veo 3.1** — cinematic video generation
- **Flutter** — iOS + Android
- **FastAPI + uvicorn** — WebSocket proxy
- **Google Cloud Run** — auto-scaling serverless backend
- **Google Maps Platform** — GPS walking tour map + directions
