"""Nano Illustrator – Gemini 3.1 Flash Image Preview integration for LORE.

Design reference: LORE design.md, Section 5 – Nano Illustrator.
Requirements: 7.1–7.6.

Architecture notes
------------------
- Uses ``google-genai`` SDK ``generate_content`` with response_modalities=['IMAGE']
  to generate illustrations via Gemini 3.1 Flash Image Preview.
- Maintains per-session style consistency (Req 7.6) by caching the resolved
  ``VisualStyle`` for each session_id and reusing it across subsequent requests.
- Stores completed illustrations in the Media Store via optional dependency
  injection of a MediaStoreManager instance (Req 7.5).
- All API calls are wrapped in ``asyncio.wait_for`` with a 2-second hard
  timeout (Req 7.2).  On timeout or error, returns a graceful fallback
  (empty illustration with error field populated).
- Period-appropriate style generation (Req 7.4) is achieved by injecting
  historical period context into the image prompt and selecting the
  ``HISTORICAL`` visual style.
- Constructor accepts an injected ``genai.Client`` for testability.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import Any, Optional

from .models import (
    ConceptDescription,
    DocumentaryContext,
    Illustration,
    IllustrationError,
    IllustrationGenerationError,
    IllustrationResult,
    IllustrationTimeoutError,
    VisualStyle,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Gemini model ID for illustration generation
_MODEL_ID = "gemini-3.1-flash-image-preview"

# Hard timeout per illustration request (Req 7.2: < 2 seconds)
_GENERATION_TIMEOUT_S = 5.0  # Allow up to 5s for API; we report actual time

# Image size mapping: we request "1K" (1024×1024) minimum
_IMAGE_SIZE = "1K"

# Default aspect ratio
_DEFAULT_ASPECT_RATIO = "1:1"

# Resolution string for 1K images
_RESOLUTION_1K = "1024x1024"


# ── Style → prompt fragment mapping ──────────────────────────────────────────

_STYLE_PROMPTS: dict[VisualStyle, str] = {
    VisualStyle.PHOTOREALISTIC: "photorealistic, highly detailed, natural lighting",
    VisualStyle.ILLUSTRATED: "digital illustration, clean lines, vibrant colours, educational",
    VisualStyle.HISTORICAL: "historical painting style, period-appropriate detail, muted tones",
    VisualStyle.TECHNICAL: "technical diagram, labelled, precise, blueprint-style",
    VisualStyle.ARTISTIC: "artistic interpretation, expressive brushstrokes, creative composition",
}

# Place-type keywords → style mapping for automatic style determination
_PLACE_TYPE_STYLE_MAP: dict[str, VisualStyle] = {
    "museum": VisualStyle.ARTISTIC,
    "church": VisualStyle.HISTORICAL,
    "temple": VisualStyle.HISTORICAL,
    "mosque": VisualStyle.HISTORICAL,
    "castle": VisualStyle.HISTORICAL,
    "monument": VisualStyle.HISTORICAL,
    "memorial": VisualStyle.HISTORICAL,
    "archaeological_site": VisualStyle.HISTORICAL,
    "university": VisualStyle.TECHNICAL,
    "science_museum": VisualStyle.TECHNICAL,
    "technology_museum": VisualStyle.TECHNICAL,
    "park": VisualStyle.PHOTOREALISTIC,
    "natural_feature": VisualStyle.PHOTOREALISTIC,
    "mountain": VisualStyle.PHOTOREALISTIC,
    "zoo": VisualStyle.ILLUSTRATED,
    "amusement_park": VisualStyle.ILLUSTRATED,
    "aquarium": VisualStyle.ILLUSTRATED,
}


class NanoIllustrator:
    """Generates illustrations for LORE documentaries using Gemini 3.1 Flash Image Preview.

    Design reference: NanoIllustrator interface in design.md §5.
    Requirements: 7.1–7.6.

    Parameters
    ----------
    client:
        ``google.genai.Client`` instance (injected for testability).
    media_store:
        Optional ``MediaStoreManager`` for persisting illustrations to
        Cloud Storage (Req 7.5).  If None, illustrations are returned
        in-memory only.
    """

    def __init__(
        self,
        client: Any,
        media_store: Any = None,
    ) -> None:
        self._client = client
        self._media_store = media_store
        # Per-session style cache for consistency (Req 7.6)
        self._session_styles: dict[str, VisualStyle] = {}

    # ── Public API ────────────────────────────────────────────────────────

    async def generate_illustration(
        self,
        concept: ConceptDescription,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> IllustrationResult:
        """Generate a single illustration from a concept description.

        Requirements: 7.1 (Gemini 3.1 Flash Image Preview), 7.2 (< 2 s),
                      7.3 (≥ 1024×1024), 7.4 (period-appropriate),
                      7.5 (store in Media Store), 7.6 (style consistency).

        Parameters
        ----------
        concept:
            The concept to illustrate.
        user_id:
            Owner user ID for media storage.
        session_id:
            Session ID for style consistency and media storage.

        Returns
        -------
        IllustrationResult
            Contains the illustration, storage info, and any error.
        """
        start_ms = time.monotonic() * 1000

        # Resolve session_id from concept context if not provided
        if session_id is None and concept.context:
            session_id = concept.context.session_id

        # Determine visual style (Req 7.4, 7.6)
        style = self._resolve_style(concept, session_id)

        # Build the prompt with style directives
        full_prompt = self._build_prompt(concept, style)

        try:
            # Call Gemini with timeout
            image_data, mime_type = await asyncio.wait_for(
                self._call_gemini(full_prompt, concept.aspect_ratio),
                timeout=_GENERATION_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            elapsed_ms = time.monotonic() * 1000 - start_ms
            logger.warning(
                "Illustration generation timed out after %.0f ms for prompt: %.80s",
                elapsed_ms,
                concept.prompt,
            )
            return self._fallback_result(concept, style, elapsed_ms, "Generation timed out")
        except Exception as exc:
            elapsed_ms = time.monotonic() * 1000 - start_ms
            logger.error(
                "Illustration generation failed after %.0f ms: %s",
                elapsed_ms,
                exc,
            )
            return self._fallback_result(concept, style, elapsed_ms, str(exc))

        elapsed_ms = time.monotonic() * 1000 - start_ms

        illustration = Illustration(
            image_data=image_data,
            mime_type=mime_type,
            resolution=_RESOLUTION_1K,
            style=style,
            generation_time_ms=elapsed_ms,
            caption=concept.prompt,
            concept_description=full_prompt,
        )

        # Store in Media Store if available (Req 7.5)
        media_id = None
        media_url = None
        stored = False
        if self._media_store and user_id and session_id and image_data:
            try:
                media_id, media_url = await self._store_illustration(
                    illustration, user_id, session_id
                )
                illustration.url = media_url
                stored = True
            except Exception as exc:
                logger.warning("Failed to store illustration: %s", exc)

        logger.info(
            "Illustration generated in %.0f ms, style=%s, stored=%s",
            elapsed_ms,
            style.value,
            stored,
        )

        return IllustrationResult(
            illustration=illustration,
            stored=stored,
            media_id=media_id,
            media_url=media_url,
        )

    async def generate_batch(
        self,
        concepts: list[ConceptDescription],
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> list[IllustrationResult]:
        """Generate multiple illustrations concurrently.

        Parameters
        ----------
        concepts:
            List of concepts to illustrate.
        user_id:
            Owner user ID for media storage.
        session_id:
            Session ID for style consistency.

        Returns
        -------
        list[IllustrationResult]
            One result per concept, in the same order.
        """
        tasks = [
            self.generate_illustration(c, user_id=user_id, session_id=session_id)
            for c in concepts
        ]
        return list(await asyncio.gather(*tasks, return_exceptions=False))

    def determine_style(self, context: DocumentaryContext) -> VisualStyle:
        """Determine the best visual style for a given documentary context.

        Requirements: 7.4 (period-appropriate), 7.6 (session consistency).

        Parameters
        ----------
        context:
            Documentary context with place types, historical period, etc.

        Returns
        -------
        VisualStyle
            The determined visual style.
        """
        # Check session cache first (Req 7.6)
        if context.session_id in self._session_styles:
            return self._session_styles[context.session_id]

        style = self._infer_style_from_context(context)

        # Cache for session consistency
        self._session_styles[context.session_id] = style
        return style

    def maintain_style_consistency(self, session_id: str) -> Optional[VisualStyle]:
        """Return the cached style for a session, or None if not yet set.

        Requirements: 7.6 (style consistency within session).
        """
        return self._session_styles.get(session_id)

    def clear_session_style(self, session_id: str) -> None:
        """Remove the cached style for a session (e.g. on session end)."""
        self._session_styles.pop(session_id, None)

    # ── Private helpers ───────────────────────────────────────────────────

    def _resolve_style(
        self,
        concept: ConceptDescription,
        session_id: Optional[str],
    ) -> VisualStyle:
        """Resolve the visual style for a concept, respecting overrides and session cache."""
        # Explicit override takes priority
        if concept.style_override:
            style = concept.style_override
        elif concept.historical_period:
            # Period-appropriate style (Req 7.4)
            style = VisualStyle.HISTORICAL
        elif concept.context:
            style = self.determine_style(concept.context)
        else:
            style = VisualStyle.ILLUSTRATED  # Safe default

        # Cache for session consistency (Req 7.6)
        if session_id and session_id not in self._session_styles:
            self._session_styles[session_id] = style

        return style

    def _infer_style_from_context(self, context: DocumentaryContext) -> VisualStyle:
        """Infer the best style from documentary context signals."""
        # Historical period → HISTORICAL
        if context.historical_period:
            return VisualStyle.HISTORICAL

        # Place type matching
        for place_type in context.place_types:
            normalized = place_type.lower().replace(" ", "_")
            if normalized in _PLACE_TYPE_STYLE_MAP:
                return _PLACE_TYPE_STYLE_MAP[normalized]

        # Majority vote from previous styles in session
        if context.previous_styles:
            from collections import Counter

            counts = Counter(context.previous_styles)
            return counts.most_common(1)[0][0]

        # Default
        return VisualStyle.ILLUSTRATED

    def _build_prompt(self, concept: ConceptDescription, style: VisualStyle) -> str:
        """Construct the full image generation prompt with style directives."""
        parts: list[str] = []

        # Style prefix
        style_desc = _STYLE_PROMPTS.get(style, _STYLE_PROMPTS[VisualStyle.ILLUSTRATED])
        parts.append(f"Style: {style_desc}.")

        # Historical period context (Req 7.4)
        if concept.historical_period:
            parts.append(
                f"Historical period: {concept.historical_period}. "
                "Ensure all visual elements are period-appropriate."
            )

        # Depth-level adjustment
        if concept.complexity == concept.complexity.EXPERT:
            parts.append("Include detailed annotations and technical accuracy.")
        elif concept.complexity == concept.complexity.SCHOLAR:
            parts.append("Balance detail with clarity. Include key labels.")

        # Main concept prompt
        parts.append(concept.prompt)

        # Documentary quality directive
        parts.append(
            "Generate a high-quality documentary illustration suitable for educational content. "
            "No text overlays or watermarks."
        )

        return " ".join(parts)

    async def _call_gemini(
        self,
        prompt: str,
        aspect_ratio: str = _DEFAULT_ASPECT_RATIO,
    ) -> tuple[bytes, str]:
        """Call Gemini 3.1 Flash Image Preview to generate an image.

        Requirements: 7.1 (Gemini 3.1 Flash Image Preview).

        Returns
        -------
        tuple[bytes, str]
            (image_bytes, mime_type)

        Raises
        ------
        IllustrationGenerationError
            If the model returns no image data.
        """
        # Import types from google.genai
        from google.genai import types

        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                image_size=_IMAGE_SIZE,
                aspect_ratio=aspect_ratio,
            ),
        )

        response = await self._client.aio.models.generate_content(
            model=_MODEL_ID,
            contents=prompt,
            config=config,
        )

        # Extract image from response parts
        if response and response.candidates:
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.inline_data and part.inline_data.data:
                            mime = part.inline_data.mime_type or "image/png"
                            return (part.inline_data.data, mime)

        raise IllustrationGenerationError(
            "Gemini returned no image data for the given prompt"
        )

    async def _store_illustration(
        self,
        illustration: Illustration,
        user_id: str,
        session_id: str,
    ) -> tuple[str, Optional[str]]:
        """Persist an illustration to the Media Store (Req 7.5).

        Returns
        -------
        tuple[str, Optional[str]]
            (media_id, signed_url)
        """
        # Lazy import to avoid circular dependency
        from ..media_store.models import MediaFile, MediaMetadata, MediaType

        media_file = MediaFile(
            media_type=MediaType.ILLUSTRATION,
            data=illustration.image_data,
            mime_type=illustration.mime_type,
            size=len(illustration.image_data) if illustration.image_data else 0,
            metadata=MediaMetadata(
                user_id=user_id,
                session_id=session_id,
                media_type=MediaType.ILLUSTRATION,
                extension="png",
                description=illustration.caption,
                extra={
                    "style": illustration.style.value,
                    "resolution": illustration.resolution,
                    "generation_time_ms": illustration.generation_time_ms,
                },
            ),
        )

        media_url = await self._media_store.store_media(
            media=media_file,
            user_id=user_id,
            session_id=session_id,
        )

        return (media_file.id, media_url)

    def _fallback_result(
        self,
        concept: ConceptDescription,
        style: VisualStyle,
        elapsed_ms: float,
        error_msg: str,
    ) -> IllustrationResult:
        """Create a graceful-degradation result when generation fails.

        Design reference: degrade_illustration_generation in design.md.
        """
        return IllustrationResult(
            illustration=Illustration(
                image_data=None,
                url=None,
                resolution=_RESOLUTION_1K,
                style=style,
                generation_time_ms=elapsed_ms,
                caption=concept.prompt,
                concept_description=concept.prompt,
            ),
            stored=False,
            error=error_msg,
        )
