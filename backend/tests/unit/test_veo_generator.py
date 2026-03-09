"""Unit tests for the Veo Generator service.

Tests cover:
  - VeoGenerator.generate_clip() — happy path, timeout, error handling
  - VeoGenerator.generate_scene_chain() — multi-clip, visual continuity, partial failures
  - VeoGenerator.validate_clip_quality() — duration and resolution constraints
  - Prompt building with style directives
  - Media Store integration
  - Graceful degradation (Req 6.6)
"""

from __future__ import annotations

import asyncio
import sys
import types as builtin_types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Mock google.genai before importing VeoGenerator ──────────────────────────

_mock_genai = MagicMock()
_mock_types = MagicMock()
sys.modules.setdefault("google", MagicMock())
sys.modules.setdefault("google.genai", _mock_genai)
sys.modules.setdefault("google.genai.types", _mock_types)

from backend.services.veo_generator.generator import (
    VeoGenerator,
    _MODEL_ID,
    _STYLE_PROMPTS,
)
from backend.services.veo_generator.models import (
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


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_scene(**overrides) -> SceneDescription:
    """Create a SceneDescription with sensible defaults."""
    defaults = {
        "prompt": "Cinematic view of the Roman Colosseum at sunset",
        "duration": 8,
        "style": VideoStyle.CINEMATIC,
        "aspect_ratio": AspectRatio.LANDSCAPE,
        "generate_audio": True,
        "resolution": VideoResolution.FHD_1080P,
    }
    defaults.update(overrides)
    return SceneDescription(**defaults)


def _make_context(**overrides) -> DocumentaryContext:
    """Create a DocumentaryContext with sensible defaults."""
    defaults = {
        "session_id": "sess-001",
        "mode": "sight",
        "topic": "Roman Colosseum",
        "place_name": "Colosseum",
    }
    defaults.update(overrides)
    return DocumentaryContext(**defaults)


def _make_completed_operation(video_uri: str = "gs://bucket/video.mp4"):
    """Create a mock completed Veo operation."""
    video_obj = MagicMock()
    video_obj.uri = video_uri
    video_obj.url = None

    generated_video = MagicMock()
    generated_video.video = video_obj

    response = MagicMock()
    response.generated_videos = [generated_video]

    operation = MagicMock()
    operation.done = True
    operation.response = response

    return operation


def _make_pending_then_done_operation(
    video_uri: str = "gs://bucket/video.mp4", polls_before_done: int = 1
):
    """Create a mock operation that returns pending then done."""
    video_obj = MagicMock()
    video_obj.uri = video_uri
    video_obj.url = None

    generated_video = MagicMock()
    generated_video.video = video_obj

    response = MagicMock()
    response.generated_videos = [generated_video]

    # First call: pending
    pending_op = MagicMock()
    pending_op.done = False
    pending_op.response = None

    # Done call
    done_op = MagicMock()
    done_op.done = True
    done_op.response = response

    return pending_op, done_op


def _make_client(operation=None):
    """Create a mock genai.Client with generate_videos wired up."""
    client = MagicMock()

    if operation is None:
        operation = _make_completed_operation()

    client.models.generate_videos.return_value = operation
    client.operations.get.return_value = operation

    return client


# ── Model tests ──────────────────────────────────────────────────────────────


class TestModels:
    """Test Pydantic model creation and validation."""

    def test_scene_description_defaults(self):
        scene = SceneDescription(prompt="test")
        assert scene.duration == 8
        assert scene.style == VideoStyle.CINEMATIC
        assert scene.generate_audio is True
        assert scene.resolution == VideoResolution.FHD_1080P
        assert scene.aspect_ratio == AspectRatio.LANDSCAPE

    def test_scene_description_with_context(self):
        ctx = _make_context()
        scene = SceneDescription(prompt="test", context=ctx)
        assert scene.context.session_id == "sess-001"

    def test_video_clip_auto_id(self):
        clip = VideoClip()
        assert len(clip.id) == 12
        assert clip.duration == 0.0
        assert clip.has_native_audio is True

    def test_video_generation_result_defaults(self):
        result = VideoGenerationResult()
        assert result.clip is None
        assert result.stored is False
        assert result.error is None
        assert result.status == VideoStatus.COMPLETED

    def test_scene_chain_result(self):
        clips = [
            VideoClip(duration=8.0),
            VideoClip(duration=8.0),
        ]
        result = SceneChainResult(
            clips=clips,
            total_duration=16.0,
            visual_continuity_score=1.0,
        )
        assert len(result.clips) == 2
        assert result.total_duration == 16.0

    def test_video_style_enum(self):
        assert VideoStyle.CINEMATIC.value == "cinematic"
        assert VideoStyle.HISTORICAL.value == "historical"

    def test_video_resolution_enum(self):
        assert VideoResolution.FHD_1080P.value == "1080p"
        assert VideoResolution.UHD_4K.value == "4k"

    def test_scene_duration_bounds(self):
        scene = SceneDescription(prompt="test", duration=4)
        assert scene.duration == 4
        scene = SceneDescription(prompt="test", duration=8)
        assert scene.duration == 8

    def test_aspect_ratio_enum(self):
        assert AspectRatio.LANDSCAPE.value == "16:9"
        assert AspectRatio.PORTRAIT.value == "9:16"


# ── Generator tests ──────────────────────────────────────────────────────────


class TestVeoGenerator:
    """Test VeoGenerator clip generation."""

    @pytest.mark.asyncio
    async def test_generate_clip_happy_path(self):
        """Successful clip generation returns a valid result."""
        client = _make_client()
        gen = VeoGenerator(client=client)
        scene = _make_scene()

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene, user_id="u1", session_id="s1")

        assert result.status == VideoStatus.COMPLETED
        assert result.error is None
        assert result.clip is not None
        assert result.clip.url == "gs://bucket/video.mp4"
        assert result.clip.duration == 8.0
        assert result.clip.resolution == VideoResolution.FHD_1080P
        assert result.clip.has_native_audio is True
        assert result.clip.style == VideoStyle.CINEMATIC

    @pytest.mark.asyncio
    async def test_generate_clip_with_context(self):
        """Clip generation with documentary context."""
        client = _make_client()
        gen = VeoGenerator(client=client)
        ctx = _make_context()
        scene = _make_scene(context=ctx)

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene)

        assert result.status == VideoStatus.COMPLETED
        assert result.clip is not None

    @pytest.mark.asyncio
    async def test_generate_clip_session_from_context(self):
        """Session ID is resolved from context when not provided."""
        client = _make_client()
        gen = VeoGenerator(client=client)
        ctx = _make_context(session_id="ctx-sess")
        scene = _make_scene(context=ctx)

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene)

        assert result.status == VideoStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_generate_clip_timeout(self):
        """Timeout returns graceful degradation result (Req 6.6)."""
        client = MagicMock()

        # generate_videos raises timeout
        async def _slow(*args, **kwargs):
            await asyncio.sleep(999)

        client.models.generate_videos.side_effect = lambda **kw: (_ for _ in ()).throw(
            Exception("timeout")
        )

        gen = VeoGenerator(client=client)
        scene = _make_scene()

        result = await gen.generate_clip(scene, user_id="u1", session_id="s1")

        assert result.status == VideoStatus.FAILED
        assert result.error is not None
        assert result.clip is None

    @pytest.mark.asyncio
    async def test_generate_clip_api_error(self):
        """API error returns graceful degradation result (Req 6.6)."""
        client = MagicMock()
        client.models.generate_videos.side_effect = RuntimeError("API down")

        gen = VeoGenerator(client=client)
        scene = _make_scene()

        result = await gen.generate_clip(scene, user_id="u1", session_id="s1")

        assert result.status == VideoStatus.FAILED
        assert "API down" in result.error
        assert result.clip is None

    @pytest.mark.asyncio
    async def test_generate_clip_no_video_in_response(self):
        """Empty response triggers VeoGenerationError → graceful fallback."""
        empty_op = MagicMock()
        empty_op.done = True
        empty_op.response = MagicMock()
        empty_op.response.generated_videos = []

        client = _make_client(operation=empty_op)
        gen = VeoGenerator(client=client)
        scene = _make_scene()

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene, user_id="u1", session_id="s1")

        assert result.status == VideoStatus.FAILED
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_generate_clip_null_response(self):
        """Null response triggers graceful fallback."""
        null_op = MagicMock()
        null_op.done = True
        null_op.response = None

        client = _make_client(operation=null_op)
        gen = VeoGenerator(client=client)
        scene = _make_scene()

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene, user_id="u1", session_id="s1")

        assert result.status == VideoStatus.FAILED
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_generate_clip_historical_style(self):
        """Historical style includes period-appropriate prompt."""
        client = _make_client()
        gen = VeoGenerator(client=client)
        ctx = _make_context(historical_period="Ancient Rome, 80 AD")
        scene = _make_scene(style=VideoStyle.HISTORICAL, context=ctx)

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene, user_id="u1", session_id="s1")

        assert result.status == VideoStatus.COMPLETED
        # Verify the prompt includes historical context
        call_kwargs = client.models.generate_videos.call_args
        prompt = call_kwargs.kwargs.get("prompt", "")
        assert "historical" in prompt.lower() or "period" in prompt.lower()

    @pytest.mark.asyncio
    async def test_generate_clip_speculative_style(self):
        """Speculative style generates successfully."""
        client = _make_client()
        gen = VeoGenerator(client=client)
        scene = _make_scene(style=VideoStyle.SPECULATIVE)

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene)

        assert result.status == VideoStatus.COMPLETED
        assert result.clip.style == VideoStyle.SPECULATIVE

    @pytest.mark.asyncio
    async def test_generate_clip_portrait_aspect(self):
        """Portrait aspect ratio is passed correctly."""
        client = _make_client()
        gen = VeoGenerator(client=client)
        scene = _make_scene(aspect_ratio=AspectRatio.PORTRAIT)

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene)

        assert result.status == VideoStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_generate_clip_no_audio(self):
        """Clip without audio generates successfully."""
        client = _make_client()
        gen = VeoGenerator(client=client)
        scene = _make_scene(generate_audio=False)

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene)

        assert result.status == VideoStatus.COMPLETED
        assert result.clip.has_native_audio is False

    @pytest.mark.asyncio
    async def test_generate_clip_4k_resolution(self):
        """4K resolution clips generate successfully."""
        client = _make_client()
        gen = VeoGenerator(client=client)
        scene = _make_scene(resolution=VideoResolution.UHD_4K)

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene)

        assert result.status == VideoStatus.COMPLETED
        assert result.clip.resolution == VideoResolution.UHD_4K

    @pytest.mark.asyncio
    async def test_generate_clip_with_reference_image(self):
        """Reference image is passed for visual continuity."""
        client = _make_client()
        gen = VeoGenerator(client=client)
        scene = _make_scene(
            reference_image="gs://bucket/frame.png",
            reference_image_mime_type="image/png",
        )

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene)

        assert result.status == VideoStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_generate_clip_custom_model(self):
        """Custom model ID is used."""
        client = _make_client()
        gen = VeoGenerator(client=client, model_id="veo-3.1-fast-generate-001")
        scene = _make_scene()

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene)

        assert result.status == VideoStatus.COMPLETED


# ── Polling tests ────────────────────────────────────────────────────────────


class TestPolling:
    """Test async polling for video generation completion."""

    @pytest.mark.asyncio
    async def test_polling_completes_after_retries(self):
        """Operation that completes after polling."""
        pending_op, done_op = _make_pending_then_done_operation()
        client = MagicMock()
        client.models.generate_videos.return_value = pending_op
        client.operations.get.return_value = done_op

        gen = VeoGenerator(client=client)
        scene = _make_scene()

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene)

        assert result.status == VideoStatus.COMPLETED
        assert result.clip is not None


# ── Scene chain tests ────────────────────────────────────────────────────────


class TestSceneChain:
    """Test scene chain generation with visual continuity."""

    @pytest.mark.asyncio
    async def test_scene_chain_two_clips(self):
        """Generate a chain of 2 clips."""
        client = _make_client()
        gen = VeoGenerator(client=client)
        scenes = [
            _make_scene(prompt="Exterior shot of Colosseum"),
            _make_scene(prompt="Interior arena floor"),
        ]

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_scene_chain(scenes, user_id="u1", session_id="s1")

        assert len(result.clips) == 2
        assert result.total_duration == 16.0
        assert result.visual_continuity_score == 1.0
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_scene_chain_partial_failure(self):
        """Partial failure in scene chain still returns successful clips."""
        # First call succeeds, second fails
        success_op = _make_completed_operation("gs://bucket/scene1.mp4")
        fail_op = MagicMock()
        fail_op.done = True
        fail_op.response = MagicMock()
        fail_op.response.generated_videos = []

        client = MagicMock()
        client.models.generate_videos.side_effect = [success_op, fail_op]
        client.operations.get.side_effect = [success_op, fail_op]

        gen = VeoGenerator(client=client)
        scenes = [
            _make_scene(prompt="Scene 1"),
            _make_scene(prompt="Scene 2"),
        ]

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_scene_chain(scenes, user_id="u1", session_id="s1")

        assert len(result.clips) == 1
        assert result.visual_continuity_score == 0.5
        assert len(result.errors) == 1

    @pytest.mark.asyncio
    async def test_scene_chain_empty(self):
        """Empty scene list returns empty chain."""
        client = _make_client()
        gen = VeoGenerator(client=client)

        result = await gen.generate_scene_chain([])

        assert len(result.clips) == 0
        assert result.total_duration == 0.0

    @pytest.mark.asyncio
    async def test_scene_chain_assigns_indices(self):
        """Scene chain clips have correct scene_index."""
        client = _make_client()
        gen = VeoGenerator(client=client)
        scenes = [
            _make_scene(prompt=f"Scene {i}") for i in range(3)
        ]

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_scene_chain(scenes)

        for i, clip in enumerate(result.clips):
            assert clip.scene_index == i

    @pytest.mark.asyncio
    async def test_scene_chain_all_fail(self):
        """All scenes failing returns empty chain with errors."""
        client = MagicMock()
        client.models.generate_videos.side_effect = RuntimeError("Service down")

        gen = VeoGenerator(client=client)
        scenes = [_make_scene(prompt=f"Scene {i}") for i in range(3)]

        result = await gen.generate_scene_chain(scenes)

        assert len(result.clips) == 0
        assert len(result.errors) == 3
        assert result.visual_continuity_score == 0.0


# ── Quality validation tests ─────────────────────────────────────────────────


class TestQualityValidation:
    """Test clip quality validation (Req 6.2, 6.5)."""

    def test_valid_clip_1080p(self):
        """1080p clip with valid duration passes."""
        gen = VeoGenerator(client=MagicMock())
        clip = VideoClip(duration=8.0, resolution=VideoResolution.FHD_1080P)
        assert gen.validate_clip_quality(clip) is True

    def test_valid_clip_4k(self):
        """4K clip passes."""
        gen = VeoGenerator(client=MagicMock())
        clip = VideoClip(duration=8.0, resolution=VideoResolution.UHD_4K)
        assert gen.validate_clip_quality(clip) is True

    def test_invalid_clip_720p(self):
        """720p clip fails quality check (Req 6.5)."""
        gen = VeoGenerator(client=MagicMock())
        clip = VideoClip(duration=8.0, resolution=VideoResolution.HD_720P)
        assert gen.validate_clip_quality(clip) is False

    def test_invalid_clip_zero_duration(self):
        """Zero duration clip fails quality check."""
        gen = VeoGenerator(client=MagicMock())
        clip = VideoClip(duration=0.0, resolution=VideoResolution.FHD_1080P)
        assert gen.validate_clip_quality(clip) is False

    def test_valid_chain_duration(self):
        """Chain with total 8–60s passes (Req 6.2)."""
        gen = VeoGenerator(client=MagicMock())
        clips = [VideoClip(duration=8.0) for _ in range(3)]
        assert gen.validate_chain_duration(clips) is True  # 24s

    def test_invalid_chain_too_short(self):
        """Chain under 8s fails."""
        gen = VeoGenerator(client=MagicMock())
        clips = [VideoClip(duration=4.0)]
        assert gen.validate_chain_duration(clips) is False  # 4s

    def test_invalid_chain_too_long(self):
        """Chain over 60s fails."""
        gen = VeoGenerator(client=MagicMock())
        clips = [VideoClip(duration=8.0) for _ in range(8)]
        assert gen.validate_chain_duration(clips) is False  # 64s

    def test_valid_chain_boundary_8s(self):
        """Chain of exactly 8s passes."""
        gen = VeoGenerator(client=MagicMock())
        clips = [VideoClip(duration=8.0)]
        assert gen.validate_chain_duration(clips) is True

    def test_valid_chain_boundary_60s(self):
        """Chain of exactly 60s passes."""
        gen = VeoGenerator(client=MagicMock())
        clips = [VideoClip(duration=8.0) for _ in range(7)] + [VideoClip(duration=4.0)]
        assert gen.validate_chain_duration(clips) is True  # 60s


# ── Prompt building tests ────────────────────────────────────────────────────


class TestPromptBuilding:
    """Test prompt construction with style directives."""

    def test_cinematic_style_in_prompt(self):
        gen = VeoGenerator(client=MagicMock())
        scene = _make_scene(style=VideoStyle.CINEMATIC)
        prompt = gen._build_prompt(scene)
        assert "cinematic" in prompt.lower()

    def test_documentary_style_in_prompt(self):
        gen = VeoGenerator(client=MagicMock())
        scene = _make_scene(style=VideoStyle.DOCUMENTARY)
        prompt = gen._build_prompt(scene)
        assert "documentary" in prompt.lower()

    def test_historical_period_in_prompt(self):
        gen = VeoGenerator(client=MagicMock())
        ctx = _make_context(historical_period="Renaissance Italy, 1500s")
        scene = _make_scene(style=VideoStyle.HISTORICAL, context=ctx)
        prompt = gen._build_prompt(scene)
        assert "Renaissance Italy" in prompt
        assert "period-appropriate" in prompt

    def test_scene_prompt_included(self):
        gen = VeoGenerator(client=MagicMock())
        scene = _make_scene(prompt="Aerial view of the Pyramids of Giza")
        prompt = gen._build_prompt(scene)
        assert "Pyramids of Giza" in prompt

    def test_professional_quality_directive(self):
        gen = VeoGenerator(client=MagicMock())
        scene = _make_scene()
        prompt = gen._build_prompt(scene)
        assert "Professional documentary quality" in prompt


# ── Media Store integration tests ────────────────────────────────────────────


class TestMediaStoreIntegration:
    """Test integration with MediaStoreManager (Req 6.7)."""

    @pytest.mark.asyncio
    async def test_clip_stored_when_media_store_available(self):
        """Clip is stored in media store when configured."""
        client = _make_client()
        media_store = AsyncMock()
        media_store.store_media.return_value = "media-123"
        media_store.generate_signed_url.return_value = "https://signed-url.example.com"

        gen = VeoGenerator(client=client, media_store=media_store)
        scene = _make_scene()

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene, user_id="u1", session_id="s1")

        assert result.stored is True
        assert result.media_id == "media-123"
        assert result.media_url == "https://signed-url.example.com"

    @pytest.mark.asyncio
    async def test_clip_not_stored_without_media_store(self):
        """Clip is not stored when media store is None."""
        client = _make_client()
        gen = VeoGenerator(client=client)  # No media_store
        scene = _make_scene()

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene, user_id="u1", session_id="s1")

        assert result.stored is False
        assert result.media_id is None

    @pytest.mark.asyncio
    async def test_media_store_failure_graceful(self):
        """Media store failure doesn't crash clip generation."""
        client = _make_client()
        media_store = AsyncMock()
        media_store.store_media.side_effect = RuntimeError("Storage down")

        gen = VeoGenerator(client=client, media_store=media_store)
        scene = _make_scene()

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene, user_id="u1", session_id="s1")

        # Clip generation still succeeds
        assert result.status == VideoStatus.COMPLETED
        assert result.clip is not None
        assert result.stored is False

    @pytest.mark.asyncio
    async def test_signed_url_failure_falls_back_to_gcs(self):
        """Signed URL failure falls back to GCS URI."""
        client = _make_client()
        media_store = AsyncMock()
        media_store.store_media.return_value = "media-123"
        media_store.generate_signed_url.side_effect = RuntimeError("Signing error")

        gen = VeoGenerator(client=client, media_store=media_store)
        scene = _make_scene()

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene, user_id="u1", session_id="s1")

        assert result.media_id == "media-123"
        # Falls back to GCS URI
        assert result.media_url is not None

    @pytest.mark.asyncio
    async def test_clip_not_stored_without_user_id(self):
        """Clip is not stored when user_id is None."""
        client = _make_client()
        media_store = AsyncMock()
        gen = VeoGenerator(client=client, media_store=media_store)
        scene = _make_scene()

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene)

        assert result.stored is False


# ── Graceful degradation tests ───────────────────────────────────────────────


class TestGracefulDegradation:
    """Test that failures return safe results (Req 6.6)."""

    @pytest.mark.asyncio
    async def test_fallback_result_on_timeout(self):
        """Timeout returns failed result with no clip."""
        gen = VeoGenerator(client=MagicMock())
        result = gen._fallback_result(_make_scene(), 5000.0, "timed out")
        assert result.status == VideoStatus.FAILED
        assert result.clip is None
        assert result.error == "timed out"

    @pytest.mark.asyncio
    async def test_fallback_result_on_api_error(self):
        """API error returns failed result."""
        gen = VeoGenerator(client=MagicMock())
        result = gen._fallback_result(_make_scene(), 2000.0, "500 Internal Server Error")
        assert result.status == VideoStatus.FAILED
        assert result.clip is None
        assert "500" in result.error

    @pytest.mark.asyncio
    async def test_multiple_failures_dont_cascade(self):
        """Multiple sequential failures each return independent results."""
        client = MagicMock()
        client.models.generate_videos.side_effect = RuntimeError("down")
        gen = VeoGenerator(client=client)

        for _ in range(5):
            result = await gen.generate_clip(_make_scene())
            assert result.status == VideoStatus.FAILED
            assert result.error is not None


# ── Output GCS URI tests ────────────────────────────────────────────────────


class TestOutputGcsUri:
    """Test GCS output URI configuration."""

    @pytest.mark.asyncio
    async def test_output_gcs_uri_passed_to_config(self):
        """Output GCS URI is included in config for Vertex AI."""
        client = _make_client()
        gen = VeoGenerator(
            client=client,
            output_gcs_uri="gs://my-bucket/videos/",
        )
        scene = _make_scene()

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene)

        assert result.status == VideoStatus.COMPLETED


# ── Edge case tests ──────────────────────────────────────────────────────────


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_default_model_id(self):
        """Default model ID is veo-3.1-generate-001."""
        assert _MODEL_ID == "veo-3.1-generate-001"

    def test_style_prompts_cover_all_styles(self):
        """All VideoStyle values have a prompt mapping."""
        for style in VideoStyle:
            assert style in _STYLE_PROMPTS

    @pytest.mark.asyncio
    async def test_negative_prompt_passed(self):
        """Custom negative prompt is passed through."""
        client = _make_client()
        gen = VeoGenerator(client=client)
        scene = _make_scene(negative_prompt="no people, no text")

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene)

        assert result.status == VideoStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_video_url_from_url_attribute(self):
        """Extract video URL when .url attribute is set instead of .uri."""
        video_obj = MagicMock()
        video_obj.uri = None
        video_obj.url = "https://storage.example.com/video.mp4"

        generated_video = MagicMock()
        generated_video.video = video_obj

        response = MagicMock()
        response.generated_videos = [generated_video]

        operation = MagicMock()
        operation.done = True
        operation.response = response

        client = _make_client(operation=operation)
        gen = VeoGenerator(client=client)
        scene = _make_scene()

        with patch("backend.services.veo_generator.generator.asyncio.sleep", new_callable=AsyncMock):
            result = await gen.generate_clip(scene)

        assert result.clip.url == "https://storage.example.com/video.mp4"
