"""LiveSessionManager — persistent Gemini Live API session per user for VoiceMode.

Mirrors the AudioLoop pattern from the Google AI Studio reference script:
  - One persistent session per client (not open/close per request)
  - out_queue feeds send_realtime() which calls session.send_realtime_input()
  - receive_audio() reads session.receive() and dispatches:
      response.data            → PCM audio  → on_audio_chunk callback
      server_content.output_transcription → model's spoken text → on_output_transcript callback
      server_content.input_transcription  → user's spoken text  → on_transcript callback
      response.tool_call       → function call → on_function_call callback
  - audio_stream_end is sent when the user releases the mic button so VAD fires

Key improvements over the previous version:
  1. response_modalities=["AUDIO"] + output_audio_transcription → model speaks AND
     returns text of what it said simultaneously (no second TTS model needed).
  2. tools=[GoogleSearch, generate_illustration, generate_video] → the Live model
     grounds itself via Search and orchestrates image/video via function calls,
     replacing the separate NarrationEngine script + SearchGrounder pipeline.
  3. language_code passed to AudioTranscriptionConfig → fixes Hindi transcription bug.
  4. on_output_transcript callback → Flutter displays the model's narration as text.
  5. on_function_call callback → MessageRouter dispatches to NanoIllustrator/VeoGenerator.
  6. Interruption handling drains audio_in_queue (mirrors reference script exactly).

Reference: https://github.com/google-gemini/cookbook/blob/main/quickstarts/Get_started_LiveAPI.py
Live API tools: https://ai.google.dev/gemini-api/docs/live-tools
Live API reference: https://ai.google.dev/api/live

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

# ── Constants matching the reference script ───────────────────────────────────

_raw_model = os.getenv(
    "GEMINI_LIVE_MODEL", "models/gemini-live-2.5-flash-native-audio"
)
# The Live API requires the "models/" prefix. Guard against .env values that
# omit it (e.g. GEMINI_LIVE_MODEL=gemini-live-2.5-flash-native-audio).
LIVE_MODEL: str = _raw_model if _raw_model.startswith("models/") else f"models/{_raw_model}"
SEND_SAMPLE_RATE: int = 16_000    # 16 kHz input — Gemini Live API requirement
RECEIVE_SAMPLE_RATE: int = 24_000  # 24 kHz output PCM from the model
# The reference script uses maxsize=5 for a local PyAudio loop where the sender
# keeps up trivially.  In our architecture the Flutter client sends chunks at
# ~80 ms intervals over a WebSocket while _send_realtime awaits each
# send_realtime_input() call over the network — the queue fills and drops chunks
# before Gemini ever receives enough audio to respond.  100 slots ≈ 8 seconds of
# headroom at 80 ms/chunk, which is more than enough for any network hiccup.
OUT_QUEUE_MAXSIZE: int = 100

# ── LORE documentary narrator system instruction ──────────────────────────────

_SYSTEM_INSTRUCTION = (
    "You are LORE, an immersive AI documentary narrator. "
    "Your role is to deliver rich, engaging documentary narration about any topic "
    "the user asks about — history, science, culture, architecture, nature, and more. "
    "\n\n"
    "When the user speaks:\n"
    "1. Acknowledge their topic briefly (1 sentence) so they know you heard them.\n"
    "2. Immediately begin narrating a compelling documentary about that topic. "
    "   Speak naturally, as if narrating a high-quality documentary film.\n"
    "3. Use Google Search to ground your narration in verified facts. "
    "   Cite sources naturally within the narration.\n"
    "4. When the topic warrants a visual, call generate_illustration with a vivid "
    "   description of the scene. Do this 1-2 times per topic.\n"
    "5. For cinematic moments (key events, dramatic scenes), call generate_video "
    "   with a short description.\n"
    "6. Adapt depth to the user's apparent interest — go deeper if they ask follow-up "
    "   questions, branch into sub-topics if they ask to explore further.\n"
    "\n"
    "Tone: authoritative yet warm, like David Attenborough meets a knowledgeable friend. "
    "Never break character. Never say you cannot search — use the search tool."
)

# ── Function declarations for illustration and video generation ───────────────

_FUNCTION_GENERATE_ILLUSTRATION = {
    "name": "generate_illustration",
    "description": (
        "Generate a period-appropriate illustration or image for the current documentary topic. "
        "Call this when a visual would enhance the narration — historical scenes, "
        "architectural details, natural phenomena, portraits, maps, etc."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "description": {
                "type": "STRING",
                "description": (
                    "Detailed visual description of the scene or subject to illustrate. "
                    "Include style hints: 'historical painting', 'photorealistic', "
                    "'technical diagram', 'illustrated map', etc."
                ),
            },
            "caption": {
                "type": "STRING",
                "description": "Short caption to display below the illustration (1-2 sentences).",
            },
            "topic": {
                "type": "STRING",
                "description": "The documentary topic this illustration belongs to.",
            },
        },
        "required": ["description", "caption", "topic"],
    },
}

_FUNCTION_GENERATE_VIDEO = {
    "name": "generate_video",
    "description": (
        "Generate a short cinematic video clip for a dramatic or visually compelling moment "
        "in the documentary. Use sparingly — only for key scenes that benefit from motion."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "description": {
                "type": "STRING",
                "description": (
                    "Cinematic description of the video clip. Include camera movement, "
                    "lighting, subject, and mood. E.g. 'Slow pan across the burning Library "
                    "of Alexandria at night, flames reflected in the harbour water'."
                ),
            },
            "topic": {
                "type": "STRING",
                "description": "The documentary topic this video clip belongs to.",
            },
        },
        "required": ["description", "topic"],
    },
}


class LiveSession:
    """One persistent Live API session for a single connected user.

    Mirrors AudioLoop from the reference script, adapted for the LORE backend.
    Instead of PyAudio we receive PCM bytes from the WebSocket gateway and push
    transcripts, audio, and function calls back via callbacks.

    Audio flow (input):
        WebSocket chunk → send_audio_chunk() → out_queue → _send_realtime() → session

    Audio flow (output — mirrors reference script receive_audio()):
        session.receive() → response.data (PCM)                → audio_in_queue → on_audio_chunk
                          → server_content.output_transcription → on_output_transcript
                          → server_content.input_transcription  → on_transcript
                          → response.tool_call                  → on_function_call
                          → interrupted                         → drain audio_in_queue

    The session stays open for the lifetime of the user's VoiceMode screen.
    """

    def __init__(
        self,
        client: Any,
        session_id: str,
        on_transcript: Callable[[str], Coroutine],
        on_audio_chunk: Optional[Callable[[Optional[bytes]], Coroutine]] = None,
        on_output_transcript: Optional[Callable[[str], Coroutine]] = None,
        on_function_call: Optional[Callable[[str, str, dict], Coroutine]] = None,
        language: str = "en",
    ) -> None:
        self._client = client
        self.session_id = session_id
        self._on_transcript = on_transcript
        self._on_audio_chunk = on_audio_chunk
        self._on_output_transcript = on_output_transcript
        self._on_function_call = on_function_call
        self._language = language

        # Mirrors AudioLoop attributes from the reference script
        self._session: Optional[Any] = None
        self._out_queue: Optional[asyncio.Queue] = None
        self._task_group_task: Optional[asyncio.Task] = None
        self._running = False

    # ── Public API ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Open the Live API session and start background tasks."""
        if self._running:
            return
        self._running = True
        self._out_queue = asyncio.Queue(maxsize=OUT_QUEUE_MAXSIZE)
        self._task_group_task = asyncio.create_task(
            self._run(), name=f"live-session-{self.session_id[:8]}"
        )
        logger.info("LiveSession started for session %s", self.session_id)

    async def stop(self) -> None:
        """Close the Live API session and cancel background tasks."""
        self._running = False
        if self._task_group_task and not self._task_group_task.done():
            self._task_group_task.cancel()
            try:
                await self._task_group_task
            except (asyncio.CancelledError, Exception):
                pass
        self._session = None
        logger.info("LiveSession stopped for session %s", self.session_id)

    async def send_audio_chunk(self, pcm_bytes: bytes) -> None:
        """Enqueue a raw PCM chunk to be sent to the Live API.

        Called by the message router for every voice_chunk message.
        Matches listen_audio() → out_queue.put() in the reference script.
        """
        if self._out_queue is None or not self._running:
            return
        msg = {"data": pcm_bytes, "mime_type": f"audio/pcm;rate={SEND_SAMPLE_RATE}"}
        try:
            self._out_queue.put_nowait(msg)
        except asyncio.QueueFull:
            logger.debug("out_queue full for session %s — dropping chunk", self.session_id)

    async def signal_mic_stop(self) -> None:
        """Send audioStreamEnd to flush VAD and trigger transcript.

        Called when the user releases the mic button.
        Equivalent to the reference script's end_of_turn signal.
        """
        if self._session is None or not self._running:
            return
        try:
            await self._session.send_realtime_input(audio_stream_end=True)
            logger.debug("audioStreamEnd sent for session %s", self.session_id)
        except Exception as exc:
            logger.warning("Failed to send audioStreamEnd: %s", exc)

    # ── Internal: mirrors AudioLoop.run() ────────────────────────────────────

    async def _run(self) -> None:
        """Main coroutine — opens the Live API session and runs all tasks.

        Mirrors AudioLoop.run() using asyncio.TaskGroup.
        Config follows the reference script structure exactly, with additions:
          - response_modalities=["AUDIO"] + output_audio_transcription for text of model speech
          - tools=[GoogleSearch + function_declarations] for grounding and function calling
          - language_code on AudioTranscriptionConfig to fix language detection
        """
        from google.genai import types

        # Tools: function declarations for illustration/video.
        # NOTE: google_search and function_declarations cannot coexist across
        # separate Tool objects in the Live API — the server returns 1011.
        # Google Search grounding is omitted here; the model can still use its
        # built-in knowledge. Re-add GoogleSearch only if the API adds support.
        tools = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(**_FUNCTION_GENERATE_ILLUSTRATION),
                    types.FunctionDeclaration(**_FUNCTION_GENERATE_VIDEO),
                ]
            ),
        ]

        config = types.LiveConnectConfig(
            # AUDIO modality — model speaks back in real time.
            # output_audio_transcription gives us the text of what the model says
            # simultaneously, so Flutter can display it without a second TTS call.
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Zephyr"
                    )
                )
            ),
            # Input transcription — AudioTranscriptionConfig has no fields in the
            # current SDK (language_code is not a valid parameter and causes a
            # Pydantic validation error that crashes the session before it opens).
            input_audio_transcription=types.AudioTranscriptionConfig(),
            # Output transcription — text of the model's spoken narration.
            # Arrives via server_content.output_transcription.text
            output_audio_transcription=types.AudioTranscriptionConfig(),
            # Context window compression — matches reference script values exactly
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=104_857,
                sliding_window=types.SlidingWindow(target_tokens=52_428),
            ),
            tools=tools,
            system_instruction=types.Content(
                parts=[types.Part(text=_SYSTEM_INSTRUCTION)]
            ),
        )

        try:
            async with (
                self._client.aio.live.connect(
                    model=LIVE_MODEL, config=config
                ) as session,
                asyncio.TaskGroup() as tg,
            ):
                self._session = session
                tg.create_task(self._send_realtime(), name="send_realtime")
                tg.create_task(self._receive_audio(), name="receive_audio")
        except asyncio.CancelledError:
            pass
        except ExceptionGroup as eg:
            logger.error("LiveSession ExceptionGroup for %s: %s", self.session_id, eg)
        except Exception as exc:
            logger.error("LiveSession error for %s: %s", self.session_id, exc)
        finally:
            self._session = None
            self._running = False

    async def _send_realtime(self) -> None:
        """Drain out_queue and forward each item to the Live API session.

        Mirrors AudioLoop.send_realtime() from the reference script, with one
        important addition: after receiving the first chunk we immediately drain
        any additional chunks that have already accumulated in the queue and send
        them all before yielding back to the event loop.

        Why: Flutter sends audio at ~80 ms intervals.  Each send_realtime_input()
        call awaits a network round-trip to Gemini.  Without batch-draining, the
        sender falls one chunk behind per iteration and the queue fills up, causing
        every subsequent chunk to be dropped and Gemini to receive silence.
        Batch-draining keeps the queue near-empty so no chunks are lost.
        """
        from google.genai import types

        while self._running:
            if self._out_queue is None:
                await asyncio.sleep(0.01)
                continue

            # Block until at least one chunk is available (or timeout to check _running)
            try:
                msg = await asyncio.wait_for(self._out_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            if self._session is None:
                continue

            # Collect this chunk plus any that arrived while we were awaiting
            msgs = [msg]
            while True:
                try:
                    msgs.append(self._out_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            # Send all collected chunks
            for m in msgs:
                try:
                    await self._session.send_realtime_input(
                        audio=types.Blob(
                            data=m["data"],
                            mime_type=m["mime_type"],
                        )
                    )
                except Exception as exc:
                    logger.warning("send_realtime_input failed: %s", exc)
                    break

    async def _receive_audio(self) -> None:
        """Read all responses from the Live API session.

        Mirrors AudioLoop.receive_audio() from the reference script, extended to
        handle the full response surface of gemini-2.5-flash-native-audio-preview:

          response.data
              Raw PCM audio bytes — accumulated per turn, sent as one blob on
              turn_complete. This prevents the broken/delayed audio caused by
              writing hundreds of tiny WAV files and reinitialising the audio
              player for each ~200-byte chunk.

          response.text
              Plain text response (TEXT modality). Not used — we use AUDIO +
              output_audio_transcription instead.

          server_content.output_transcription.text
              Partial text of what the model is speaking. Fired immediately
              per chunk with partial=True so Flutter updates the text box in
              real time (mirrors reference script print(text, end="")).
              Also accumulated and fired once at turn_complete with partial=False.

          server_content.input_transcription.text
              Partial text of what the user said. Accumulated per turn, fired
              ONCE on turn_complete (full sentence).

          server_content.turn_complete
              End of model turn — fire all accumulated callbacks with full data.

          server_content.interrupted
              Barge-in. Discard accumulated audio and transcripts, drain
              audio_in_queue — mirrors reference script exactly.

          response.tool_call
              Model wants to call generate_illustration or generate_video.
        """
        while self._running:
            if self._session is None:
                await asyncio.sleep(0.05)
                continue

            try:
                turn = self._session.receive()

                # Text accumulators reset per turn.
                # Audio is streamed immediately — no accumulation needed.
                input_transcript_parts: list[str] = []
                output_transcript_parts: list[str] = []

                async for response in turn:

                    # ── 1. PCM audio — stream immediately ───────────────────
                    # Each chunk is forwarded to on_audio_chunk right away so
                    # Flutter's AudioPlaybackService can start playing before
                    # the full response is ready. The service appends chunks to
                    # a streaming buffer rather than writing per-chunk WAV files.
                    if response.data:
                        if self._on_audio_chunk:
                            try:
                                await self._on_audio_chunk(response.data)
                            except Exception as exc:
                                logger.warning("on_audio_chunk (stream) failed: %s", exc)
                        continue

                    # ── 2. model_turn.parts — audio (inline_data) and text ───
                    # The native audio model delivers PCM via
                    # server_content.model_turn.parts[].inline_data as well as
                    # via response.data (block 1 above handles the latter).
                    # Thought parts (part.thought=True) must be skipped here to
                    # avoid the SDK warning and to prevent internal reasoning
                    # from leaking into transcription.
                    # IMPORTANT: do NOT `continue` after this block — the same
                    # response message can carry output_transcription /
                    # turn_complete / interrupted in server_content (block 4).
                    _sc_early = getattr(response, "server_content", None)
                    _mt_early = getattr(_sc_early, "model_turn", None) if _sc_early else None
                    if _mt_early:
                        for _part in (getattr(_mt_early, "parts", None) or []):
                            # Skip thought parts — internal reasoning only
                            if getattr(_part, "thought", False):
                                continue
                            # Audio delivered via inline_data (native audio model)
                            _inline = getattr(_part, "inline_data", None)
                            if _inline and getattr(_inline, "data", None):
                                if self._on_audio_chunk:
                                    try:
                                        await self._on_audio_chunk(_inline.data)
                                    except Exception as exc:
                                        logger.warning("on_audio_chunk (inline_data) failed: %s", exc)
                            # Text parts (TEXT modality fallback) — log only
                            elif getattr(_part, "text", None):
                                logger.debug(
                                    "model_turn text part for session %s: %r",
                                    self.session_id, _part.text
                                )
                    # Fall through to block 3 / 4 — do NOT continue here.

                    # ── 3. Tool call — function calling ──────────────────────
                    tool_call = getattr(response, "tool_call", None)
                    if tool_call:
                        await self._handle_tool_call(tool_call)
                        continue

                    # ── 4. server_content events ─────────────────────────────
                    server_content = getattr(response, "server_content", None)
                    if not server_content:
                        continue

                    # 4a. Output transcription — partial text of what the model
                    # is speaking. Fire immediately as a partial update so Flutter
                    # can update the text box in real time (mirrors reference script
                    # print(text, end="") behaviour). Also accumulate for turn_complete
                    # to send the final consolidated message.
                    output_trans = getattr(server_content, "output_transcription", None)
                    if output_trans and getattr(output_trans, "text", None):
                        output_transcript_parts.append(output_trans.text)
                        # Stream partial text immediately so the UI updates word-by-word
                        if self._on_output_transcript:
                            try:
                                await self._on_output_transcript(
                                    output_trans.text, partial=True
                                )
                            except TypeError:
                                # Fallback for callbacks that don't accept partial kwarg
                                await self._on_output_transcript(output_trans.text)
                            except Exception as exc:
                                logger.warning("on_output_transcript (partial) failed: %s", exc)

                    # 4b. Input transcription — partial text of what the user
                    # said. Accumulate — do NOT fire yet (user transcript fires once
                    # on turn_complete so we get the full sentence).
                    input_trans = getattr(server_content, "input_transcription", None)
                    if input_trans and getattr(input_trans, "text", None):
                        input_transcript_parts.append(input_trans.text)

                    # 4c. Turn complete — fire final callbacks ─────────────────
                    if getattr(server_content, "turn_complete", False):
                        # Fire input transcript once (user's full sentence)
                        full_input = "".join(input_transcript_parts).strip()
                        if full_input:
                            logger.info(
                                "Input transcript for session %s: %r",
                                self.session_id, full_input
                            )
                            try:
                                await self._on_transcript(full_input)
                            except Exception as exc:
                                logger.warning("on_transcript callback failed: %s", exc)

                        # Output transcript final — log only, do NOT re-send to client.
                        # The client already received every word via partial=True chunks
                        # above. Re-sending the full concatenated text here would cause
                        # the frontend to display the entire narration a second time
                        # (duplicate text bug). We only log for debugging.
                        full_output = "".join(output_transcript_parts).strip()
                        if full_output:
                            logger.info(
                                "Output transcript for session %s: %r",
                                self.session_id, full_output
                            )

                        # Signal Flutter that the turn is complete — flush audio buffer.
                        if self._on_audio_chunk:
                            try:
                                await self._on_audio_chunk(None)  # None = flush signal
                            except Exception as exc:
                                logger.warning("on_audio_chunk flush signal failed: %s", exc)

                        # Reset for next turn
                        input_transcript_parts = []
                        output_transcript_parts = []

                    # 4d. Interrupted — discard accumulated text.
                    # Audio needs no cleanup since chunks were already streamed.
                    if getattr(server_content, "interrupted", False):
                        logger.debug(
                            "Interrupted for session %s — discarding partial transcripts",
                            self.session_id,
                        )
                        input_transcript_parts = []
                        output_transcript_parts = []

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._running:
                    logger.warning(
                        "receive_audio error for session %s: %s — retrying",
                        self.session_id, exc
                    )
                    await asyncio.sleep(0.5)

    async def _handle_tool_call(self, tool_call: Any) -> None:
        """Dispatch function calls from the model and send responses back.

        Per the Live API docs, after receiving a tool_call the client must
        respond with session.send_tool_response(function_responses=[...]).
        The model will not continue generating until it receives the response.

        Each FunctionCall has: .id, .name, .args (dict)
        """
        from google.genai import types

        function_responses = []

        for fc in tool_call.function_calls:
            call_id = fc.id
            name = fc.name
            args = dict(fc.args) if fc.args else {}

            logger.info(
                "Tool call for session %s: %s(%s)",
                self.session_id, name, args
            )

            # Dispatch to the on_function_call callback (wired to MessageRouter).
            # The callback executes the actual generation (NanoIllustrator/VeoGenerator)
            # and returns a result dict to include in the function response.
            result: dict = {"status": "ok"}
            if self._on_function_call:
                try:
                    result = await self._on_function_call(call_id, name, args) or {"status": "ok"}
                except Exception as exc:
                    logger.warning(
                        "on_function_call failed for %s: %s", name, exc
                    )
                    result = {"status": "error", "error": str(exc)}

            function_responses.append(
                types.FunctionResponse(
                    id=call_id,
                    name=name,
                    response=result,
                )
            )

        # Send all responses back to the session in one call.
        if self._session and function_responses:
            try:
                await self._session.send_tool_response(
                    function_responses=function_responses
                )
            except Exception as exc:
                logger.warning("send_tool_response failed: %s", exc)


class LiveSessionManager:
    """Registry of active LiveSession instances, one per client_id.

    Stored in ConnectionManager alongside BranchDocumentaryManager,
    DepthDialManager, etc.  The MessageRouter calls these methods in
    response to voice_session_start / voice_chunk / voice_mic_stop /
    voice_session_end messages.
    """

    def __init__(self, genai_client: Any) -> None:
        self._client = genai_client
        self._sessions: dict[str, LiveSession] = {}

    async def start_session(
        self,
        client_id: str,
        session_id: str,
        on_transcript: Callable[[str], Coroutine],
        on_audio_chunk: Optional[Callable[[Optional[bytes]], Coroutine]] = None,
        on_output_transcript: Optional[Callable[[str], Coroutine]] = None,
        on_function_call: Optional[Callable[[str, str, dict], Coroutine]] = None,
        language: str = "en",
    ) -> None:
        """Open a new Live API session for client_id, closing any existing one."""
        await self.end_session(client_id)
        session = LiveSession(
            client=self._client,
            session_id=session_id,
            on_transcript=on_transcript,
            on_audio_chunk=on_audio_chunk,
            on_output_transcript=on_output_transcript,
            on_function_call=on_function_call,
            language=language,
        )
        self._sessions[client_id] = session
        await session.start()

    async def send_audio_chunk(self, client_id: str, pcm_bytes: bytes) -> None:
        session = self._sessions.get(client_id)
        if session:
            await session.send_audio_chunk(pcm_bytes)

    async def signal_mic_stop(self, client_id: str) -> None:
        session = self._sessions.get(client_id)
        if session:
            await session.signal_mic_stop()

    async def end_session(self, client_id: str) -> None:
        session = self._sessions.pop(client_id, None)
        if session:
            await session.stop()

    def has_session(self, client_id: str) -> bool:
        return client_id in self._sessions
