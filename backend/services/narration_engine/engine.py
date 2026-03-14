"""Narration Engine — real-time voice narration via Gemini Live API.

Design reference: LORE design.md, Section 3 – Narration Engine.
Requirements:
  3.1 — Continuous voice input using Gemini Live API
  3.2 — Transcribe speech within 500 ms
  5.2 — Begin audio output within 2 seconds of trigger
  11.5 — Use Gemini Live API Native Audio for voice synthesis

Architecture notes
------------------
The NarrationEngine wraps the Gemini Live API (google-genai SDK) to:

  1. Generate narration scripts from a NarrationContext (topic + location +
     depth dial).  Script generation uses the standard Gemini text model
     (gemini-3-flash-preview) for fast, structured output.

  2. Synthesise speech via the Gemini Live API native audio model
     (gemini-live-2.5-flash-native-audio).  Audio is streamed back as
     24 kHz / 16-bit / mono PCM chunks through an async generator so the
     WebSocket gateway can forward them to the client in real time.

  3. Delegate tone selection to the AffectiveNarrator and encode the
     desired vocal style as a system instruction + voice name selection.

Dependency injection
  The genai Client is injected so unit tests can replace it with a mock.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from typing import Any, AsyncIterator, Optional

from .affective_narrator import AffectiveNarrator
from .models import (
    AudioChunk,
    DepthLevel,
    EmotionalTone,
    NarrationContext,
    NarrationResult,
    NarrationScript,
    NarrationSegment,
    VoiceParameters,
)

logger = logging.getLogger(__name__)

# ── Model identifiers ────────────────────────────────────────
LIVE_AUDIO_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"
SCRIPT_MODEL = "gemini-3-flash-preview"

# ── Defaults ──────────────────────────────────────────────────
AUDIO_SAMPLE_RATE = 24_000       # Live API output sample rate (Hz)
AUDIO_SAMPLE_WIDTH = 2           # 16-bit PCM → 2 bytes per sample
AUDIO_CHANNELS = 1               # Mono
MAX_SCRIPT_TOKENS = 4096
SCRIPT_GENERATION_TIMEOUT = 10.0  # seconds
SPEECH_SYNTHESIS_TIMEOUT = 30.0   # seconds

# Depth-dial word budgets (approximate)
_DEPTH_WORD_BUDGET: dict[DepthLevel, int] = {
    DepthLevel.EXPLORER: 120,    # ~1 minute narration
    DepthLevel.SCHOLAR: 250,     # ~2 minutes
    DepthLevel.EXPERT: 500,      # ~4 minutes
}


class NarrationEngine:
    """Generates narration scripts and synthesises speech.

    Parameters
    ----------
    client:
        A ``google.genai.Client`` instance (or compatible mock).
    affective_narrator:
        Optional AffectiveNarrator; one is created if not provided.
    default_language:
        ISO 639-1 language code for narration output.
    """

    def __init__(
        self,
        client: Any,
        affective_narrator: Optional[AffectiveNarrator] = None,
        default_language: str = "en",
    ) -> None:
        self._client = client
        self._affective = affective_narrator or AffectiveNarrator()
        self._default_language = default_language

    # ── Script generation ─────────────────────────────────────

    async def generate_script(
        self,
        context: NarrationContext,
        depth_level: Optional[DepthLevel] = None,
    ) -> NarrationScript:
        """Generate a narration script from documentary context.

        Uses Gemini text model for fast structured output.  The script is
        split into segments, each tagged with the emotional tone determined
        by the AffectiveNarrator.

        Returns a NarrationScript with segments and estimated durations.
        """
        depth = depth_level or context.depth_level or DepthLevel.EXPLORER
        tone = self._affective.determine_emotional_tone(context)
        word_budget = _DEPTH_WORD_BUDGET[depth]
        language = context.language or self._default_language

        prompt = self._build_script_prompt(context, depth, tone, word_budget, language)

        try:
            response = await asyncio.wait_for(
                self._generate_text(prompt),
                timeout=SCRIPT_GENERATION_TIMEOUT,
            )
            segments = self._parse_script_response(response, tone)
        except asyncio.TimeoutError:
            logger.warning("Script generation timed out after %.1fs", SCRIPT_GENERATION_TIMEOUT)
            segments = [self._fallback_segment(context, tone)]
        except Exception:
            logger.exception("Script generation failed")
            segments = [self._fallback_segment(context, tone)]

        total_dur = sum(s.duration for s in segments)
        return NarrationScript(
            segments=segments,
            total_duration=total_dur,
            language=language,
            depth_level=depth,
            tone=tone,
        )

    # ── Speech synthesis (streaming) ──────────────────────────

    async def synthesize_speech(
        self,
        script: NarrationScript,
        language: Optional[str] = None,
        tone: Optional[EmotionalTone] = None,
    ) -> AsyncIterator[AudioChunk]:
        """Stream audio chunks for a narration script via Gemini Live API.

        Yields AudioChunk objects containing raw 24 kHz / 16-bit / mono
        PCM data.  The caller (WebSocket gateway) should forward each
        chunk to the client immediately for real-time playback.

        The method opens a single Live API session and sends the full
        script text with tone instructions as the system prompt.
        """
        effective_tone = tone or script.tone
        voice_params = self._affective.adapt_tone(effective_tone)
        tone_instruction = self._affective.get_tone_instruction(effective_tone)
        lang = language or script.language or self._default_language

        full_text = "\n\n".join(seg.text for seg in script.segments)
        system_instruction = self._build_speech_system_instruction(
            tone_instruction, lang,
        )

        config = self._build_live_config(voice_params, system_instruction)

        sequence = 0
        try:
            async with self._client.aio.live.connect(
                model=LIVE_AUDIO_MODEL,
                config=config,
            ) as session:
                # Send the narration text
                await self._send_text_to_session(session, full_text)

                # Stream audio chunks back
                async for message in session.receive():
                    chunks = self._extract_audio_chunks(message, sequence)
                    for chunk in chunks:
                        yield chunk
                        sequence = chunk.sequence + 1

        except asyncio.TimeoutError:
            logger.warning("Speech synthesis timed out")
        except Exception:
            logger.exception("Speech synthesis failed")

    async def synthesize_speech_collected(
        self,
        script: NarrationScript,
        language: Optional[str] = None,
        tone: Optional[EmotionalTone] = None,
    ) -> NarrationResult:
        """Generate speech and collect all chunks into a NarrationResult.

        Unlike synthesize_speech (streaming), this method waits for the
        full audio and assembles a NarrationResult with transcript and
        chunk count.  Useful for non-streaming callers.
        """
        effective_tone = tone or script.tone
        transcript = "\n\n".join(seg.text for seg in script.segments)
        chunk_count = 0
        total_bytes = 0

        async for chunk in self.synthesize_speech(script, language, tone):
            chunk_count += 1
            total_bytes += len(chunk.data)

        # Estimate duration from PCM byte count
        duration = total_bytes / (AUDIO_SAMPLE_RATE * AUDIO_SAMPLE_WIDTH * AUDIO_CHANNELS)

        return NarrationResult(
            script=script,
            transcript=transcript,
            duration=duration,
            language=script.language,
            tone=effective_tone,
            depth_level=script.depth_level,
            chunk_count=chunk_count,
        )

    # ── Translation ───────────────────────────────────────────

    async def translate_script(
        self,
        script: NarrationScript,
        target_language: str,
    ) -> NarrationScript:
        """Translate a narration script to the target language.

        Uses Gemini text model to translate each segment while preserving
        factual accuracy and tone markers.
        """
        if script.language == target_language:
            return script

        prompt = self._build_translation_prompt(script, target_language)

        try:
            response = await asyncio.wait_for(
                self._generate_text(prompt),
                timeout=SCRIPT_GENERATION_TIMEOUT,
            )
            translated_segments = self._parse_translation_response(
                response, script.segments, script.tone,
            )
        except Exception:
            logger.exception("Translation failed, returning original script")
            return script

        total_dur = sum(s.duration for s in translated_segments)
        return NarrationScript(
            segments=translated_segments,
            total_duration=total_dur,
            language=target_language,
            depth_level=script.depth_level,
            tone=script.tone,
        )

    # ── Convenience: full pipeline ────────────────────────────

    async def generate_narration(
        self,
        context: NarrationContext,
    ) -> AsyncIterator[AudioChunk]:
        """End-to-end: context → script → streaming audio chunks.

        This is the primary entry point used by the Orchestrator.
        """
        script = await self.generate_script(context)
        async for chunk in self.synthesize_speech(script):
            yield chunk

    # ── Private helpers ───────────────────────────────────────

    def _build_script_prompt(
        self,
        context: NarrationContext,
        depth: DepthLevel,
        tone: EmotionalTone,
        word_budget: int,
        language: str,
    ) -> str:
        """Build a structured prompt for Gemini script generation."""
        location_block = ""
        if context.place_name:
            location_block = (
                f"\n## Location\n"
                f"Name: {context.place_name}\n"
                f"Description: {context.place_description or 'N/A'}\n"
                f"Types: {', '.join(context.place_types) if context.place_types else 'N/A'}\n"
                f"Visual: {context.visual_description or 'N/A'}\n"
            )

        topic_block = ""
        if context.topic:
            topic_block = f"\n## Topic\n{context.topic}\n"

        prev_block = ""
        if context.previous_topics:
            prev_block = (
                f"\n## Previously Covered\n"
                f"{', '.join(context.previous_topics)}\n"
                f"(Avoid repeating these.)\n"
            )

        return (
            f"You are a world-class documentary narrator. "
            f"Generate a narration script for a mobile documentary experience.\n"
            f"\n## Parameters\n"
            f"- Depth level: {depth.value} "
            f"({'brief overview' if depth == DepthLevel.EXPLORER else 'detailed analysis' if depth == DepthLevel.SCHOLAR else 'comprehensive deep-dive'})\n"
            f"- Emotional tone: {tone.value}\n"
            f"- Target word count: ~{word_budget} words\n"
            f"- Language: {language}\n"
            f"{location_block}{topic_block}{prev_block}"
            f"\n## Output format\n"
            f"Return a JSON array of narration segments. Each segment is an object "
            f"with a \"text\" field (the narration paragraph) and an estimated "
            f"\"duration\" field (seconds, assuming ~150 words per minute). "
            f"Example:\n"
            f'[{{"text": "Welcome to...", "duration": 12.5}}]\n'
            f"\nReturn ONLY the JSON array, no markdown fences or extra text."
        )

    async def _generate_text(self, prompt: str) -> str:
        """Call Gemini text model and return the response text."""
        response = await self._client.aio.models.generate_content(
            model=SCRIPT_MODEL,
            contents=prompt,
        )
        return response.text or ""

    def _parse_script_response(
        self, response: str, tone: EmotionalTone,
    ) -> list[NarrationSegment]:
        """Parse the JSON array returned by the script generation model."""
        # Strip markdown fences if present
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            text = text.strip()

        try:
            raw_segments = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse script JSON, using raw text")
            return [NarrationSegment(
                text=response.strip(),
                duration=max(1.0, len(response.split()) / 2.5),
                tone=tone,
            )]

        if not isinstance(raw_segments, list):
            raw_segments = [raw_segments]

        segments: list[NarrationSegment] = []
        for raw in raw_segments:
            seg_text = raw.get("text", "") if isinstance(raw, dict) else str(raw)
            seg_dur = float(raw.get("duration", 0)) if isinstance(raw, dict) else 0.0
            if not seg_text:
                continue
            # Estimate duration if not provided
            if seg_dur <= 0:
                seg_dur = max(1.0, len(seg_text.split()) / 2.5)
            segments.append(NarrationSegment(text=seg_text, duration=seg_dur, tone=tone))

        return segments or [NarrationSegment(text="...", duration=1.0, tone=tone)]

    def _fallback_segment(
        self, context: NarrationContext, tone: EmotionalTone,
    ) -> NarrationSegment:
        """Create a graceful fallback segment when script generation fails."""
        subject = context.place_name or context.topic or "this location"
        return NarrationSegment(
            text=f"Welcome. Let me tell you about {subject}.",
            duration=4.0,
            tone=tone,
        )

    def _build_speech_system_instruction(
        self, tone_instruction: str, language: str,
    ) -> str:
        """Build the system instruction for the Live API session."""
        return (
            f"You are a documentary narrator. Read the following narration "
            f"script aloud exactly as written. Do not add commentary or "
            f"deviate from the text.\n\n"
            f"Style: {tone_instruction}\n"
            f"Language: {language}\n"
        )

    def _build_live_config(
        self,
        voice_params: VoiceParameters,
        system_instruction: str,
    ) -> Any:
        """Build the configuration for client.aio.live.connect()."""
        from google.genai import types

        return types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=types.Content(
                parts=[types.Part(text=system_instruction)]
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_params.voice_name,
                    )
                )
            ),
        )

    async def _send_text_to_session(self, session: Any, text: str) -> None:
        """Send narration text to a Live API session.

        Uses send_realtime_input with text — the correct method for sending
        new user input (audio, video, or text) in real time.
        send_client_content is reserved for injecting conversation history only.
        """
        from google.genai import types

        await session.send_realtime_input(text=text)

    def _extract_audio_chunks(
        self, message: Any, start_sequence: int,
    ) -> list[AudioChunk]:
        """Extract audio data from a Live API response message."""
        chunks: list[AudioChunk] = []
        seq = start_sequence

        # Check for model turn with audio parts
        server_content = getattr(message, "server_content", None)
        if not server_content:
            return chunks

        model_turn = getattr(server_content, "model_turn", None)
        if not model_turn:
            # Check for turn_complete signal
            turn_complete = getattr(server_content, "turn_complete", False)
            if turn_complete and seq > 0:
                chunks.append(AudioChunk(data=b"", sequence=seq, is_final=True))
            return chunks

        parts = getattr(model_turn, "parts", []) or []
        for part in parts:
            inline_data = getattr(part, "inline_data", None)
            if inline_data and getattr(inline_data, "data", None):
                raw = inline_data.data
                # Decode base64 if the data is a string
                if isinstance(raw, str):
                    audio_bytes = base64.b64decode(raw)
                else:
                    audio_bytes = bytes(raw)

                if audio_bytes:
                    chunks.append(AudioChunk(data=audio_bytes, sequence=seq))
                    seq += 1

        return chunks

    # ── Translation helpers ───────────────────────────────────

    def _build_translation_prompt(
        self, script: NarrationScript, target_language: str,
    ) -> str:
        """Build a prompt to translate narration segments."""
        segments_json = json.dumps(
            [{"text": s.text, "duration": s.duration} for s in script.segments],
            ensure_ascii=False,
        )
        return (
            f"Translate the following documentary narration segments from "
            f"{script.language} to {target_language}. Preserve factual "
            f"accuracy, tone, and the approximate word count of each segment.\n\n"
            f"Input segments (JSON array):\n{segments_json}\n\n"
            f"Return a JSON array with the same structure, but with "
            f"translated \"text\" fields and adjusted \"duration\" estimates. "
            f"Return ONLY the JSON array."
        )

    def _parse_translation_response(
        self,
        response: str,
        original_segments: list[NarrationSegment],
        tone: EmotionalTone,
    ) -> list[NarrationSegment]:
        """Parse translated segments from model response."""
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            text = text.strip()

        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse translation JSON")
            return list(original_segments)

        if not isinstance(raw, list):
            return list(original_segments)

        translated: list[NarrationSegment] = []
        for i, item in enumerate(raw):
            seg_text = item.get("text", "") if isinstance(item, dict) else str(item)
            seg_dur = float(item.get("duration", 0)) if isinstance(item, dict) else 0.0
            if not seg_text:
                continue
            if seg_dur <= 0 and i < len(original_segments):
                seg_dur = original_segments[i].duration
            translated.append(NarrationSegment(text=seg_text, duration=seg_dur, tone=tone))

        return translated or list(original_segments)
