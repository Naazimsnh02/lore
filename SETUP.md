# LORE — Setup, Testing & Deployment Guide

This is the canonical reference for configuring, running, testing, and
publishing the LORE backend and Flutter mobile app.

---

## Project Identity

| Item | Value |
|------|-------|
| GCP Project ID | `<YOUR_GCP_PROJECT_ID>` |
| Vertex AI Region | `<YOUR_GCP_REGION>` (e.g., `us-central1`) |
---

## AI Models

| Role | Model ID |
|------|----------|
| Live API — Vertex AI | `gemini-live-2.5-flash-native-audio` |
| Live API — AI Studio | `gemini-2.5-flash-native-audio-preview-12-2025` |
| Illustrations | `gemini-3.1-flash-image-preview` |
| Video generation | `veo-3.1-generate-preview` |

---

## Active Modes & Architecture

LORE has 4 modes, all designed to use the Gemini Live API directly:

| Mode | Screen | Connects to |
|------|--------|-------------|
| SightMode | `sight_mode_screen.dart` | Gemini Live proxy (8090) |
| VoiceMode | `new_voice_mode_screen.dart` | Gemini Live proxy (8090) + image server (8091) + video server (8092) |
| LoreMode | `lore_mode_screen.dart` | Gemini Live proxy (8090) + image server (8091) |
| GPS Walking Tour | `new_gps_mode_screen.dart` | Gemini Live proxy (8090) + Google Directions API |

### Backend services (active)

| Service | Port | Role |
|---------|------|------|
| `gemini_live_proxy` | 8090 | Proxies all 4 modes to Gemini Live API |
| `nano_illustrator` | 8091 | HTTP image generation endpoint |
| `veo_generator` | 8092 | HTTP video generation endpoint |

The image/video server URLs are derived from the `GEMINI_PROXY_URL` host (same host, ports 8091/8092).

---

## API Keys — Where to Find Them

> **Never commit real keys.** `.env` and `local.properties` are git-ignored.
> Use Secret Manager in production.

| Key | Where to get it |
|-----|----------------|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey |
| `GOOGLE_PLACES_API_KEY` | GCP Console → APIs & Services → Credentials → Create API Key |
| `GOOGLE_MAPS_API_KEY` | Same key as Places — enable Maps SDK + Directions API on it |

---

## One-Time Setup

### 1. Clone and install tools

```bash
gcloud --version     # Google Cloud SDK
flutter --version    # Flutter 3.x
python --version     # Python 3.11+
```

### 2. Authenticate with Google Cloud

```bash
gcloud auth login
gcloud config set project <YOUR_GCP_PROJECT_ID>
gcloud auth application-default login
```

### 3. Configure backend environment

```bash
cp .env.example .env
# Edit .env — see "Environment Variables Reference" below
```

### 4. Configure Flutter dart-defines

Create `mobile/dart-defines.json` (git-ignored):

```json
{
  "GEMINI_PROXY_URL": "ws://<your-lan-ip>:8090",
  "GOOGLE_MAPS_API_KEY": "<your-maps-api-key>"
}
```

> Use `ws://10.0.2.2:8090` for Android emulator. Only add `GCP_PROJECT_ID` for production Vertex AI.

### 5. Configure Android Maps API key

Add to `mobile/android/local.properties` (git-ignored):

```properties
GOOGLE_MAPS_API_KEY=<your-maps-api-key>
```

### 6. Enable required GCP APIs

```
GCP Console → APIs & Services → Library → enable:
  - Places API (New)
  - Maps SDK for Android
  - Maps SDK for iOS
  - Directions API
  - Vertex AI API (for production)
```

### 7. Install Python dependencies

```bash
# From project root
pip install aiohttp google-genai python-dotenv certifi websockets
```

---

## Environment Variables Reference

### Backend `.env`

| Variable | Value | Notes |
|----------|-------|-------|
| `GCP_PROJECT_ID` | `<YOUR_GCP_PROJECT_ID>` | |
| `VERTEX_AI_LOCATION` | `<YOUR_GCP_REGION>` | |
| `GEMINI_API_KEY` | set in `.env` | From AI Studio |
| `GOOGLE_PLACES_API_KEY` | set in `.env` | From GCP Console |
| `GOOGLE_MAPS_API_KEY` | set in `.env` | Same key as Places |

### Per-environment settings

| Variable | Local dev | Production |
|----------|-----------|------------|
| `LOG_LEVEL` | `DEBUG` | `INFO` |
| `GOOGLE_GENAI_USE_VERTEXAI` | `false` | `true` |

---

## Local Development

### Start all backend services (3 terminals from project root)

```powershell
# Terminal 1 — Gemini Live proxy (port 8090)
python backend/services/gemini_live_proxy/server.py

# Terminal 2 — Image generation (port 8091)
python backend/services/nano_illustrator/image_server.py

# Terminal 3 — Video generation (port 8092)
python backend/services/veo_generator/video_server.py
```

### Flutter

```bash
cd mobile
flutter pub get
flutter run --dart-define-from-file=dart-defines.json
```

---

## Deployment to Cloud Run

### Step 1 — Gemini Live proxy

```bash
cd backend/services/gemini_live_proxy
gcloud builds submit --tag gcr.io/<YOUR_GCP_PROJECT_ID>/lore-gemini-proxy:latest --project <YOUR_GCP_PROJECT_ID>
gcloud run deploy lore-gemini-proxy \
  --image gcr.io/<YOUR_GCP_PROJECT_ID>/lore-gemini-proxy:latest \
  --region <YOUR_GCP_REGION> --platform managed \
  --set-env-vars GCP_PROJECT_ID=<YOUR_GCP_PROJECT_ID> \
  --allow-unauthenticated --project <YOUR_GCP_PROJECT_ID>
```

*(Repeat similar steps for `lore-nano-illustrator` and `lore-veo-generator`)*

### Step 2 — Update dart-defines.json for production

```json
{
  "GEMINI_PROXY_URL": "wss://lore-gemini-proxy-<HASH>-<REGION>.a.run.app",
  "GCP_PROJECT_ID": "<YOUR_GCP_PROJECT_ID>",
  "GOOGLE_MAPS_API_KEY": "<YOUR_MAPS_API_KEY>"
}
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Permission denied` on GCP calls locally | Run `gcloud auth application-default login` |
| Gemini `policy violation` (1008) | `GCP_PROJECT_ID` is set in `dart-defines.json` but proxy uses AI Studio — remove it for local dev |
| Veo video has no audio | Switch to Vertex AI: `GOOGLE_GENAI_USE_VERTEXAI=true` |
