"""Pydantic data models for Chronicle PDF export.

Requirements: 16.1 – 16.7
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ChronicleFormat(str, Enum):
    """Output format for Chronicle export."""

    PDF = "pdf"
    # Future: HTML, EPUB, etc.


class ChronicleStatus(str, Enum):
    """Status of Chronicle export operation."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ChronicleSection(BaseModel):
    """Represents a section in the Chronicle document.

    Sections correspond to branches in the documentary structure.
    """

    section_id: str
    title: str
    depth: int = Field(ge=0, le=3)  # 0 = root, max = 3
    parent_section_id: Optional[str] = None
    start_time_ms: int
    end_time_ms: Optional[int] = None
    # Content element IDs included in this section
    content_ids: list[str] = Field(default_factory=list)


class ChronicleMetadata(BaseModel):
    """Metadata for the Chronicle document."""

    title: str
    session_id: str
    user_id: str
    mode: str  # sight | voice | lore
    depth_dial: str  # explorer | scholar | expert
    language: str
    start_time_ms: int
    end_time_ms: Optional[int] = None
    total_duration_seconds: float = 0.0
    location_count: int = 0
    content_count: dict[str, int] = Field(default_factory=dict)
    # Branch structure for table of contents
    sections: list[ChronicleSection] = Field(default_factory=list)


class ChronicleExportRequest(BaseModel):
    """Request to export a session as a Chronicle PDF.

    Requirement 16.1 – provide Chronicle export functionality.
    """

    session_id: str
    user_id: str
    format: ChronicleFormat = ChronicleFormat.PDF
    # Optional customization
    include_timestamps: bool = True
    include_sources: bool = True
    include_video_thumbnails: bool = True
    include_toc: bool = True
    # Page layout options
    page_size: str = "A4"  # A4, Letter, etc.
    font_size: int = Field(default=11, ge=8, le=16)


class ChronicleExportResult(BaseModel):
    """Result of Chronicle export operation.

    Requirement 16.7 – Chronicle stored in Media Store with shareable link.
    """

    chronicle_id: str
    session_id: str
    user_id: str
    status: ChronicleStatus
    # Cloud Storage URL for the generated PDF
    storage_url: str = ""
    # Shareable signed URL with expiration
    shareable_url: str = ""
    shareable_url_expires_at_ms: Optional[int] = None
    # File metadata
    file_size_bytes: int = 0
    page_count: int = 0
    generation_time_seconds: float = 0.0
    created_at_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    # Error information if failed
    error: Optional[str] = None
    error_details: dict[str, Any] = Field(default_factory=dict)


class ChronicleContentItem(BaseModel):
    """Represents a single content item to be included in the Chronicle.

    This is an intermediate representation used during PDF generation.
    """

    sequence_id: int
    timestamp_ms: int
    content_type: str  # narration | video | illustration | fact | transition
    # Type-specific fields
    text: str = ""  # narration transcript or fact claim
    image_url: str = ""  # illustration or video thumbnail
    video_url: str = ""  # video link
    duration_seconds: Optional[float] = None
    sources: list[dict[str, str]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
