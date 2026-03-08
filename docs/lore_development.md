# ◆ GEMINI LIVE AGENT CHALLENGE 2025 ◆

# LORE
## The World Is Your Documentary

---

# SECTION 1 — HACKATHON OVERVIEW

## 1. Hackathon: Gemini Live Agent Challenge
Hosted by Google, the Gemini Live Agent Challenge tasks builders to move beyond static chatbots and create immersive, real-time AI experiences using multimodal inputs and outputs. The challenge celebrates the power of Gemini's multimodal capabilities — vision, hearing, speech, and generation — in a new class of next-generation agents.

### 1.1 Core Theme
"Stop typing, and start interacting." The challenge pushes builders away from text-in/text-out interactions toward experiences that leverage the full multimodal capabilities of Gemini's Live API.

### 1.2 The Three Categories

| Category | Focus | Mandatory Tech |
| :--- | :--- | :--- |
| **Live Agents** | Real-time Interaction (Audio + Vision). Talk naturally, handle interruptions. | Gemini Live API or ADK. Hosted on Google Cloud. |
| **Creative Storyteller** | Multimodal Storytelling with Interleaved Output. Text + images + audio + video in one fluid stream. | Gemini interleaved/mixed output. Hosted on Google Cloud. |
| **UI Navigator** | Visual UI Understanding. Agent observes screen, interprets UI, performs actions. | Gemini multimodal screenshots/screen recordings. Hosted on Google Cloud. |

### 1.3 Universal Requirements (All Categories)
- Must leverage a Gemini model
- Agents must be built using Google GenAI SDK OR ADK (Agent Development Kit)
- Must use at least one Google Cloud service
- Must deploy backend on Google Cloud

### 1.4 Submission Checklist
1. **Text Description** — summary of features, technologies, data sources, findings & learnings
2. **Public Code Repository URL** — with spin-up instructions in README
3. **Proof of Google Cloud Deployment** — screen recording showing GCP deployment OR link to code file demonstrating Cloud service usage
4. **Architecture Diagram** — visual of system components (Gemini, backend, database, frontend)
5. **Demonstration Video** — under 4 minutes, demos multimodal features live (no mockups), pitches problem + value

> [!TIP]
> **JUDGE TIP**
> Pro tip from organizers: Add architecture diagram to file upload or image carousel so it's easy for judges to find.

### 1.5 Bonus Points Opportunities
- Publish content (blog, podcast, video) on how the project was built with Google AI + Cloud. Include: 'I created this for the Gemini Live Agent Challenge.' Use hashtag #GeminiLiveAgentChallenge on social media.
- Prove automated Cloud Deployment using scripts or infrastructure-as-code (IaC). Code must be in the public repository.
- Sign up for a Google Developer Group and provide link to your public GDG profile.

---

# SECTION 2 — PROJECT: LORE

## 2. LORE — Project Overview
LORE ("The World Is Your Documentary") is a next-generation Live Agent that fuses real-time camera vision with conversational AI to transform any physical location or spoken topic into a fully generated, interleaved documentary — complete with AI narration, Veo 3.1 video clips, Nano Banana 2 illustrations, and Search-grounded historical facts, all streaming simultaneously in real time.

### CORE CONCEPT
**LORE = TimeLens (camera-to-documentary) + DocuCast (voice-to-documentary) unified into a single app, where the combination unlocks a third mode — LoreMode — impossible in either app alone.**

### 2.1 The Problem LORE Solves
The world is full of history, stories, and knowledge — but accessing it is passive, fragmented, and text-heavy. A tourist at the Colosseum reads a plaque. A student Googles the 2008 financial crisis and gets a Wikipedia article. Neither experience is immersive, emotional, or contextually grounded in where the person is standing or what they're experiencing in the moment.

LORE solves this by making the entire world a living documentary. Point your camera, speak your question, and LORE generates a cinematic, narrated, illustrated experience — instantly, in real time.

### 2.2 The Genesis: Two Ideas Merged

| Dimension | TimeLens (Camera) | DocuCast (Voice) | LORE (Fusion) |
| :--- | :--- | :--- | :--- |
| **Primary Input** | Camera / Vision | Voice / Audio | Camera + Voice simultaneously |
| **Context Type** | Location-bound | Topic-bound | Location + Topic fused |
| **Core Output** | Narrated historical overlay | Interleaved streaming documentary | Cinematic documentary grounded in reality |
| **Signature Feature** | GPS Walking Tour Mode | Branch Documentaries | Alternate History Mode (exclusive to LoreMode) |
| **Session Memory** | Per-location facts | Per-topic thread | Full session — cross-reference any moment |
| **Unique Gap Filled** | Museum without walls | Research as experience | Grounded reality storytelling |

### 2.3 Target Markets
- **Tourism & Cultural Sites** — museums, monuments, heritage trails, archaeological sites
- **K-12 and Higher Education** — field trips, history class, immersive learning
- **Independent Learners** — people curious about history, current events, science
- **Journalists & Researchers** — rapid documentary-style briefings on any topic
- **Content Creators** — turn any location into a scripted, generated video piece

---

## 3. The Three Operating Modes
LORE operates across three distinct modes, each targeting a different input modality. All three share the same output engine — interleaved narration, illustrations, and video — but differ in how the user engages.

### 3.1 SightMode (TimeLens DNA)
**What It Does**
The user points their phone or tablet camera at any monument, battlefield, building, artifact, or landscape. LORE's Gemini Live API vision stream recognizes the location or object, cross-references Google Maps + Places API for metadata, runs Search grounding to verify historical facts, and begins streaming a full narrated documentary with generated illustrations and Veo 3.1 video clips — all within seconds.

**User Experience Flow**
1. Open LORE app — camera activates, Live API stream begins
2. Point at a location or object
3. Gemini Live identifies it (Maps + Places cross-reference)
4. Narration begins: "You're standing where 40,000 soldiers fought in 1764..."
5. Nano Banana 2 illustrations appear inline (sub-2 seconds)
6. Veo 3.1 clips stream: period-accurate cinematic scenes with native audio
7. User can interrupt at any time — Live API handles barge-in gracefully

**Signature Feature: GPS Walking Tour Mode**
As the user physically walks around a heritage site, LORE tracks GPS position and automatically triggers new narration + visuals for each zone — functioning as an invisible, infinitely knowledgeable audio guide layered with generated imagery. Each zone is a new documentary chapter.

**SAMPLE NARRATION**
*Example Output: "You are standing at the north entrance of the Red Fort, built in 1639 under Shah Jahan. Behind this wall, the last Mughal Emperor Bahadur Shah Zafar was arrested by the British in 1857 — the final act of an empire that had ruled for 300 years..."*

### 3.2 VoiceMode (DocuCast DNA)
**What It Does**
The user speaks any topic — historical event, scientific concept, current news story, biographical subject — and LORE generates a fully interleaved streaming documentary. This is not a text answer. It's a cinematic experience with narration, illustrations, and video flowing as one coherent output stream.

**The Interleaved Output Stream**
All of the following are generated simultaneously and delivered as a single coherent experience:
- **Narration** — Live API native audio in a chosen voice, tone-adaptive throughout the arc
- **Illustrations** — Gemini 3.1 Flash Image Preview generates scene illustrations, portraits, timelines, infographics inline (sub-2 seconds each)
- **Video Clips** — Veo 3.1 generates 8-60 second cinematic clips with native audio (dialogue, SFX, ambiance at 48kHz)
- **Data Visuals** — charts, maps, and timeline graphics generated in real time

**Signature Feature: Branch Documentaries**
At any point in the documentary, the user can interrupt with a deeper question or tap any claim to spawn a full sub-documentary. Sub-docs maintain the same voice, visual style, and character consistency. They nest inside the main documentary and return to the main thread when complete.

**Depth Dial**
At session start, the user sets their expertise level. LORE adapts its entire documentary — vocabulary, analogies, narrative complexity, and illustration style — accordingly across three levels: Explorer (general audience), Scholar (informed adult), and Expert (domain professional).

**SAMPLE SESSION**
*Example: User says "Explain the 2008 financial crisis for a first-time investor." LORE generates a 5-act documentary: Setup (2003 housing boom), Rising Tension (CDO creation), Crisis Point (Lehman collapse), Aftermath (bailout), and What This Means For You (personalized investor takeaways).*

### 3.3 LoreMode — The Fusion Breakthrough
**What It Does**
LoreMode activates camera AND voice simultaneously. Gemini Live API processes the live video stream AND the audio stream concurrently, fusing location context with topic intent to generate experiences impossible in either SightMode or VoiceMode alone.

**Why This Is New**
In SightMode, LORE knows WHERE you are but not what specific angle you want to explore. In VoiceMode, LORE knows WHAT you want but has no physical context. LoreMode gives LORE both — and the combination unlocks entirely new output modes.

**Signature Feature: Alternate History**
Standing at any historical site, the user asks a 'what if' question: 'What if Rome never fell?' or 'What if Carthage had won at Zama?' LORE generates an alternate-history Veo 3.1 film — grounded visually in the actual architecture and landscape the camera is seeing — narrated with the same dramatic voice. The generated video uses the real location's visual style as reference context, making the alternate history feel physically present.

**Cross-Session Memory Queries**
LoreMode also enables cross-mode memory queries. After visiting multiple sites or exploring multiple topics, the user can ask: 'Compare the Mughal Empire we saw at the Red Fort to the Ottoman Empire we just discussed.' LORE bridges both the visual and topical memory of the session.

**DEMO MOMENT**
*This is the core hackathon demo moment: Standing at the Colosseum, user asks 'What if Carthage had won the Punic Wars?' LORE generates a 30-second alternate-history Veo 3.1 clip — Carthaginian architecture layered over the Roman backdrop — narrated in a dramatic voice. Judges will not have seen anything like this.*

---

## 4. Full Feature Specification

| Feature | Description | Mode | Powered By |
| :--- | :--- | :--- | :--- |
| **Living Session Memory** | Remembers all frames seen, places visited, and questions asked within a session. Supports cross-location and cross-topic queries. | All | Gemini Live API + Firestore |
| **Affective Narration** | Detects user emotional tone from voice. Adjusts pacing, depth, and dramatic intensity accordingly. Amazed → go deeper. Confused → simplify. | All | Gemini Affective Dialog |
| **Veo 3.1 Scene Chains** | 8-60s cinematic clips with native audio (dialogue, SFX, ambiance at 48kHz). Clips chain for continuous narrative flow. 1080p output. | All | Veo 3.1 |
| **Gemini 3.1 Flash Image Preview Illustrations** | Sub-2-second portrait and scene illustrations. Character consistency maintained across all generated imagery in a session. | All | Gemini 3 Flash Preview Image |
| **GPS Walking Tour Mode** | Auto-triggers new narration + visuals as user moves through a heritage site. Each zone = new documentary chapter. | SightMode | Google Maps Platform |
| **Historical Character Encounters** | Generates a 'conversation' with a historical figure using Gemini 3.1 Flash Image Preview (consistent face) + Veo 3.1 (generated video). | SightMode / LoreMode | Veo 3.1 + Gemini 3.1 Flash Image Preview |
| **Branch Documentaries** | Interrupt or tap any claim to spawn a full sub-documentary. Nested, same voice + style, returns to main thread. | VoiceMode | ADK Orchestration |
| **Depth Dial** | Explorer / Scholar / Expert levels set at session start. Adapts vocabulary, analogies, narrative complexity, and visuals. | VoiceMode | Gemini 3 Flash Preview |
| **Alternate History Mode** | 'What if?' questions grounded in real location. Generates alternate-history Veo 3.1 film using camera visual as style reference. | LoreMode | Gemini Live + Veo 3.1 |
| **Chronicle Export** | Every session auto-assembles into an illustrated, narrated PDF — cover illustration (Gemini 3.1 Flash Image Preview), text (Gemini 3 Flash Preview), shareable. | All | Cloud Storage + Gemini |
| **Multilingual Ghost Guide** | Seamless multilingual support — user speaks any language, LORE responds in same language. 24 languages. | All | Live API (24 languages) |
| **Search-Grounded Truth** | Every historical claim verified against Google Search in real time. No hallucinated dates, figures, or events before streaming. | All | Google Search Grounding |
| **Live News Mode** | Swap historical topics for breaking news. DocuCast mode generates live mini-docs on today's top stories with grounded facts. | VoiceMode | Search Grounding + Live API |
| **Barge-In Handling** | User can interrupt narration at any time. LORE pauses, addresses the question, and returns to documentary seamlessly. | All | Gemini Live API |

---

## 5. Technology Stack
LORE is built on Google's complete multimodal AI stack, with each component chosen for a specific role in the generation pipeline. This is the only hackathon project that uses all three of Gemini's primary generative modalities simultaneously.

### 5.1 AI Models

| Model | API Identifier | Role in LORE | Badge |
| :--- | :--- | :--- | :--- |
| **Gemini Live API (Native Audio)** | gemini-2.5-flash-native-audio-preview-12-2025 | Core engine: real-time vision + audio + session memory + barge-in handling. Primary WebSocket stream. | **PRIMARY** |
| **Gemini 3 Flash Preview** | gemini-3-flash-preview | Documentary arc planning, Search grounding, fact verification, Depth Dial adaptation, branch logic. | **ORCHESTRATOR** |
| **Veo 3.1** | veo-3.1-generate-preview | 1080p video generation with native audio — dialogue (lip-sync <120ms), SFX, ambiance at 48kHz. Scene extension for sequences. | **VIDEO** |
| **Gemini 3.1 Flash Image Preview (Gemini 3 Flash Preview Image)** | gemini-3.1-flash-image-preview | Sub-2s illustrations with character consistency. Portraits, scene illustrations, infographics, timeline graphics. | **IMAGE** |
| **Gemini 2.5 Flash Native Audio (Live API)** | Included in Live API | 30 HD voices, 24 languages. Tone-adaptive narration — voice quality and pacing shift with emotional arc. | **AUDIO** |

### 5.2 Knowledge & Grounding

| Service | Role |
| :--- | :--- |
| **Google Search Grounding** | Real-time fact verification. All historical claims checked before streaming. Prevents hallucinated dates, figures, or events. |
| **Google Maps Platform** | Location recognition from camera frames, GPS walking tour triggers, site metadata, zone boundaries. |
| **Google Places API** | Site details, historical designations, cultural context, opening hours, accessibility info. |

### 5.3 Google Cloud Infrastructure

| Service | Role | Why This Service |
| :--- | :--- | :--- |
| **Agent Development Kit (ADK)** | Multi-agent orchestration: parallel generation streams, tool routing, session state management, branch logic. | Native Gemini integration, designed for multi-step agentic workflows. |
| **Cloud Run** | Containerized backend, WebSocket server for Live API, auto-scaling. Primary compute surface. | Serverless, scales to zero, ideal for event-driven WebSocket workloads. |
| **Firestore** | Session memory persistence, Chronicle accumulation, user preferences, branch history, walking tour progress. | Real-time document store, low-latency reads perfect for session state. |
| **Cloud Storage** | Veo 3.1 clip storage, Gemini 3.1 Flash Image Preview illustration caching, Chronicle PDF assembly, session archive. | Durable, globally distributed, integrates natively with other GCP services. |
| **Vertex AI** | Model hosting, Provisioned Throughput for Live API, Veo 3.1 generation endpoint. | Enterprise-grade model serving with guaranteed throughput for hackathon demo. |
| **Cloud Pub/Sub** | Async messaging between generation agents. Allows parallel Veo + Nano Banana + narration without blocking. | Decouples slow generation (Veo, 8-30s) from fast narration (<200ms). |

### 5.4 Frontend
- **Flutter (iOS + Android)** — camera access, WebSocket client, media streaming
- **WebSockets** — real-time bidirectional stream with Live API backend
- **AR overlay layer** — position-aware illustration overlays on camera feed (ARKit/ARCore or web equivalent)
- **Chronicle PDF viewer** — in-app viewing and sharing of generated session reports

---

## 6. System Architecture

### 6.1 High-Level Architecture
LORE follows a real-time streaming architecture with three parallel generation lanes orchestrated by ADK. All lanes are triggered by a single user session and deliver output to a unified client stream.

### 6.2 Data Flow
**Step 1 — Input Capture**
The mobile client opens a WebSocket connection to the Cloud Run backend. Camera frames stream as compressed video input at 10fps. Microphone audio streams as PCM audio. Both are multiplexed into the Gemini Live API session, which maintains a stateful connection.

**Step 2 — Intent Recognition (< 200ms)**
Gemini Live API processes the first frame batch and audio clip simultaneously:
- **Vision:** Identifies location/object → cross-references Maps API → retrieves site metadata
- **Audio:** Transcribes user speech → classifies intent (SightMode / VoiceMode / LoreMode)
- **Affective Dialog:** Detects emotional tone → sets narration intensity parameters
- **Search Grounding:** Gemini 3 Flash Preview verifies top 5 historical claims before narration begins

**Step 3 — ADK Orchestration**
ADK receives the intent package and launches three parallel agent workers via Pub/Sub:
- **Narration Agent** — Live API native audio begins streaming narration within 400ms
- **Illustration Agent** — Gemini 3.1 Flash Image Preview generates first illustration (sub-2s), queues remaining
- **Video Agent** — Veo 3.1 begins generating first 8-second clip (15-30s), delivers via Cloud Storage URL

**Step 4 — Interleaved Output Delivery**
The Cloud Run backend assembles the three streams into a single interleaved output stream and pushes to the client WebSocket. The client renders each component as it arrives — narration first (audio playback), then illustrations inline (as image elements), then video clips (as embedded players), all timed to the narrative arc.

**Step 5 — Session Memory Accumulation**
Firestore continuously writes: current GPS position, all recognized locations, all verified facts, all generated Chronicle content, user interruptions and branch paths. At session end, the Chronicle agent reads all Firestore records and assembles the final illustrated PDF.

### 6.3 Architecture Diagram (Text Representation)

```text
┌─────────────────────────────────────────────────────────────┐
│                     MOBILE CLIENT                            │
│  Camera Stream (10fps) │ Microphone (PCM) │ UI Rendering    │
└──────────────┬──────────────────┬──────────────────────────-┘
               │   WebSocket       │
               ▼                  ▼
┌─────────────────────────────────────────────────────────────┐
│              CLOUD RUN — WebSocket Gateway                   │
│  Session Manager │ Stream Multiplexer │ Output Assembler    │
└──────┬───────────────────────────────────────┬─────────────-┘
       │                                        │
       ▼                                        │
┌──────────────────────┐              ┌─────────▼────────────┐
│  GEMINI LIVE API     │              │  GEMINI 2.5 FLASH    │
│  Vision + Audio      │              │  (Thinking Mode)     │
│  Session Memory      │              │  Arc Planning        │
│  Affective Dialog    │              │  Search Grounding    │
│  Barge-In Handling   │              │  Fact Verification   │
└──────┬───────────────┘              └──────────────────────┘
       │  Intent Package (ADK)
       ▼
┌─────────────────────────────────────────────────────────────┐
│                  ADK ORCHESTRATOR                            │
│         Pub/Sub-driven Parallel Agent Launcher               │
└────┬──────────────────────┬──────────────────────┬─────────-┘
     │                      │                       │
     ▼                      ▼                       ▼
┌──────────┐        ┌──────────────┐        ┌──────────────┐
│ NARRATION│        │ILLUSTRATION  │        │ VIDEO AGENT  │
│  AGENT   │        │   AGENT      │        │              │
│Live API  │        │Gemini 3.1 Flash Image Preview │        │  Veo 3.1     │
│TTS Audio │        │gemini-2.5-   │        │  1080p clips │
│30HD voices│       │flash-image   │        │  Native audio│
│< 400ms   │        │< 2s per image│        │  15-30s gen  │
└──────────┘        └──────────────┘        └──────────────┘
     │                      │                       │
     └──────────────────────┼───────────────────────┘
                            │ Interleaved Stream
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                 GOOGLE CLOUD SERVICES                        │
│  Firestore (Session Memory) │ Cloud Storage (Media)         │
│  Vertex AI (Model Serving)  │ Maps API (Location)           │
│  Search (Fact Grounding)    │ Pub/Sub (Async Messaging)     │
└─────────────────────────────────────────────────────────────┘
```

---

## 7. Development Guide (Claude Code)
This section provides the step-by-step development roadmap optimized for building with Claude Code. Follow phases in order — each phase produces a working, testable increment.

### 7.1 Repository Structure

```text
lore/
├── apps/
│   ├── mobile/              # Flutter app (iOS + Android)
│   │   ├── src/
│   │   │   ├── screens/     # SightMode, VoiceMode, LoreMode screens
│   │   │   ├── components/  # Camera, waveform, illustration renderer
│   │   │   ├── services/    # WebSocket client, stream assembler
│   │   │   └── stores/      # Session state (Zustand)
│   │   └── package.json
│       └── ...
├── services/
│   ├── gateway/             # Cloud Run WebSocket server
│   │   ├── main.py          # FastAPI + WebSocket handler
│   │   ├── session.py       # Session manager
│   │   └── Dockerfile
│   ├── orchestrator/        # ADK agent orchestration
│   │   ├── agents/
│   │   │   ├── narration_agent.py
│   │   │   ├── illustration_agent.py
│   │   │   └── video_agent.py
│   │   └── adk_config.yaml
│   └── chronicle/           # Chronicle PDF generator
│       └── generator.py
├── infrastructure/
│   ├── terraform/           # IaC (bonus points)
│   │   ├── main.tf
│   │   ├── cloud_run.tf
│   │   ├── firestore.tf
│   │   └── pubsub.tf
│   └── deploy.sh            # One-command deployment script
├── docs/
│   ├── ARCHITECTURE.png     # Diagram for judges
│   └── LORE_DEV_BIBLE.docx  # This document
├── README.md                # Spin-up instructions
└── docker-compose.yml       # Local development
```

### 7.2 Phase 1 — Foundation (Days 1-2)
**Goals**
Working WebSocket connection between mobile client and Cloud Run. Basic Gemini Live API session established. Camera and microphone streaming.

**Tasks**
13. Set up GCP project, enable APIs: Vertex AI, Cloud Run, Firestore, Pub/Sub, Maps, Places
14. Build Cloud Run WebSocket gateway (FastAPI + uvicorn)
15. Establish Gemini Live API WebSocket session from backend
16. Build Flutter camera screen — stream frames to backend
17. Build Flutter microphone capture — stream PCM audio
18. Test: speak to app → receive text transcription back

> **Claude Code Prompt Hint**
>
> **CLAUDE CODE PROMPT**
> "Build a FastAPI WebSocket server that accepts binary video frame data and PCM audio from a Flutter client, forwards both streams to the Gemini Live API using the google-genai Python SDK, and returns text transcription responses back to the client in real time."

### 7.3 Phase 2 — SightMode MVP (Days 3-4)
**Goals**
Camera frame → location recognition → narration output. The core SightMode loop working end-to-end.

**Tasks**
19. Integrate Google Maps + Places API: lat/lng → place name + metadata
20. Implement Gemini 3 Flash Preview Search Grounding for historical fact verification
21. Connect Live API native audio TTS — stream narration audio back to client
22. Build client audio player for streaming narration
23. Add barge-in: user speech during narration pauses and re-routes
24. Store session data to Firestore: locations visited, facts surfaced

**Minimum Viable Demo**
Point camera at a landmark → LORE narrates its history in real time. This alone is impressive and fully validates the Live Agent category.

### 7.4 Phase 3 — Illustration + Video (Days 5-6)
**Goals**
Add Gemini 3.1 Flash Image Preview illustration generation and Veo 3.1 video clips to the output stream.

**Tasks**
25. Implement Gemini 3.1 Flash Image Preview (gemini-3.1-flash-image-preview) illustration generation — triggered by narration milestones
26. Design illustration prompts: scene illustrations, historical portraits, timeline graphics
27. Implement Veo 3.1 video generation via Vertex AI endpoint
28. Build Pub/Sub pipeline: narration starts → async Veo generation → deliver URL when ready
29. Build client illustration renderer — display images inline as they arrive
30. Build client video player — play Veo clips when URL delivered
31. Implement character consistency: store character descriptions in Firestore, re-use in subsequent generation prompts

### 7.5 Phase 4 — VoiceMode + Branch Docs (Day 7)
**Goals**
Full VoiceMode experience: speak any topic → full interleaved documentary. Branch documentary logic.

**Tasks**
32. Build VoiceMode screen — voice-only input, no camera
33. Implement Gemini 3 Flash Preview documentary arc planner
34. Build branch detection: classify user interruptions as branch requests vs. clarifications
35. Implement Branch Documentary: spawn sub-session, maintain parent context, return on completion
36. Build Depth Dial UI — Explorer / Scholar / Expert selection
37. Test: explain complex topic at all three depth levels

### 7.6 Phase 5 — LoreMode + Polish (Days 8-9)
**Goals**
LoreMode (camera + voice fusion) working. Alternate History feature. Chronicle export. Full polish.

**Tasks**
38. Build LoreMode: simultaneous camera + voice input, fused intent recognition
39. Implement Alternate History: extract 'what if' intent, pass camera visual as style reference to Veo 3.1
40. Build Chronicle generator: read Firestore session data, assemble illustrated PDF
41. Implement GPS Walking Tour Mode: geofence triggers for zone-based chapter switching
42. Add multilingual support: language detection from audio, response language matching
43. Final UI polish: animations, transitions, error states, loading states

### 7.7 Phase 6 — Deployment + Submission (Day 10)
**Tasks**
44. Write Terraform IaC: Cloud Run, Firestore, Pub/Sub, Storage (bonus points)
45. Write deploy.sh: one-command deployment script
46. Write README with complete spin-up instructions
47. Record GCP proof: screen recording of Cloud Run deployment + Vertex AI model calls
48. Record 4-minute demo video: LoreMode Alternate History → SightMode walking tour → VoiceMode branch doc
49. Create architecture diagram (export from diagram tool, add to submission)
50. Write project description: features, technologies, learnings
51. Publish blog post with #GeminiLiveAgentChallenge (bonus points)

---

## 8. Key API Integration Notes

### 8.1 Gemini Live API
The Live API uses a persistent WebSocket connection. Key implementation notes:
- Use google-genai Python SDK (not REST) — the SDK handles WebSocket lifecycle management
- Session must be kept alive — implement heartbeat / reconnect logic for long walking tours
- Audio format: PCM 16-bit, 16kHz for input; Live API returns PCM audio for playback
- Video format: Send JPEG frames at 10fps — base64-encoded in the message payload
- Barge-in: Send audio even during narration playback — Live API handles interruption detection natively
- Affective Dialog: Available in gemini-2.5-flash-native-audio-preview-12-2025 — no extra configuration needed
- Session memory: Persisted within WebSocket session automatically — survives barge-ins and branches

### 8.2 Veo 3.1
Veo 3.1 is available through Vertex AI. Key notes:
- **API:** POST to `projects/{project}/locations/us-central1/publishers/google/models/veo-3.1-generate-preview:generateVideo`
- **Generation time:** 15-30 seconds for 8-second clip — always async, poll for completion or use callback
- **Native audio:** Enabled by default in 3.1 — include `audio_config: { enable_native_audio: true }` in request
- **Scene extension:** Pass `last_frame` from previous clip as `reference_image` to chain clips
- **Style reference:** Pass camera frame as `reference_image` to ground alternate history in real location
- **Character consistency:** Pass portrait description + optional reference image as `character_reference`

### 8.3 Gemini 3.1 Flash Image Preview (Gemini 3 Flash Preview Image)
Available via the standard Gemini API with image output enabled:
- **Model:** gemini-3.1-flash-image-preview (also called 'Gemini 3.1 Flash Image Preview' internally)
- **Generation time:** sub-2 seconds — can be called synchronously without blocking narration
- **Character consistency:** Include character description + seed in every prompt for same character
- **Prompt structure:** Always include art style, time period, lighting, and character details in prompt

### 8.4 Google Search Grounding
Enabled via the `tools` parameter in Gemini API calls:

```python
# Python example — enabling Search Grounding
from google import genai
from google.genai.types import Tool, GoogleSearch
 
client = genai.Client()
response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents="Verify: The Battle of Plassey was fought in 1757.",
    config=genai.types.GenerateContentConfig(
        tools=[Tool(google_search=GoogleSearch())]
    )
)
```

### 8.5 ADK (Agent Development Kit)
ADK orchestrates the three parallel generation agents. Key patterns:
- Define each agent (Narration, Illustration, Video) as an ADK Agent class with specific tools
- Use `ParallelAgent` to run all three simultaneously after intent recognition
- Use `SequentialAgent` for branch documentary logic (main → branch → return)
- Pass session context (Firestore document ID) as shared state across all agents
- Use ADK's built-in tool routing to direct fact-check requests to Search Grounding

---

## 9. Demo Video Strategy
The 4-minute demo video is the most important submission artifact. Judges will watch this first. Structure it to maximize impact in each 60-second window.

### 9.1 Recommended Demo Script

| Timestamp | Scene | What It Shows | Wow Factor |
| :--- | :--- | :--- | :--- |
| **0:00 – 0:30** | Cold open: point camera at a monument, LORE starts narrating + illustration appears | SightMode working end-to-end, sub-2s illustration | Instant reaction — no typing, no waiting |
| **0:30 – 1:00** | Walk to a different zone — new narration triggers automatically via GPS | Walking Tour Mode, session memory | Feels like an invisible museum guide |
| **1:00 – 1:45** | Activate LoreMode: camera on Colosseum, ask 'What if Carthage had won?' | LoreMode fusion, Alternate History, Veo 3.1 clip | **THIS is the unforgettable moment** |
| **1:45 – 2:30** | Switch to VoiceMode: 'Explain the 2008 financial crisis' — show full interleaved output | VoiceMode, interleaved stream, branch on 'CDO' | Shows the full multimodal output stack |
| **2:30 – 3:00** | Show Chronicle export — illustrated PDF assembles from session | Chronicle, Cloud Storage, full session memory | Shows practical output users can keep |
| **3:00 – 3:30** | Behind the scenes: show Cloud Run dashboard, Vertex AI, Firestore in real time | Proof of Google Cloud deployment | Satisfies judge requirement elegantly |
| **3:30 – 4:00** | Pitch close: 'LORE turns the entire world into a living documentary' | Value proposition, vision, scale | Emotional close, memorable tagline |

**DEMO RISK MANAGEMENT**
> [!IMPORTANT]
> CRITICAL: The Alternate History moment (1:00-1:45) must work flawlessly. Pre-generate the Veo clip if needed for demo reliability. The judges need to see the camera feed + voice input + generated video output simultaneously on screen. Consider a split-screen: phone camera on left, LORE output on right.

### 9.2 What Makes LORE Win the Demo
- It is the ONLY project using all three generative modalities (audio + image + video) simultaneously in one stream
- The Alternate History feature is visually stunning and conceptually unique — no other team will have this
- The use case is immediately understandable: everyone has stood at a historical site wishing they knew more
- The walking tour moment feels magical — location changes, narration changes, no button press needed
- Chronicle export shows real-world utility beyond the demo moment

---

## 10. Final Submission Checklist

### 10.1 Required Deliverables

| Item | Owner | Status | Notes |
| :--- | :--- | :--- | :--- |
| **Text Description** | Team | TODO | Summary: features, tech, data sources, learnings |
| **Public Code Repository** | Team | TODO | GitHub, public, spin-up instructions in README |
| **Proof of GCP Deployment** | Team | TODO | Screen recording of Cloud Run + Vertex AI in action |
| **Architecture Diagram** | Team | TODO | Export as PNG, add to repo /docs folder and submission |
| **4-Minute Demo Video** | Team | TODO | No mockups, LoreMode Alternate History must be shown |

### 10.2 Bonus Deliverables

| Bonus Item | Points Value | Status | Action |
| :--- | :--- | :--- | :--- |
| **Blog / Video / Podcast** | Bonus | TODO | Publish with #GeminiLiveAgentChallenge, reference hackathon |
| **IaC Deployment (Terraform)** | Bonus | TODO | terraform/ folder in repo with complete GCP infrastructure |
| **Google Developer Group** | Bonus | TODO | Sign up at gdg.community.dev, link public profile in submission |

### 10.3 README Requirements
The README must enable judges to reproduce the project. Include:
52. Prerequisites: GCP project, APIs enabled, credentials
53. Environment variables: list all required env vars with descriptions
54. Local development: `docker-compose up` command
55. GCP deployment: `deploy.sh` or Terraform instructions
56. Demo walkthrough: which screen to open first, how to trigger each mode
57. Architecture diagram: embedded in README
58. Known limitations: any features that require specific hardware/location

## 11. Competitive Differentiation
Most hackathon submissions will build one of these common patterns: AR tour guide, AI tutor, voice assistant, or news summarizer. LORE is differentiated at every level:

| Dimension | Typical Hackathon Entry | LORE |
| :--- | :--- | :--- |
| **Modalities Used** | 1-2 (usually text + voice) | 3 simultaneously (vision + audio + video generation) |
| **Output Type** | Text or audio response | Interleaved documentary: narration + illustrations + video |
| **Technical Novelty** | Single API call chain | Parallel ADK agent orchestration with Pub/Sub |
| **Gemini Stack Coverage** | Partial (1-2 APIs) | Complete: Live API + Flash Thinking + Veo 3.1 + Gemini 3.1 Flash Image Preview + Search |
| **User Experience** | Chat-like interaction | Cinematic, immersive, feels like a film generated for you |
| **Demo Moment** | Functional but expected | Alternate History: Veo 3.1 clip grounded in live camera feed |
| **Market Size** | Niche tool | Tourism ($1.9T industry) + Education (global) + Media |
| **Post-Hackathon Path** | Often abandoned | Clear SaaS path: museums, schools, content platforms |

---

The world has always had stories.
LORE just learned to tell them.

**TIMELENS × DOCUCAST → LORE ◆ GEMINI LIVE AGENT CHALLENGE 2025**
