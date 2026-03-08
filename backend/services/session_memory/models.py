"""Pydantic data models for the Session Memory Manager.

These models mirror the Firestore document schema defined in the LORE design
document (Section 10 – Session Memory Manager).  Every field that is stored in
Firestore is represented here so that the manager can do full round-trip
serialisation / deserialisation through Pydantic.

Firestore collection layout:
  sessions/{sessionId}                – SessionDocument
    └── (sub-collections are flattened into the document arrays)

Indexes required (create in Firestore console / Terraform):
  - sessions: userId ASC, startTime DESC
  - sessions: userId ASC, status ASC
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────


class OperatingMode(str, Enum):
    SIGHT = "sight"
    VOICE = "voice"
    LORE = "lore"


class DepthDial(str, Enum):
    EXPLORER = "explorer"
    SCHOLAR = "scholar"
    EXPERT = "expert"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"


class ContentType(str, Enum):
    NARRATION = "narration"
    VIDEO = "video"
    ILLUSTRATION = "illustration"
    FACT = "fact"


class InteractionType(str, Enum):
    VOICE_INPUT = "voice_input"
    BARGE_IN = "barge_in"
    MODE_SWITCH = "mode_switch"
    BRANCH_REQUEST = "branch_request"
    QUERY = "query"


# ── Nested models ──────────────────────────────────────────────────────────────


class GeoPoint(BaseModel):
    """Geographic coordinates stored alongside a location visit."""

    latitude: float
    longitude: float


class ContentCount(BaseModel):
    """Running tally of each content type generated in a session."""

    narration_segments: int = 0
    video_clips: int = 0
    illustrations: int = 0
    facts: int = 0


class ContentRefMetadata(BaseModel):
    """Metadata attached to every piece of generated content."""

    depth_level: DepthDial
    language: str
    emotional_tone: Optional[str] = None
    # Authoritative source URLs attached by the Search Grounder
    sources: list[str] = Field(default_factory=list)
    # Arbitrary extra metadata (e.g. image resolution, video resolution)
    extra: dict[str, Any] = Field(default_factory=dict)


class LocationVisit(BaseModel):
    """Records a single location the user visited during a session.

    Requirement 10.1 – all locations visited must be stored.
    """

    place_id: str
    name: str
    coordinates: GeoPoint
    # Unix millisecond timestamps for all time fields (consistent with the
    # WebSocket protocol defined in models.py)
    visit_time_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    duration_seconds: float = 0.0
    # IDs of ContentRef documents triggered by this visit
    triggered_content_ids: list[str] = Field(default_factory=list)


class UserInteraction(BaseModel):
    """Records a single user interaction (voice, barge-in, query, etc.).

    Requirement 10.1 – all interactions must be stored with timestamps.
    """

    interaction_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    interaction_type: InteractionType
    input: str
    response: str
    processing_time_ms: float = 0.0


class ContentRef(BaseModel):
    """Reference to a piece of generated content stored in Cloud Storage.

    Requirement 10.1 – all generated content must be stored with timestamps.
    """

    content_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content_type: ContentType
    # Signed URL pointing to the actual media in Cloud Storage
    storage_url: str
    timestamp_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    duration_seconds: Optional[float] = None  # for video/narration
    metadata: ContentRefMetadata


class BranchNode(BaseModel):
    """Represents one node in the Branch Documentary tree.

    Requirement 10.1 – branch structure must be stored.
    Requirement 13.4 – maximum depth is 3.
    """

    branch_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    parent_branch_id: Optional[str] = None  # None = root branch
    topic: str
    depth: int = Field(ge=0, le=3)  # 0 = root, max = 3
    start_time_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    end_time_ms: Optional[int] = None
    content_ids: list[str] = Field(default_factory=list)


# ── Top-level document ─────────────────────────────────────────────────────────


class SessionDocument(BaseModel):
    """Full Firestore document for a LORE session.

    Maps 1-to-1 to the ``sessions/{sessionId}`` Firestore document.
    """

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str  # indexed in Firestore
    mode: OperatingMode
    status: SessionStatus = SessionStatus.ACTIVE

    # Configuration snapshot at session start
    depth_dial: DepthDial = DepthDial.SCHOLAR
    language: str = "en"

    start_time_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    end_time_ms: Optional[int] = None

    # Nested arrays (Firestore stores these as array fields)
    locations: list[LocationVisit] = Field(default_factory=list)
    interactions: list[UserInteraction] = Field(default_factory=list)
    content_references: list[ContentRef] = Field(default_factory=list)
    branch_structure: list[BranchNode] = Field(default_factory=list)

    # Derived counters kept in sync by the manager
    total_duration_seconds: float = 0.0
    content_count: ContentCount = Field(default_factory=ContentCount)

    def to_firestore_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for Firestore set/update."""
        return self.model_dump(mode="json")

    @classmethod
    def from_firestore_dict(cls, data: dict[str, Any]) -> "SessionDocument":
        """Deserialise from a Firestore document snapshot dict."""
        return cls.model_validate(data)


# ── Query result ───────────────────────────────────────────────────────────────


class QueryResult(BaseModel):
    """Represents one matching item returned by a cross-session query.

    Requirement 10.4 – enable cross-session queries.
    """

    session_id: str
    session_start_time_ms: int
    match_type: str  # e.g. "interaction", "location", "content"
    snippet: str  # human-readable summary of the match
    relevance_score: float = Field(ge=0.0, le=1.0)
    # Raw matched object serialised to a dict for downstream use
    raw: dict[str, Any] = Field(default_factory=dict)
