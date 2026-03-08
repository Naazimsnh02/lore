"""Content Serializer — ContentElement / DocumentaryStream → DCF JSON string.

Serializes structured content objects into Documentary Content Format (DCF)
JSON strings for storage and transmission.

Requirements: 28.3, 28.4, 28.5
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.services.content_parser.models import (
    DCFElement,
    DCFStream,
    FactContent,
    IllustrationContent,
    NarrationContent,
    SourceCitation,
    TransitionContent,
    VideoContent,
)
from backend.services.orchestrator.models import (
    ContentElement,
    ContentElementType,
    DocumentaryStream,
)

logger = logging.getLogger(__name__)


class ContentSerializer:
    """Serializes ContentElement and DocumentaryStream objects to DCF JSON.

    Guarantees that for all valid ContentElement objects C:
        parse(serialize(C)) == C   (round-trip property, Requirement 28.5)

    Design reference: design.md §2 – Content Serializer
    Requirements: 28.3, 28.4, 28.5
    """

    def serialize(self, element: ContentElement) -> str:
        """Serialize a ContentElement to a DCF element JSON string.

        Args:
            element: A valid ContentElement to serialize.

        Returns:
            A compact JSON string conforming to the DCF element grammar.

        Requirements: 28.3, 28.4
        """
        dcf = self._element_to_dcf(element)
        return dcf.model_dump_json()

    def serialize_stream(self, stream: DocumentaryStream) -> str:
        """Serialize a DocumentaryStream to a DCF stream JSON string.

        Args:
            stream: A valid DocumentaryStream to serialize.

        Returns:
            A compact JSON string conforming to the DCF stream grammar.

        Requirements: 28.3, 28.4
        """
        elements = [self._element_to_dcf(el) for el in stream.elements]
        dcf_stream = DCFStream(
            stream_id=stream.stream_id,
            request_id=stream.request_id,
            session_id=stream.session_id,
            mode=stream.mode.value,
            started_at=stream.started_at,
            completed_at=stream.completed_at,
            error=stream.error,
            elements=elements,
        )
        return dcf_stream.model_dump_json()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _element_to_dcf(self, element: ContentElement) -> DCFElement:
        """Convert a ContentElement to a typed DCFElement.

        The inverse mapping of ContentParser._dcf_element_to_content_element():

          ContentElement → DCF
          ─────────────────────────────────────────────────────
          NARRATION:  narration_text→transcript, audio_data→audio_data,
                      audio_duration→duration, emotional_tone→tone+metadata
          VIDEO:      video_url→video_url, video_duration→duration,
                      transition_text(meta)→thumbnail_url/resolution/etc.
          ILLUSTRATION: image_url→image_url, image_data→image_data,
                        caption→caption, visual_style→visual_style,
                        transition_text(concept)→concept_description
          FACT:       claim_text→claim, verified→verified,
                      sources→sources, confidence→confidence,
                      transition_text(perspectives)→alternative_perspectives
          TRANSITION: transition_text→transition_type+message

        Requirements: 28.3, 28.4
        """
        typed_content: Any

        if element.type == ContentElementType.NARRATION:
            typed_content = self._build_narration_content(element)

        elif element.type == ContentElementType.VIDEO:
            typed_content = self._build_video_content(element)

        elif element.type == ContentElementType.ILLUSTRATION:
            typed_content = self._build_illustration_content(element)

        elif element.type == ContentElementType.FACT:
            typed_content = self._build_fact_content(element)

        elif element.type == ContentElementType.TRANSITION:
            typed_content = self._build_transition_content(element)

        else:
            # Fallback for unknown types — emit an empty transition
            logger.warning("Unknown ContentElementType %s; using transition fallback", element.type)
            typed_content = TransitionContent(transition_type="scene_change")

        return DCFElement(
            element_id=element.id,
            sequence_id=element.sequence_id,
            timestamp=element.timestamp,
            type=element.type.value,
            content=typed_content,
        )

    # ── Per-type content builders ─────────────────────────────────────────────

    def _build_narration_content(self, el: ContentElement) -> NarrationContent:
        """Build NarrationContent from a NARRATION ContentElement.

        Decodes the compact tagged emotional_tone format:
          "tone;lang=<l>;depth=<d>;audio_url=<u>"
        """
        tone, language, depth_level, audio_url = self._decode_narration_tone(
            el.emotional_tone
        )
        return NarrationContent(
            transcript=el.narration_text or "",
            audio_data=el.audio_data,
            audio_url=audio_url,
            duration=el.audio_duration,
            language=language,
            tone=tone,
            depth_level=depth_level,
        )

    def _build_video_content(self, el: ContentElement) -> VideoContent:
        """Build VideoContent from a VIDEO ContentElement.

        Decodes video metadata stored in transition_text as tagged pairs:
          "thumb=<url>;res=<r>;audio=1;desc=<description>"
        """
        thumbnail_url = ""
        resolution = "1080p"
        has_native_audio = False
        scene_description = ""

        if el.transition_text:
            for part in el.transition_text.split(";"):
                if part.startswith("thumb="):
                    thumbnail_url = part[6:]
                elif part.startswith("res="):
                    resolution = part[4:]
                elif part == "audio=1":
                    has_native_audio = True
                elif part.startswith("desc="):
                    scene_description = part[5:]

        return VideoContent(
            video_url=el.video_url or "",
            thumbnail_url=thumbnail_url,
            duration=el.video_duration,
            resolution=resolution,
            has_native_audio=has_native_audio,
            scene_description=scene_description,
        )

    def _build_illustration_content(self, el: ContentElement) -> IllustrationContent:
        """Build IllustrationContent from an ILLUSTRATION ContentElement.

        Decodes concept_description stored in transition_text as:
          "concept=<description>"
        """
        concept_description = ""
        if el.transition_text and el.transition_text.startswith("concept="):
            concept_description = el.transition_text[8:]

        return IllustrationContent(
            image_url=el.image_url or "",
            image_data=el.image_data,
            caption=el.caption or "",
            visual_style=el.visual_style or "illustrated",
            concept_description=concept_description,
        )

    def _build_fact_content(self, el: ContentElement) -> FactContent:
        """Build FactContent from a FACT ContentElement.

        Decodes alternative perspectives stored in transition_text as:
          "perspectives=[...]"
        """
        alternative_perspectives: list[str] = []
        if el.transition_text and el.transition_text.startswith("perspectives="):
            try:
                import json as _json
                alternative_perspectives = _json.loads(el.transition_text[13:])
            except (json.JSONDecodeError, ValueError):
                logger.warning("Failed to decode alternative perspectives from transition_text")

        sources = [
            SourceCitation(
                title=src.get("title", ""),
                url=src.get("url", ""),
                authority=src.get("authority", "other")
                if src.get("authority", "other") in {"academic", "government", "media", "other"}
                else "other",
                excerpt=src.get("excerpt", ""),
            )
            for src in el.sources
        ]

        return FactContent(
            claim=el.claim_text or "",
            verified=el.verified if el.verified is not None else False,
            sources=sources,
            confidence=el.confidence if el.confidence is not None else 0.0,
            alternative_perspectives=alternative_perspectives,
        )

    def _build_transition_content(self, el: ContentElement) -> TransitionContent:
        """Build TransitionContent from a TRANSITION ContentElement.

        Decodes transition_text stored as:
          "<transition_type>|<message>"  or just  "<transition_type>"
        """
        valid_types = {"scene_change", "topic_shift", "branch_enter", "branch_exit"}
        transition_type = "scene_change"
        message = ""

        if el.transition_text:
            if "|" in el.transition_text:
                ttype, _, msg = el.transition_text.partition("|")
                if ttype in valid_types:
                    transition_type = ttype
                    message = msg
                else:
                    message = el.transition_text
            elif el.transition_text in valid_types:
                transition_type = el.transition_text
            else:
                message = el.transition_text

        return TransitionContent(transition_type=transition_type, message=message)

    # ── Narration tone codec ──────────────────────────────────────────────────

    @staticmethod
    def _decode_narration_tone(
        emotional_tone: str | None,
    ) -> tuple[str, str, str, str]:
        """Decode the compact narration tone string.

        Format: "tone[;lang=<l>][;depth=<d>][;audio_url=<u>]"

        Returns:
            (tone, language, depth_level, audio_url)
        """
        tone = "neutral"
        language = "en"
        depth_level = "explorer"
        audio_url = ""

        if not emotional_tone:
            return tone, language, depth_level, audio_url

        parts = emotional_tone.split(";")
        if parts:
            tone = parts[0] or "neutral"
        for part in parts[1:]:
            if part.startswith("lang="):
                language = part[5:]
            elif part.startswith("depth="):
                depth_level = part[6:]
            elif part.startswith("audio_url="):
                audio_url = part[10:]

        return tone, language, depth_level, audio_url
