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
| LoreMode | `lore_mode_screen.dart` | Gemini Live proxy (8090) + image server (8091) |
| VoiceMode | `new_voice_mode_screen.dart` | Gemini Live proxy (8090) + image server (8091) + video server (8092) |
| SightMode | `sight_mode_screen.dart` | Gemini Live proxy (8090) |
| GPS Tracking mode | `new_gps_mode_screen.dart` | Gemini Live proxy (8090) + Google Directions API |

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

> **Windows Users:** To run the `.sh` deployment scripts later, please use **Git Bash** (installed with Git for Windows) or **WSL**.

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

We provide automated scripts in `infrastructure/scripts/` to handle project setup and deployment for both Linux/macOS (`.sh`) and Windows PowerShell (`.ps1`).

### 1. One-time project bootstrap

Run the bootstrap script to enable required APIs, create the service account, and grant necessary roles. This replaces the manual API enablement steps.

**Linux / macOS / Git Bash:**
```bash
# Usage: ./infrastructure/scripts/bootstrap.sh <PROJECT_ID> [REGION]
./infrastructure/scripts/bootstrap.sh my-lore-project us-central1
```

**Windows PowerShell:**
```powershell
# Usage: .\infrastructure\scripts\bootstrap.ps1 -ProjectId <PROJECT_ID> [-Region <REGION>]
.\infrastructure\scripts\bootstrap.ps1 -ProjectId my-lore-project -Region us-central1
```

After bootstrapping, you must store your Places API key in Secret Manager (as instructed by the script output):

```bash
# Linux/macOS
echo -n 'YOUR_PLACES_API_KEY' | \
  gcloud secrets create lore-places-api-key --data-file=- --project=my-lore-project

# Windows PowerShell
echo 'YOUR_PLACES_API_KEY' | gcloud secrets create lore-places-api-key --data-file=- --project=my-lore-project
```

Grant the service account access to the secret:

```bash
gcloud secrets add-iam-policy-binding lore-places-api-key \
  --member='serviceAccount:lore-backend@my-lore-project.iam.gserviceaccount.com' \
  --role='roles/secretmanager.secretAccessor' \
  --project=my-lore-project
```

### 2. Deploy services

Deploy all three services (`lore-gemini-proxy`, `lore-nano-illustrator`, `lore-veo-generator`) using the deployment script.

**Linux / macOS / Git Bash:**
```bash
# Usage: ./infrastructure/scripts/deploy.sh --project <PROJECT_ID> [--vertex]
./infrastructure/scripts/deploy.sh --project my-lore-project --vertex
```

**Windows PowerShell:**
```powershell
# Usage: .\infrastructure\scripts\deploy.ps1 -ProjectId <PROJECT_ID> [-Vertex]
.\infrastructure\scripts\deploy.ps1 -ProjectId my-lore-project -Vertex
```

> Use `--vertex` (Bash) or `-Vertex` (PowerShell) to use Vertex AI for the Live API, which is recommended for production.

> Use `--vertex` to use Vertex AI for the Live API, which is recommended for production.

### 3. Update mobile/dart-defines.json for production

The deployment script will output a template for your production `dart-defines.json`. It should look like this:

```json
{
  "GEMINI_PROXY_URL": "wss://lore-gemini-proxy-<HASH>-<REGION>.a.run.app",
  "NANO_ILLUSTRATOR_URL": "https://lore-nano-illustrator-<HASH>-<REGION>.a.run.app/generate",
  "VEO_GENERATOR_URL": "https://lore-veo-generator-<HASH>-<REGION>.a.run.app/generate",
  "GCP_PROJECT_ID": "my-lore-project",
  "GOOGLE_MAPS_API_KEY": "<your-maps-api-key>",
  "GOOGLE_GENAI_USE_VERTEXAI": "true"
}
```

Then rebuild the app:
```bash
cd mobile
flutter build apk --dart-define-from-file=dart-defines.json
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Permission denied` on GCP calls locally | Run `gcloud auth application-default login` |
| Gemini `policy violation` (1008) | `GCP_PROJECT_ID` is set in `dart-defines.json` but proxy uses AI Studio — remove it for local dev |
| Veo video has no audio | Switch to Vertex AI: `GOOGLE_GENAI_USE_VERTEXAI=true` |
