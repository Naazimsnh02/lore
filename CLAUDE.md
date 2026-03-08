# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**LORE** ("The World Is Your Documentary") is a hackathon entry for the **Gemini Live Agent Challenge 2025**. It is a multimodal Live Agent that fuses real-time camera vision + voice input to generate on-demand, interleaved documentary experiences (narration + AI illustrations + Veo 3.1 video clips), powered by the full Google Gemini + Cloud stack.

The repository is currently in the **planning/documentation phase**. The `docs/` folder contains the full development bible (`lore_development.md`) and a React concept/pitch page (`lore-concept.jsx`). No application code has been written yet.

## Planned Repository Structure

Once built, the project will follow this layout (see `docs/lore_development.md` §7.1):

```
lore/
├── apps/
│   ├── mobile/          # Flutter app (primary client)
├── services/
│   ├── gateway/         # Cloud Run: FastAPI + WebSocket handler (main.py, session.py, Dockerfile)
│   ├── orchestrator/    # ADK multi-agent orchestration (narration, illustration, video agents)
│   └── chronicle/       # Chronicle PDF generator
├── infrastructure/
│   ├── terraform/       # IaC (Cloud Run, Firestore, Pub/Sub, Storage)
│   └── deploy.sh        # One-command deployment
└── docs/                # Architecture diagram, dev bible
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

### AI Models
| Model ID | Role |
|---|---|
| `gemini-2.5-flash-native-audio-preview-12-2025` | Core: Live API, vision + audio + session memory + barge-in |
| `gemini-3-flash-preview` | Documentary arc planning, Search grounding, fact verification |
| `veo-3.1-generate-preview` | 1080p video clips with native audio |
| `gemini-3.1-flash-image-preview` | Sub-2s illustrations with character consistency |

## Three Operating Modes

- **SightMode** — Camera → location recognized via Maps/Places API → narrated documentary streams
- **VoiceMode** — Voice topic → full interleaved documentary with Branch Documentaries (sub-docs on any claim)
- **LoreMode** — Camera + Voice simultaneously → unlocks **Alternate History** (Veo clip grounded in real camera visual as style reference)

## Key API Integration Patterns

**Gemini Live API** (see `docs/lore_development.md` §8.1):
- Always use the `google-genai` SDK, not REST
- Audio input: PCM 16-bit 16kHz; Video input: JPEG frames at 10fps, base64-encoded
- Implement heartbeat/reconnect for long sessions

**Veo 3.1** (§8.2):
- Async only — always poll for completion or use callback
- Pass `last_frame` as `reference_image` for scene chaining
- Pass camera frame as `reference_image` for Alternate History grounding

**Search Grounding** (§8.4):
```python
from google.genai.types import Tool, GoogleSearch
config = genai.types.GenerateContentConfig(tools=[Tool(google_search=GoogleSearch())])
```

**ADK patterns** (§8.5):
- `ParallelAgent` for concurrent narration + illustration + video generation
- `SequentialAgent` for branch documentary logic (main → branch → return)
- Pass Firestore document ID as shared state across all agents

## Hackathon Requirements

All submissions must: use a Gemini model, use Google GenAI SDK or ADK, use at least one Google Cloud service, deploy backend on Google Cloud.

**Submission deliverables:** text description, public repo with README spin-up instructions, proof of GCP deployment, architecture diagram (add to `/docs`), 4-minute demo video (no mockups).

**Bonus points:** Terraform IaC in `infrastructure/terraform/`, blog post with `#GeminiLiveAgentChallenge`, Google Developer Group signup.

## Development Phases

Build in this order (see §7.2–7.7):
1. **Phase 1** — WebSocket gateway + Gemini Live API session + Flutter camera/mic streaming
2. **Phase 2** — SightMode MVP: location recognition → narration (minimum viable demo)
3. **Phase 3** — Illustration (Gemini 3.1 Flash Image Preview) + Video (Veo 3.1) generation pipelines
4. **Phase 4** — VoiceMode + Branch Documentary ADK logic
5. **Phase 5** — LoreMode (fusion) + Alternate History + Chronicle PDF + GPS Walking Tour
6. **Phase 6** — Terraform IaC + deploy.sh + README + demo video recording
