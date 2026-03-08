"""Content Parser — DCF JSON string → ContentElement / DocumentaryStream.

Parses Documentary Content Format (DCF) strings into structured objects,
validates all required fields, and provides descriptive error messages for
invalid input.

Requirements: 28.2, 28.6, 28.7
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from pydantic import ValidationError

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
    Mode,
)

logger = logging.getLogger(__name__)


class ParseError(Exception):
    """Raised when a DCF string cannot be parsed into a valid content object.

    Always carries a descriptive message (Requirement 28.6).
    """


# ── Required field definitions per element type ───────────────────────────────

_REQUIRED_FIELDS: dict[str, list[str]] = {
    "narration": ["transcript"],
    "video": [],
    "illustration": [],
    "fact": ["claim"],
    "transition": [],
}

_TOP_LEVEL_REQUIRED = ["element_id", "sequence_id", "timestamp", "type", "content"]
_STREAM_REQUIRED = ["stream_id", "mode", "started_at", "elements"]


# ── ContentParser ─────────────────────────────────────────────────────────────


class ContentParser:
    """Parses DCF JSON strings into ContentElement and DocumentaryStream objects.

    Design reference: design.md §2 – Content Parser
    Requirements: 28.2, 28.6, 28.7
    """

    # ── Public API ────────────────────────────────────────────────────────────

    def parse(self, dcf_string: str) -> ContentElement:
        """Parse a DCF element JSON string into a ContentElement.

        Args:
            dcf_string: JSON string conforming to the DCF element grammar.

        Returns:
            A populated ContentElement object.

        Raises:
            ParseError: If the string is not valid JSON, violates the DCF
                        grammar, or is missing required fields.

        Requirements: 28.2, 28.6, 28.7
        """
        raw = self._decode_json(dcf_string)
        self._validate_top_level(raw)
        return self._build_element(raw)

    def parse_stream(self, dcf_string: str) -> DocumentaryStream:
        """Parse a DCF stream JSON string into a DocumentaryStream.

        Args:
            dcf_string: JSON string conforming to the DCF stream grammar.

        Returns:
            A DocumentaryStream with all elements parsed.

        Raises:
            ParseError: If the string is invalid or missing required fields.

        Requirements: 28.2, 28.6, 28.7
        """
        raw = self._decode_json(dcf_string)
        self._validate_stream_level(raw)

        try:
            dcf_stream = DCFStream.model_validate(
                self._prepare_stream_dict(raw)
            )
        except ValidationError as exc:
            raise ParseError(
                f"DCF stream validation failed: {self._format_validation_error(exc)}"
            ) from exc

        elements = [
            self._build_element(el.model_dump()) for el in dcf_stream.elements
        ]

        return DocumentaryStream(
            stream_id=dcf_stream.stream_id,
            request_id=dcf_stream.request_id,
            session_id=dcf_stream.session_id,
            mode=Mode(dcf_stream.mode),
            elements=elements,
            started_at=dcf_stream.started_at,
            completed_at=dcf_stream.completed_at,
            error=dcf_stream.error,
        )

    def validate_dict(self, data: dict[str, Any]) -> tuple[bool, list[str]]:
        """Validate a dictionary against the DCF element grammar.

        Returns:
            Tuple of (is_valid, list_of_error_messages).

        Requirements: 28.6, 28.7
        """
        errors: list[str] = []

        for field in _TOP_LEVEL_REQUIRED:
            if field not in data:
                errors.append(f"Missing required field: '{field}'")

        if "type" in data:
            element_type = data["type"]
            valid_types = {"narration", "video", "illustration", "fact", "transition"}
            if element_type not in valid_types:
                errors.append(
                    f"Invalid type '{element_type}'; "
                    f"must be one of {sorted(valid_types)}"
                )
            elif "content" in data and isinstance(data["content"], dict):
                content = data["content"]
                for req_field in _REQUIRED_FIELDS.get(element_type, []):
                    if req_field not in content or content[req_field] is None:
                        errors.append(
                            f"content.{req_field} is required for type '{element_type}'"
                        )

        if "sequence_id" in data:
            seq = data["sequence_id"]
            if not isinstance(seq, int) or seq < 0:
                errors.append("sequence_id must be a non-negative integer")

        if "timestamp" in data:
            ts = data["timestamp"]
            if not isinstance(ts, (int, float)) or ts < 0:
                errors.append("timestamp must be a non-negative number")

        return (len(errors) == 0, errors)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _decode_json(self, dcf_string: str) -> dict[str, Any]:
        """Decode a JSON string; raise ParseError on failure."""
        if not isinstance(dcf_string, str):
            raise ParseError(
                f"Expected a JSON string, got {type(dcf_string).__name__}"
            )
        try:
            data = json.loads(dcf_string)
        except json.JSONDecodeError as exc:
            raise ParseError(f"Invalid JSON: {exc.msg} at position {exc.pos}") from exc
        if not isinstance(data, dict):
            raise ParseError(
                f"DCF must be a JSON object, got {type(data).__name__}"
            )
        return data

    def _validate_top_level(self, data: dict[str, Any]) -> None:
        """Validate required top-level fields (Requirement 28.7)."""
        is_valid, errors = self.validate_dict(data)
        if not is_valid:
            raise ParseError(
                "DCF element is invalid:\n" + "\n".join(f"  - {e}" for e in errors)
            )

    def _validate_stream_level(self, data: dict[str, Any]) -> None:
        """Validate required fields for a stream-level DCF object."""
        errors: list[str] = []
        for field in _STREAM_REQUIRED:
            if field not in data:
                errors.append(f"Missing required field: '{field}'")
        if errors:
            raise ParseError(
                "DCF stream is invalid:\n" + "\n".join(f"  - {e}" for e in errors)
            )

    def _prepare_stream_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Prepare raw stream dict for DCFStream.model_validate()."""
        prepared = dict(data)
        prepared.setdefault("version", "1.0")
        # Each element dict must pass through _prepare_element_dict
        if "elements" in prepared and isinstance(prepared["elements"], list):
            prepared["elements"] = [
                self._prepare_element_dict(el)
                for el in prepared["elements"]
                if isinstance(el, dict)
            ]
        return prepared

    def _build_element(self, data: dict[str, Any]) -> ContentElement:
        """Convert a validated DCF element dict into a ContentElement."""
        element_type = data["type"]
        content_data = data.get("content", {})
        if not isinstance(content_data, dict):
            raise ParseError(
                f"content must be a JSON object, got {type(content_data).__name__}"
            )

        # Build the typed DCFElement via Pydantic for content validation
        try:
            dcf_element = DCFElement.model_validate(
                self._prepare_element_dict(data)
            )
        except ValidationError as exc:
            raise ParseError(
                f"DCF element validation failed: {self._format_validation_error(exc)}"
            ) from exc

        return self._dcf_element_to_content_element(dcf_element)

    def _prepare_element_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Add defaults and type-tag the content block for model_validate."""
        prepared = dict(data)
        prepared.setdefault("version", "1.0")
        content = dict(prepared.get("content", {})) if isinstance(prepared.get("content"), dict) else {}

        # Map content to the correct typed model based on element type
        element_type = prepared.get("type", "")
        prepared["content"] = self._coerce_content(element_type, content)
        return prepared

    def _coerce_content(
        self, element_type: str, content: dict[str, Any]
    ) -> dict[str, Any]:
        """Coerce content dict into the correct typed content model shape."""
        # We return the raw dict; Pydantic model_validate on DCFElement handles the union
        # by selecting the right model based on which fields are present.
        # Since the union is ordered, we need to ensure the dict has discriminating fields.
        # We add type-specific marker defaults to assist disambiguation.
        if element_type == "narration":
            content.setdefault("transcript", content.get("transcript", ""))
        elif element_type == "fact":
            content.setdefault("claim", content.get("claim", ""))
        return content

    def _dcf_element_to_content_element(self, dcf: DCFElement) -> ContentElement:
        """Convert a typed DCFElement into the flat ContentElement model.

        Mapping (DCF → ContentElement):
          narration: transcript→narration_text, audio_data→audio_data,
                     duration→audio_duration, tone→emotional_tone
          video:     video_url→video_url, duration→video_duration
          illustration: image_url→image_url, image_data→image_data,
                        caption→caption, visual_style→visual_style
          fact:      claim→claim_text, verified→verified,
                     confidence→confidence, sources→sources
          transition: message→transition_text

        Requirements: 28.2
        """
        kwargs: dict[str, Any] = {
            "id": dcf.element_id,
            "type": ContentElementType(dcf.type),
            "sequence_id": dcf.sequence_id,
            "timestamp": dcf.timestamp,
        }

        c = dcf.content

        if isinstance(c, NarrationContent):
            kwargs["narration_text"] = c.transcript
            kwargs["audio_data"] = c.audio_data
            kwargs["audio_duration"] = c.duration
            kwargs["emotional_tone"] = c.tone if c.tone != "neutral" else None
            # Store language and depth_level in a stable way
            # (they have no dedicated ContentElement field — we embed them
            #  in emotional_tone context string if non-default, but to
            #  preserve round-trip we keep tone/language/depth_level as
            #  individual fields on the extended content model.
            # For ContentElement we map: tone stored in emotional_tone,
            # language & depth_level stored in a JSON-encoded suffix that
            # the serializer strips back out.)
            #
            # Simpler approach: store auxiliary narration meta in the
            # transition_text field? No — use the existing image_url slot? No.
            # The cleanest round-trip solution: store language+depth_level
            # in a structured way within emotional_tone as "tone|lang|depth".
            tone_str = c.tone or "neutral"
            # Encode auxiliary narration metadata into emotional_tone using
            # a compact tagged format: "tone;lang=<l>;depth=<d>"
            parts = [tone_str]
            if c.language != "en":
                parts.append(f"lang={c.language}")
            if c.depth_level != "explorer":
                parts.append(f"depth={c.depth_level}")
            if c.audio_url:
                parts.append(f"audio_url={c.audio_url}")
            kwargs["emotional_tone"] = ";".join(parts)

        elif isinstance(c, VideoContent):
            kwargs["video_url"] = c.video_url
            kwargs["video_duration"] = c.duration
            # Store auxiliary video meta in transition_text using tagged format
            meta_parts: list[str] = []
            if c.thumbnail_url:
                meta_parts.append(f"thumb={c.thumbnail_url}")
            if c.resolution != "1080p":
                meta_parts.append(f"res={c.resolution}")
            if c.has_native_audio:
                meta_parts.append("audio=1")
            if c.scene_description:
                meta_parts.append(f"desc={c.scene_description}")
            if meta_parts:
                kwargs["transition_text"] = ";".join(meta_parts)

        elif isinstance(c, IllustrationContent):
            kwargs["image_url"] = c.image_url
            kwargs["image_data"] = c.image_data
            kwargs["caption"] = c.caption
            kwargs["visual_style"] = c.visual_style
            # Store concept_description in transition_text if present
            if c.concept_description:
                kwargs["transition_text"] = f"concept={c.concept_description}"

        elif isinstance(c, FactContent):
            kwargs["claim_text"] = c.claim
            kwargs["verified"] = c.verified
            kwargs["confidence"] = c.confidence
            kwargs["sources"] = [
                src.model_dump() for src in c.sources
            ]
            # Store alternative perspectives in transition_text using JSON
            if c.alternative_perspectives:
                import json as _json
                kwargs["transition_text"] = (
                    "perspectives=" + _json.dumps(c.alternative_perspectives)
                )

        elif isinstance(c, TransitionContent):
            kwargs["transition_text"] = (
                f"{c.transition_type}|{c.message}" if c.message
                else c.transition_type
            )

        return ContentElement(**kwargs)

    @staticmethod
    def _format_validation_error(exc: ValidationError) -> str:
        """Format a Pydantic ValidationError into a human-readable string."""
        messages = []
        for error in exc.errors():
            loc = " → ".join(str(l) for l in error["loc"])
            messages.append(f"{loc}: {error['msg']}")
        return "; ".join(messages)
