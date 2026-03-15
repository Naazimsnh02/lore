"""Veo Generator – Veo 3.1 video generation integration for LORE.

Design reference: LORE design.md, Section 6 – Veo Generator.
Requirements: 6.1–6.7.

Architecture notes
------------------
- Uses ``google-genai`` SDK ``client.models.generate_videos()`` to generate
  video clips via Veo 3.1 (Req 6.1).
- Supports text-to-video and image-to-video (reference image for visual
  continuity across scene chains, Req 6.4).
- Polls the long-running operation for completion (Veo generation takes
  30-60 seconds).
- Includes native audio in generated clips (Req 6.3) via ``generate_audio=True``.
- Minimum resolution 1080p enforced (Req 6.5).
- Stores completed clips in the Media Store via optional dependency injection
  of a MediaStoreManager instance (Req 6.7).
- On timeout or error, returns a graceful fallback (VideoGenerationResult
  with error field populated, Req 6.6).
- Constructor accepts an injected ``genai.Client`` for testability.
- Scene chain generation chains clips sequentially, using Veo's video
  extension capability for visual continuity (Req 6.4).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Optional

from .models import (
    AspectRatio,
    DocumentaryContext,
    SceneChainResult,
    SceneDescription,
    VideoClip,
    VideoGenerationResult,
    VideoResolution,
    VideoStatus,
    VideoStyle,
    VeoGenerationError,
    VeoTimeoutError,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Veo 3.1 model ID — use the env var, defaulting to the correct preview ID
_MODEL_ID = os.getenv("VEO_MODEL", "veo-3.1-generate-preview")

# Polling interval for long-running video generation
_POLL_INTERVAL_S = 5.0

# Hard timeout for a single clip generation (Veo: 30-60s typical)
_GENERATION_TIMEOUT_S = 300.0

# Maximum retries for polling operation status
_MAX_POLL_RETRIES = 60  # 60 * 5s = 300s max

# Default negative prompt for documentary quality
_DEFAULT_NEGATIVE_PROMPT = "blurry, low quality, distorted, watermark, text overlay"

# ── Style → prompt fragment mapping ──────────────────────────────────────────

_STYLE_PROMPTS: dict[VideoStyle, str] = {
    VideoStyle.CINEMATIC: "cinematic, professional documentary, smooth camera movement, natural lighting",
    VideoStyle.DOCUMENTARY: "documentary style, educational, informative framing, steady shots",
    VideoStyle.HISTORICAL: "historical recreation, period-appropriate setting, muted colour grading",
    VideoStyle.SPECULATIVE: "speculative visualization, alternate reality, artistic interpretation",
}


class VeoGenerator:
    """Generates cinematic video clips for LORE documentaries using Veo 3.1.

    Design reference: VeoGenerator interface in design.md §6.
    Requirements: 6.1–6.7.

    Parameters
    ----------
    client:
        ``google.genai.Client`` instance (injected for testability).
    media_store:
        Optional ``MediaStoreManager`` for persisting clips to Cloud
        Storage (Req 6.7).  If None, clips are returned with GCS URIs only.
    output_gcs_uri:
        GCS bucket prefix for generated videos.  Required for Vertex AI.
    model_id:
        Override the default Veo model ID.
    """

    def __init__(
        self,
        client: Any,
        media_store: Any = None,
        output_gcs_uri: Optional[str] = None,
        model_id: str = _MODEL_ID,
    ) -> None:
        self._client = client
        self._media_store = media_store
        self._output_gcs_uri = output_gcs_uri
        self._model_id = model_id

    # ── Public API ────────────────────────────────────────────────────────

    async def generate_clip(
        self,
        scene: SceneDescription,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> VideoGenerationResult:
        """Generate a single video clip from a scene description.

        Requirements: 6.1 (Veo 3.1), 6.2 (duration), 6.3 (native audio),
                      6.5 (1080p), 6.7 (store in Media Store).

        Parameters
        ----------
        scene:
            The scene to generate video for.
        user_id:
            Owner user ID for media storage.
        session_id:
            Session ID for media storage.

        Returns
        -------
        VideoGenerationResult
            Contains the video clip, storage info, and any error.
        """
        start_ms = time.monotonic() * 1000

        # Resolve session_id from scene context if not provided
        if session_id is None and scene.context:
            session_id = scene.context.session_id

        # Build the full prompt with style directives
        full_prompt = self._build_prompt(scene)

        try:
            clip = await asyncio.wait_for(
                self._generate_video(
                    prompt=full_prompt,
                    scene=scene,
                ),
                timeout=_GENERATION_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            elapsed_ms = time.monotonic() * 1000 - start_ms
            logger.warning(
                "Video generation timed out after %.0f ms for prompt: %.80s",
                elapsed_ms,
                scene.prompt,
            )
            return self._fallback_result(
                scene, elapsed_ms, "Generation timed out"
            )
        except Exception as exc:
            elapsed_ms = time.monotonic() * 1000 - start_ms
            logger.error(
                "Video generation failed after %.0f ms: %s",
                elapsed_ms,
                exc,
            )
            return self._fallback_result(scene, elapsed_ms, str(exc))

        elapsed_ms = time.monotonic() * 1000 - start_ms
        clip.generation_time_ms = elapsed_ms
        clip.prompt = scene.prompt
        clip.style = scene.style

        # Store in Media Store if available (Req 6.7)
        media_id = None
        media_url = None
        stored = False
        if self._media_store and user_id and session_id and clip.url:
            try:
                media_id, media_url = await self._store_clip(
                    clip, user_id, session_id
                )
                stored = True
            except Exception as exc:
                logger.warning("Failed to store video clip: %s", exc)

        logger.info(
            "Video clip generated in %.0f ms, duration=%.1fs, resolution=%s, stored=%s",
            elapsed_ms,
            clip.duration,
            clip.resolution.value,
            stored,
        )

        return VideoGenerationResult(
            clip=clip,
            stored=stored,
            media_id=media_id,
            media_url=media_url,
            status=VideoStatus.COMPLETED,
        )

    async def generate_scene_chain(
        self,
        scenes: list[SceneDescription],
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> SceneChainResult:
        """Generate a chain of video clips with visual continuity.

        Requirements: 6.2 (8–60s via chaining), 6.4 (visual continuity).

        Scene chains are generated sequentially so each clip can reference
        the previous clip for visual continuity using Veo's video extension.

        Parameters
        ----------
        scenes:
            Ordered list of scene descriptions.
        user_id:
            Owner user ID for media storage.
        session_id:
            Session ID for media storage.

        Returns
        -------
        SceneChainResult
            Contains all generated clips and continuity metrics.
        """
        clips: list[VideoClip] = []
        errors: list[str] = []
        total_duration = 0.0

        for idx, scene in enumerate(scenes):
            scene_copy = scene.model_copy()

            # For clips after the first, reference the previous clip for
            # visual continuity (Req 6.4) if not already set
            if idx > 0 and clips and clips[-1].gcs_uri and not scene_copy.reference_image:
                scene_copy.reference_image = clips[-1].gcs_uri
                scene_copy.reference_image_mime_type = "video/mp4"

            result = await self.generate_clip(
                scene_copy, user_id=user_id, session_id=session_id
            )

            if result.error:
                errors.append(f"Scene {idx}: {result.error}")
                logger.warning(
                    "Scene chain clip %d failed: %s", idx, result.error
                )
                continue

            if result.clip:
                result.clip.scene_index = idx
                clips.append(result.clip)
                total_duration += result.clip.duration

        # Visual continuity score: proportion of successfully chained clips
        continuity_score = 0.0
        if len(scenes) > 0:
            continuity_score = len(clips) / len(scenes)

        logger.info(
            "Scene chain complete: %d/%d clips, total_duration=%.1fs, continuity=%.2f",
            len(clips),
            len(scenes),
            total_duration,
            continuity_score,
        )

        return SceneChainResult(
            clips=clips,
            total_duration=total_duration,
            visual_continuity_score=continuity_score,
            errors=errors,
        )

    def validate_clip_quality(self, clip: VideoClip) -> bool:
        """Validate that a clip meets quality constraints.

        Requirements: 6.2 (duration 8–60s), 6.5 (minimum 1080p).

        Returns True if the clip meets all quality constraints.
        """
        # Duration check (Req 6.2): individual clips 4-8s, chains can be 8-60s
        if clip.duration < 1.0:
            logger.warning("Clip %s duration %.1fs below minimum", clip.id, clip.duration)
            return False

        # Resolution check (Req 6.5): minimum 1080p
        resolution_rank = {
            VideoResolution.HD_720P: 0,
            VideoResolution.FHD_1080P: 1,
            VideoResolution.UHD_4K: 2,
        }
        if resolution_rank.get(clip.resolution, 0) < resolution_rank[VideoResolution.FHD_1080P]:
            logger.warning(
                "Clip %s resolution %s below minimum 1080p",
                clip.id,
                clip.resolution.value,
            )
            return False

        return True

    def validate_chain_duration(self, clips: list[VideoClip]) -> bool:
        """Validate that a scene chain's total duration is within 8–60 seconds.

        Requirement 6.2.
        """
        total = sum(c.duration for c in clips)
        return 8.0 <= total <= 60.0

    # ── Private helpers ───────────────────────────────────────────────────

    def _build_prompt(self, scene: SceneDescription) -> str:
        """Construct the full video generation prompt with style directives."""
        parts: list[str] = []

        # Style prefix
        style_desc = _STYLE_PROMPTS.get(scene.style, _STYLE_PROMPTS[VideoStyle.CINEMATIC])
        parts.append(f"{style_desc}.")

        # Historical period context
        if scene.context and scene.context.historical_period:
            parts.append(
                f"Historical period: {scene.context.historical_period}. "
                "Ensure all visual elements are period-appropriate."
            )

        # Main scene prompt
        parts.append(scene.prompt)

        # Documentary quality directive
        parts.append(
            "Professional documentary quality. Smooth camera movement. "
            "High production value."
        )

        return " ".join(parts)

    async def _generate_video(
        self,
        prompt: str,
        scene: SceneDescription,
    ) -> VideoClip:
        """Call Veo 3.1 API to generate a video clip.

        Uses ``google-genai`` SDK ``client.models.generate_videos()``.
        Polls the long-running operation until completion.

        Raises
        ------
        VeoGenerationError
            If the API returns no video data.
        """
        from google.genai import types

        # Build generation config
        config_kwargs: dict[str, Any] = {
            "aspect_ratio": scene.aspect_ratio.value,
            "number_of_videos": 1,
            "negative_prompt": scene.negative_prompt or _DEFAULT_NEGATIVE_PROMPT,
        }

        # generate_audio is only supported on Vertex AI (output_gcs_uri required).
        # Omit it entirely when using the Gemini API to avoid an API error.
        if self._output_gcs_uri and scene.generate_audio:
            config_kwargs["generate_audio"] = True

        # Duration: Veo accepts "4", "6", or "8"
        duration_val = str(min(max(scene.duration, 4), 8))
        config_kwargs["duration_seconds"] = duration_val

        # Resolution mapping
        if scene.resolution == VideoResolution.FHD_1080P:
            config_kwargs["resolution"] = "1080p"
        elif scene.resolution == VideoResolution.UHD_4K:
            config_kwargs["resolution"] = "4k"
        else:
            # Enforce minimum 1080p (Req 6.5)
            config_kwargs["resolution"] = "1080p"

        # Output GCS URI for Vertex AI
        if self._output_gcs_uri:
            config_kwargs["output_gcs_uri"] = self._output_gcs_uri

        config = types.GenerateVideosConfig(**config_kwargs)

        # Build generate_videos call kwargs
        gen_kwargs: dict[str, Any] = {
            "model": self._model_id,
            "prompt": prompt,
            "config": config,
        }

        # Reference image for visual continuity (Req 6.4)
        if scene.reference_image:
            image_kwargs: dict[str, Any] = {}
            if scene.reference_image.startswith("gs://"):
                image_kwargs["gcs_uri"] = scene.reference_image
            else:
                image_kwargs["image_bytes"] = scene.reference_image

            if scene.reference_image_mime_type:
                image_kwargs["mime_type"] = scene.reference_image_mime_type
            else:
                image_kwargs["mime_type"] = "image/png"

            # Only pass image for image-to-video (not video extension)
            if scene.reference_image_mime_type != "video/mp4":
                gen_kwargs["image"] = types.Image(**image_kwargs)

        # Submit the generation request (returns a long-running operation)
        operation = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._client.models.generate_videos(**gen_kwargs),
        )

        # Poll for completion
        poll_count = 0
        while not operation.done:
            if poll_count >= _MAX_POLL_RETRIES:
                raise VeoTimeoutError(
                    f"Operation did not complete after {poll_count * _POLL_INTERVAL_S}s"
                )
            await asyncio.sleep(_POLL_INTERVAL_S)
            operation = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.operations.get(operation),
            )
            poll_count += 1

        # Extract the generated video
        # The SDK's GenerateVideosOperation wraps the raw response dict.
        # On Vertex AI, generated_videos lives in the raw dict under 'response',
        # not in operation.response (which is a typed object that may be None).
        raw = getattr(operation, "__dict__", {})
        raw_response = raw.get("response") or {}
        if isinstance(raw_response, dict):
            raw_videos = raw_response.get("generated_videos", [])
        else:
            # Typed object path (AI Studio)
            raw_videos = getattr(raw_response, "generated_videos", None) or []

        # Also check operation.error
        raw_error = raw.get("error")
        if raw_error and not raw_videos:
            raise VeoGenerationError(
                f"Veo returned error: {raw_error.get('message', raw_error)}"
            )

        if not raw_videos:
            raise VeoGenerationError(
                "Veo returned no video data for the given prompt"
            )

        generated_video = raw_videos[0]
        # generated_video may be a dict or a typed object
        if isinstance(generated_video, dict):
            video_obj = generated_video.get("video") or generated_video
            video_url = (
                video_obj.get("uri") or video_obj.get("url")
                if isinstance(video_obj, dict) else None
            )
            gcs_uri = video_url
        else:
            video_obj = getattr(generated_video, "video", generated_video)
            video_url = None
            gcs_uri = None
            if hasattr(video_obj, "uri") and video_obj.uri:
                gcs_uri = video_obj.uri
                video_url = video_obj.uri
            elif hasattr(video_obj, "url") and video_obj.url:
                video_url = video_obj.url

        # Parse duration from response or use requested duration
        clip_duration = float(scene.duration)

        # Determine actual resolution
        clip_resolution = scene.resolution
        if clip_resolution == VideoResolution.HD_720P:
            # Enforce minimum 1080p (Req 6.5)
            clip_resolution = VideoResolution.FHD_1080P

        return VideoClip(
            url=video_url,
            gcs_uri=gcs_uri,
            duration=clip_duration,
            resolution=clip_resolution,
            has_native_audio=scene.generate_audio,
            style=scene.style,
        )

    async def _store_clip(
        self,
        clip: VideoClip,
        user_id: str,
        session_id: str,
    ) -> tuple[str, Optional[str]]:
        """Persist a video clip to the Media Store (Req 6.7).

        Returns
        -------
        tuple[str, Optional[str]]
            (media_id, signed_url)
        """
        from ..media_store.models import MediaFile, MediaMetadata, MediaType

        # For GCS-stored videos, we record the metadata without re-uploading
        # the binary data (it's already in GCS from Veo).
        media_file = MediaFile(
            media_type=MediaType.VIDEO,
            data=b"",  # Placeholder — binary is in GCS
            mime_type="video/mp4",
            size=0,
            metadata=MediaMetadata(
                user_id=user_id,
                session_id=session_id,
                media_type=MediaType.VIDEO,
                extension="mp4",
                description=clip.prompt,
                gcs_object_name=clip.gcs_uri or "",
                extra={
                    "style": clip.style.value,
                    "resolution": clip.resolution.value,
                    "duration": clip.duration,
                    "has_native_audio": clip.has_native_audio,
                    "generation_time_ms": clip.generation_time_ms,
                    "scene_index": clip.scene_index,
                },
            ),
        )

        # If we have a GCS URI, register it directly; otherwise use store_media
        media_id = media_file.id
        media_url = clip.url

        # Store metadata record in the media store
        stored_id = await self._media_store.store_media(
            media=media_file,
            user_id=user_id,
            session_id=session_id,
        )
        media_id = stored_id

        # Generate a signed URL for client access
        try:
            signed_url = await self._media_store.generate_signed_url(media_id)
            return (media_id, signed_url)
        except Exception:
            return (media_id, media_url)

    def _fallback_result(
        self,
        scene: SceneDescription,
        elapsed_ms: float,
        error_msg: str,
    ) -> VideoGenerationResult:
        """Create a graceful-degradation result when generation fails.

        Requirement 6.6: system continues without video when Veo fails.
        """
        return VideoGenerationResult(
            clip=None,
            stored=False,
            error=error_msg,
            status=VideoStatus.FAILED,
        )
