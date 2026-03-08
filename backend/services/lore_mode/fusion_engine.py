"""FusionEngine — merges visual, verbal, and GPS contexts for LoreMode.

Design reference: LORE design.md, Context Fusion Strategy.
Requirements:
  4.2 — Fuse contextual information from camera and voice
  4.5 — Link visual and spoken contexts for cross-modal queries
  4.6 — Prioritise voice input over camera when processing load exceeds capacity

Strategy:
  1. Visual provides location and scene information
  2. Verbal provides topic focus and user intent
  3. GPS provides geographic context and nearby landmarks
  4. Fusion creates enriched context combining all modalities
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from .models import (
    ConnectionType,
    CrossModalConnection,
    FusedContext,
    ProcessingLoad,
    ProcessingPriority,
)

logger = logging.getLogger(__name__)

# Historical keywords to help detect connections
_HISTORICAL_KEYWORDS = {
    "ancient", "medieval", "renaissance", "roman", "greek", "egyptian",
    "viking", "colonial", "victorian", "revolution", "war", "battle",
    "empire", "dynasty", "kingdom", "century", "historic", "heritage",
    "monument", "temple", "cathedral", "castle", "palace", "fortress",
    "ruins", "archaeological", "artifact", "museum",
}

_CULTURAL_KEYWORDS = {
    "festival", "tradition", "cuisine", "art", "music", "dance", "religion",
    "ceremony", "ritual", "mythology", "folklore", "legend", "custom",
    "architecture", "craft", "language", "literature", "theater", "theatre",
}

_GEOGRAPHIC_KEYWORDS = {
    "mountain", "river", "lake", "ocean", "sea", "island", "valley",
    "desert", "forest", "canyon", "coast", "peninsula", "volcano",
    "glacier", "plateau", "delta", "bay", "strait", "cave",
}

# Place types that imply historical significance
_HISTORICAL_PLACE_TYPES = {
    "museum", "church", "temple", "mosque", "synagogue", "monument",
    "landmark", "castle", "palace", "fort", "cemetery", "memorial",
    "archaeological_site", "historical_landmark", "cultural_landmark",
    "tourist_attraction", "place_of_worship", "heritage_site",
}


class FusionEngine:
    """Fuses multimodal contexts into a unified documentary context.

    The engine combines visual (camera), verbal (voice), and GPS contexts
    to create a rich FusedContext object.  It detects cross-modal connections
    between what the user sees and what they ask about, and adjusts processing
    priority when the system is under load.
    """

    def fuse(
        self,
        visual_context: Optional[dict[str, Any]] = None,
        verbal_context: Optional[dict[str, Any]] = None,
        gps_context: Optional[dict[str, float]] = None,
        frame_data: Optional[bytes] = None,
        processing_load: Optional[ProcessingLoad] = None,
    ) -> FusedContext:
        """Fuse multimodal contexts into a unified FusedContext.

        Args:
            visual_context: Dict from SightMode recognition (place_name,
                place_id, place_types, etc.).  May be None if camera
                recognition failed.
            verbal_context: Dict from VoiceMode transcription (topic,
                original_query, language, confidence).
            gps_context: Dict with latitude, longitude, accuracy.
            frame_data: Raw camera frame bytes for style reference.
            processing_load: Current processing load metrics.

        Returns:
            FusedContext with merged information and detected connections.
        """
        visual_context = visual_context or {}
        verbal_context = verbal_context or {}
        gps_context = gps_context or {}

        # Determine processing priority (Req 4.6)
        priority = self._determine_priority(processing_load)

        # Extract visual fields
        place_name = visual_context.get("place_name", "")
        place_id = visual_context.get("place_id", "")
        place_description = visual_context.get("place_description", "")
        place_types = visual_context.get("place_types", [])
        visual_description = visual_context.get("visual_description", "")
        visual_confidence = visual_context.get("confidence", 0.0)
        vis_lat = visual_context.get("latitude", 0.0)
        vis_lon = visual_context.get("longitude", 0.0)
        formatted_address = visual_context.get("formatted_address", "")

        # Extract verbal fields
        topic = verbal_context.get("topic", "")
        original_query = verbal_context.get("original_query", "")
        language = verbal_context.get("language", "en")
        verbal_confidence = verbal_context.get("confidence", 0.0)

        # Extract GPS fields
        gps_lat = gps_context.get("latitude", 0.0)
        gps_lon = gps_context.get("longitude", 0.0)
        gps_accuracy = gps_context.get("accuracy", 0.0)

        # Primary location: prefer visual (camera) over GPS
        latitude = vis_lat if vis_lat != 0.0 else gps_lat
        longitude = vis_lon if vis_lon != 0.0 else gps_lon

        # Build fused topic (verbal is primary, enriched with visual)
        fused_topic = self._build_fused_topic(
            topic, place_name, visual_description, priority
        )

        # Find cross-modal connections (Req 4.5)
        connections = self.find_connections(
            place_name=place_name,
            place_types=place_types,
            place_description=place_description,
            visual_description=visual_description,
            topic=topic,
        )

        # Check for historical significance for advanced features
        has_historical = self._has_historical_significance(
            place_types, place_description, visual_description
        )

        fused = FusedContext(
            mode="lore",
            place_id=place_id,
            place_name=place_name,
            place_description=place_description,
            place_types=place_types,
            latitude=latitude,
            longitude=longitude,
            formatted_address=formatted_address,
            visual_description=visual_description,
            visual_confidence=visual_confidence,
            topic=topic,
            original_query=original_query,
            language=language,
            verbal_confidence=verbal_confidence,
            gps_latitude=gps_lat,
            gps_longitude=gps_lon,
            gps_accuracy=gps_accuracy,
            fused_topic=fused_topic,
            cross_modal_connections=connections,
            enable_alternate_history=True,
            enable_historical_characters=has_historical,
            frame_data=frame_data,
            processing_priority=priority,
        )

        logger.info(
            "Fused context: topic=%r place=%r connections=%d priority=%s",
            fused_topic,
            place_name,
            len(connections),
            priority.value,
        )

        return fused

    def find_connections(
        self,
        *,
        place_name: str = "",
        place_types: list[str] | None = None,
        place_description: str = "",
        visual_description: str = "",
        topic: str = "",
    ) -> list[CrossModalConnection]:
        """Find semantic connections between the location and topic.

        Detects historical, cultural, geographic, temporal, and thematic
        connections by analysing keyword overlap and place type signals.

        Args:
            place_name: Recognised place name.
            place_types: Place type tags from Google Places.
            place_description: Place editorial summary.
            visual_description: Gemini-generated scene description.
            topic: User's spoken topic.

        Returns:
            List of CrossModalConnection objects sorted by relevance.
        """
        if not topic or (not place_name and not place_description):
            return []

        place_types = place_types or []
        connections: list[CrossModalConnection] = []

        # Combine all location text for keyword matching
        location_text = " ".join(
            [place_name, place_description, visual_description, " ".join(place_types)]
        ).lower()
        topic_lower = topic.lower()

        # Extract words for overlap
        location_words = set(re.findall(r"\b[a-z]{3,}\b", location_text))
        topic_words = set(re.findall(r"\b[a-z]{3,}\b", topic_lower))

        # Historical connections
        historical_signals = (
            location_words & _HISTORICAL_KEYWORDS
        ) | (topic_words & _HISTORICAL_KEYWORDS)
        has_historical_type = bool(set(place_types) & _HISTORICAL_PLACE_TYPES)

        if historical_signals or has_historical_type:
            relevance = self.calculate_relevance(
                location_words, topic_words, _HISTORICAL_KEYWORDS
            )
            if has_historical_type:
                relevance = min(1.0, relevance + 0.2)
            if relevance > 0.0:
                shared = list((location_words | topic_words) & _HISTORICAL_KEYWORDS)
                connections.append(
                    CrossModalConnection(
                        type=ConnectionType.HISTORICAL,
                        description=f"{topic} at {place_name}" if place_name else topic,
                        relevance=relevance,
                        keywords=shared[:5],
                    )
                )

        # Cultural connections
        cultural_signals = (
            location_words & _CULTURAL_KEYWORDS
        ) | (topic_words & _CULTURAL_KEYWORDS)
        if cultural_signals:
            relevance = self.calculate_relevance(
                location_words, topic_words, _CULTURAL_KEYWORDS
            )
            if relevance > 0.0:
                shared = list((location_words | topic_words) & _CULTURAL_KEYWORDS)
                connections.append(
                    CrossModalConnection(
                        type=ConnectionType.CULTURAL,
                        description=f"Cultural aspects of {topic} at {place_name}" if place_name else topic,
                        relevance=relevance,
                        keywords=shared[:5],
                    )
                )

        # Geographic connections
        geographic_signals = (
            location_words & _GEOGRAPHIC_KEYWORDS
        ) | (topic_words & _GEOGRAPHIC_KEYWORDS)
        if geographic_signals:
            relevance = self.calculate_relevance(
                location_words, topic_words, _GEOGRAPHIC_KEYWORDS
            )
            if relevance > 0.0:
                shared = list((location_words | topic_words) & _GEOGRAPHIC_KEYWORDS)
                connections.append(
                    CrossModalConnection(
                        type=ConnectionType.GEOGRAPHIC,
                        description=f"Geographic context of {topic} at {place_name}" if place_name else topic,
                        relevance=relevance,
                        keywords=shared[:5],
                    )
                )

        # Thematic connection: direct word overlap between topic and location
        direct_overlap = location_words & topic_words
        # Filter out very common words
        common_words = {"the", "and", "for", "that", "this", "with", "from", "are", "was", "has", "have", "been"}
        meaningful_overlap = direct_overlap - common_words - _HISTORICAL_KEYWORDS - _CULTURAL_KEYWORDS - _GEOGRAPHIC_KEYWORDS
        if meaningful_overlap:
            relevance = min(1.0, len(meaningful_overlap) * 0.25)
            connections.append(
                CrossModalConnection(
                    type=ConnectionType.THEMATIC,
                    description=f"Thematic link between {topic} and {place_name}" if place_name else topic,
                    relevance=relevance,
                    keywords=list(meaningful_overlap)[:5],
                )
            )

        # Sort by relevance descending
        connections.sort(key=lambda c: c.relevance, reverse=True)
        return connections

    @staticmethod
    def calculate_relevance(
        location_words: set[str],
        topic_words: set[str],
        domain_keywords: set[str],
    ) -> float:
        """Calculate relevance score based on keyword overlap with a domain.

        Score components:
        - 0.4 from topic overlap with domain
        - 0.4 from location overlap with domain
        - 0.2 from cross-modal overlap (topic ∩ location ∩ domain)

        Returns:
            Float between 0.0 and 1.0.
        """
        topic_domain = topic_words & domain_keywords
        location_domain = location_words & domain_keywords
        cross_modal = topic_domain & location_domain

        if not topic_domain and not location_domain:
            return 0.0

        # Normalise by domain size (capped at 5 matches = max contribution)
        topic_score = min(1.0, len(topic_domain) / 3) * 0.4
        location_score = min(1.0, len(location_domain) / 3) * 0.4
        cross_score = min(1.0, len(cross_modal) / 2) * 0.2

        return round(min(1.0, topic_score + location_score + cross_score), 3)

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _build_fused_topic(
        topic: str,
        place_name: str,
        visual_description: str,
        priority: ProcessingPriority,
    ) -> str:
        """Build a combined topic string from verbal + visual contexts.

        When under load (voice_priority), the topic is used as-is with
        minimal location enrichment.
        """
        parts: list[str] = []

        if topic:
            parts.append(topic)

        if priority == ProcessingPriority.DEGRADED:
            # Under heavy load, use just the voice topic
            return " ".join(parts) if parts else "Unknown topic"

        if place_name:
            if topic:
                parts.append(f"at {place_name}")
            else:
                parts.append(place_name)
        elif visual_description and not topic:
            parts.append(f"(visual: {visual_description})")

        return " ".join(parts) if parts else "Unknown topic"

    @staticmethod
    def _determine_priority(
        load: Optional[ProcessingLoad],
    ) -> ProcessingPriority:
        """Determine processing priority based on current load (Req 4.6).

        When overloaded:
        - voice input is prioritised
        - camera frame rate is reduced
        """
        if load is None:
            return ProcessingPriority.NORMAL

        if load.is_overloaded:
            if load.voice_latency_ms > 2000:
                return ProcessingPriority.DEGRADED
            return ProcessingPriority.VOICE_PRIORITY

        return ProcessingPriority.NORMAL

    @staticmethod
    def _has_historical_significance(
        place_types: list[str],
        place_description: str,
        visual_description: str,
    ) -> bool:
        """Check whether the location has historical significance."""
        if set(place_types) & _HISTORICAL_PLACE_TYPES:
            return True

        combined = f"{place_description} {visual_description}".lower()
        return bool(set(re.findall(r"\b[a-z]{3,}\b", combined)) & _HISTORICAL_KEYWORDS)
