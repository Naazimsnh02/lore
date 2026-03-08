"""Pydantic data models for the Media Store Manager.

These models mirror the MediaFile, MediaMetadata, and QuotaInfo interfaces
defined in the LORE design document (Section 11 – Media Store Manager).

Cloud Storage object path convention:
    media/{userId}/{sessionId}/{mediaType}/{mediaId}.{ext}

Requirements: 22.1 – 22.7
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────


class MediaType(str, Enum):
    VIDEO = "video"
    ILLUSTRATION = "illustration"
    NARRATION = "narration"
    CHRONICLE = "chronicle"  # exported PDF


class MediaStatus(str, Enum):
    ACTIVE = "active"
    DELETED = "deleted"
    EXPIRED = "expired"


# ── Nested models ──────────────────────────────────────────────────────────────


class MediaMetadata(BaseModel):
    """Arbitrary key-value metadata stored alongside a media object.

    Stored both as Cloud Storage object metadata and in Firestore for
    indexed lookups (e.g. listing all media for a session).
    """

    user_id: str
    session_id: str
    media_type: MediaType
    created_at_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    # GCS object name (full path inside the bucket)
    gcs_object_name: str = ""
    # Original file extension (mp4, jpg, png, pdf …)
    extension: str = ""
    # Human-readable description / prompt used to generate the content
    description: str = ""
    # Extra pass-through fields (resolution, duration, …)
    extra: dict[str, Any] = Field(default_factory=dict)


class MediaFile(BaseModel):
    """Represents a media artifact stored in (or to be stored in) Cloud Storage.

    The ``data`` field holds the raw bytes when reading from or writing to GCS.
    It is excluded from serialisation to dicts used for Firestore metadata records
    (call ``to_metadata_dict()`` instead).

    Design reference: MediaFile interface in design.md §11.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    media_type: MediaType
    # Raw bytes – populated on retrieve, required on store
    data: Optional[bytes] = None
    mime_type: str
    # File size in bytes; auto-filled from ``data`` if not provided explicitly
    size: int = 0
    metadata: MediaMetadata
    status: MediaStatus = MediaStatus.ACTIVE

    def to_metadata_dict(self) -> dict[str, Any]:
        """Serialise to a dict suitable for Firestore storage (no raw bytes)."""
        d = self.model_dump(mode="json", exclude={"data"})
        return d

    @classmethod
    def from_metadata_dict(cls, data: dict[str, Any]) -> "MediaFile":
        """Deserialise a Firestore metadata dict (no raw bytes)."""
        return cls.model_validate(data)


class QuotaInfo(BaseModel):
    """Storage quota snapshot for a user.

    Design reference: QuotaInfo interface in design.md §11.
    Requirement 22.6 – notify user when quota is exceeded.
    """

    user_id: str
    used_bytes: int = 0
    limit_bytes: int = Field(
        default=10 * 1024 * 1024 * 1024,  # 10 GiB default
        description="Configurable per-user quota limit in bytes.",
    )
    file_count: int = 0
    # Computed convenience fields
    used_mb: float = 0.0
    limit_mb: float = 0.0
    percent_used: float = 0.0

    def model_post_init(self, __context: Any) -> None:
        self.used_mb = round(self.used_bytes / (1024 * 1024), 2)
        self.limit_mb = round(self.limit_bytes / (1024 * 1024), 2)
        self.percent_used = (
            round(self.used_bytes / self.limit_bytes * 100, 2)
            if self.limit_bytes > 0
            else 0.0
        )

    @property
    def is_exceeded(self) -> bool:
        """Return True when used storage is at or above the quota limit."""
        return self.used_bytes >= self.limit_bytes


class StoredMediaRecord(BaseModel):
    """Lightweight record written to Firestore when a media file is stored.

    Collection layout:  ``media_records/{mediaId}``
    Indexed fields:     user_id, session_id, created_at_ms, status
    """

    media_id: str
    user_id: str
    session_id: str
    media_type: MediaType
    gcs_object_name: str
    mime_type: str
    size_bytes: int
    created_at_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    expires_at_ms: Optional[int] = None  # None = retain indefinitely / 90-day default
    status: MediaStatus = MediaStatus.ACTIVE
    description: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)

    def to_firestore_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_firestore_dict(cls, data: dict[str, Any]) -> "StoredMediaRecord":
        return cls.model_validate(data)
