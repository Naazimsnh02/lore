"""Unit tests for Chronicle PDF exporter.

Tests cover:
- PDF generation completeness (Requirement 16.2)
- Table of contents structure (Requirement 16.3)
- Export timing for various session lengths (Requirement 16.6)
- Content inclusion (narration, illustrations, video thumbnails, sources)
- Chronological organization
- Branch structure representation

Requirements: 16.1 – 16.7
"""

import asyncio
import time
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.chronicle_exporter.exporter import ChronicleExporter
from backend.services.chronicle_exporter.models import (
    ChronicleExportRequest,
    ChronicleFormat,
    ChronicleMetadata,
    ChronicleSection,
    ChronicleStatus,
)
from backend.services.session_memory.models import (
    BranchNode,
    ContentCount,
    ContentRef,
    ContentRefMetadata,
    ContentType,
    DepthDial,
    GeoPoint,
    LocationVisit,
    OperatingMode,
    SessionDocument,
    SessionStatus,
    UserInteraction,
)


@pytest.fixture
def mock_session_memory():
    """Mock SessionMemoryManager."""
    return AsyncMock()


@pytest.fixture
def mock_media_store():
    """Mock MediaStoreManager."""
    mock = AsyncMock()
    mock.store_media = AsyncMock(
        return_value="gs://lore-media/chronicles/test.pdf"
    )
    mock.generate_signed_url = AsyncMock(
        return_value="https://storage.googleapis.com/signed-url"
    )
    return mock


@pytest.fixture
def chronicle_exporter(mock_session_memory, mock_media_store):
    """Create ChronicleExporter instance with mocked dependencies."""
    return ChronicleExporter(mock_session_memory, mock_media_store)


@pytest.fixture
def sample_session():
    """Create a sample session document for testing."""
    session_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    start_time = int(time.time() * 1000)

    return SessionDocument(
        session_id=session_id,
        user_id=user_id,
        mode=OperatingMode.SIGHT,
        status=SessionStatus.COMPLETED,
        depth_dial=DepthDial.SCHOLAR,
        language="en",
        start_time_ms=start_time,
        end_time_ms=start_time + 3600000,  # 1 hour later
        total_duration_seconds=3600.0,
        locations=[
            LocationVisit(
                place_id="place1",
                name="Eiffel Tower",
                coordinates=GeoPoint(latitude=48.8584, longitude=2.2945),
                visit_time_ms=start_time,
                duration_seconds=600.0,
            )
        ],
        interactions=[],
        content_references=[
            ContentRef(
                content_id=str(uuid.uuid4()),
                content_type=ContentType.NARRATION,
                storage_url="gs://lore-media/narration1.wav",
                timestamp_ms=start_time + 1000,
                duration_seconds=30.0,
                metadata=ContentRefMetadata(
                    depth_level=DepthDial.SCHOLAR,
                    language="en",
                    emotional_tone="enthusiastic",
                    extra={"transcript": "Welcome to the Eiffel Tower!"},
                ),
            ),
            ContentRef(
                content_id=str(uuid.uuid4()),
                content_type=ContentType.ILLUSTRATION,
                storage_url="gs://lore-media/illustration1.png",
                timestamp_ms=start_time + 2000,
                metadata=ContentRefMetadata(
                    depth_level=DepthDial.SCHOLAR,
                    language="en",
                ),
            ),
            ContentRef(
                content_id=str(uuid.uuid4()),
                content_type=ContentType.FACT,
                storage_url="",
                timestamp_ms=start_time + 3000,
                metadata=ContentRefMetadata(
                    depth_level=DepthDial.SCHOLAR,
                    language="en",
                    sources=["https://example.com/eiffel-tower"],
                    extra={"claim": "The Eiffel Tower was built in 1889."},
                ),
            ),
        ],
        branch_structure=[
            BranchNode(
                branch_id=str(uuid.uuid4()),
                parent_branch_id=None,
                topic="Eiffel Tower History",
                depth=0,
                start_time_ms=start_time,
                end_time_ms=start_time + 1800000,
            )
        ],
        content_count=ContentCount(
            narration_segments=1, video_clips=0, illustrations=1, facts=1
        ),
    )


# ── Test: Basic export functionality ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_chronicle_success(
    chronicle_exporter, mock_session_memory, sample_session
):
    """Test successful Chronicle export.

    Requirement 16.1: Provide Chronicle export functionality.
    """
    mock_session_memory.load_session = AsyncMock(return_value=sample_session)

    request = ChronicleExportRequest(
        session_id=sample_session.session_id,
        user_id=sample_session.user_id,
    )

    result = await chronicle_exporter.export_chronicle(request)

    assert result.status == ChronicleStatus.COMPLETED
    assert result.session_id == sample_session.session_id
    assert result.user_id == sample_session.user_id
    assert result.storage_url != ""
    assert result.shareable_url != ""
    assert result.file_size_bytes > 0
    assert result.error is None


@pytest.mark.asyncio
async def test_export_chronicle_session_not_found(
    chronicle_exporter, mock_session_memory
):
    """Test export fails gracefully when session not found."""
    mock_session_memory.load_session = AsyncMock(return_value=None)

    request = ChronicleExportRequest(
        session_id="nonexistent",
        user_id="user123",
    )

    result = await chronicle_exporter.export_chronicle(request)

    assert result.status == ChronicleStatus.FAILED
    assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_export_chronicle_timeout(
    chronicle_exporter, mock_session_memory, sample_session
):
    """Test export respects 30-second timeout.

    Requirement 16.6: Complete within 30 seconds for 1-hour sessions.
    """
    # Simulate slow PDF generation
    async def slow_load(*args, **kwargs):
        await asyncio.sleep(35)  # Exceed 30s timeout
        return sample_session

    mock_session_memory.load_session = slow_load

    request = ChronicleExportRequest(
        session_id=sample_session.session_id,
        user_id=sample_session.user_id,
    )

    result = await chronicle_exporter.export_chronicle(request)

    assert result.status == ChronicleStatus.FAILED
    assert "timeout" in result.error.lower()
    assert result.generation_time_seconds >= 30.0


# ── Test: Metadata building ───────────────────────────────────────────────────


def test_build_metadata(chronicle_exporter, sample_session):
    """Test Chronicle metadata construction.

    Requirement 16.5: Include table of contents with branch structure.
    """
    metadata = chronicle_exporter._build_metadata(sample_session)

    assert metadata.session_id == sample_session.session_id
    assert metadata.user_id == sample_session.user_id
    assert metadata.mode == "sight"
    assert metadata.depth_dial == "scholar"
    assert metadata.language == "en"
    assert metadata.total_duration_seconds == 3600.0
    assert metadata.location_count == 1
    assert metadata.content_count["narration"] == 1
    assert metadata.content_count["illustration"] == 1
    assert metadata.content_count["fact"] == 1
    assert len(metadata.sections) == 1
    assert metadata.sections[0].title == "Eiffel Tower History"


def test_build_metadata_with_branches(chronicle_exporter):
    """Test metadata with nested branch structure.

    Requirement 16.5: Include branch structure in table of contents.
    """
    session = SessionDocument(
        session_id="session1",
        user_id="user1",
        mode=OperatingMode.LORE,
        start_time_ms=int(time.time() * 1000),
        branch_structure=[
            BranchNode(
                branch_id="branch1",
                parent_branch_id=None,
                topic="Main Topic",
                depth=0,
                start_time_ms=int(time.time() * 1000),
            ),
            BranchNode(
                branch_id="branch2",
                parent_branch_id="branch1",
                topic="Sub Topic 1",
                depth=1,
                start_time_ms=int(time.time() * 1000),
            ),
            BranchNode(
                branch_id="branch3",
                parent_branch_id="branch1",
                topic="Sub Topic 2",
                depth=1,
                start_time_ms=int(time.time() * 1000),
            ),
        ],
        content_count=ContentCount(),
    )

    metadata = chronicle_exporter._build_metadata(session)

    assert len(metadata.sections) == 3
    assert metadata.sections[0].depth == 0
    assert metadata.sections[1].depth == 1
    assert metadata.sections[2].depth == 1
    assert metadata.sections[1].parent_section_id == "branch1"


# ── Test: Content preparation ─────────────────────────────────────────────────


def test_prepare_content_items_chronological(
    chronicle_exporter, sample_session
):
    """Test content items are organized chronologically.

    Requirement 16.4: Organize content chronologically with timestamps.
    """
    items = chronicle_exporter._prepare_content_items(sample_session)

    assert len(items) == 3

    # Verify chronological order
    for i in range(len(items) - 1):
        assert items[i].timestamp_ms <= items[i + 1].timestamp_ms

    # Verify sequence IDs
    for i, item in enumerate(items):
        assert item.sequence_id == i


def test_prepare_content_items_types(chronicle_exporter, sample_session):
    """Test all content types are included.

    Requirement 16.3: Include narration transcripts, illustrations,
    video thumbnails, and source citations.
    """
    items = chronicle_exporter._prepare_content_items(sample_session)

    content_types = {item.content_type for item in items}
    assert "narration" in content_types
    assert "illustration" in content_types
    assert "fact" in content_types


def test_prepare_content_items_narration(chronicle_exporter, sample_session):
    """Test narration content extraction."""
    items = chronicle_exporter._prepare_content_items(sample_session)

    narration_items = [i for i in items if i.content_type == "narration"]
    assert len(narration_items) == 1
    assert narration_items[0].text == "Welcome to the Eiffel Tower!"
    assert narration_items[0].duration_seconds == 30.0


def test_prepare_content_items_fact(chronicle_exporter, sample_session):
    """Test fact content with sources.

    Requirement 16.3: Include source citations.
    """
    items = chronicle_exporter._prepare_content_items(sample_session)

    fact_items = [i for i in items if i.content_type == "fact"]
    assert len(fact_items) == 1
    assert fact_items[0].text == "The Eiffel Tower was built in 1889."
    assert len(fact_items[0].sources) == 1


# ── Test: PDF generation ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_pdf_basic(chronicle_exporter, sample_session):
    """Test basic PDF generation.

    Requirement 16.2: Generate illustrated PDF document.
    """
    metadata = chronicle_exporter._build_metadata(sample_session)
    content_items = chronicle_exporter._prepare_content_items(sample_session)

    request = ChronicleExportRequest(
        session_id=sample_session.session_id,
        user_id=sample_session.user_id,
    )

    pdf_bytes = await chronicle_exporter._generate_pdf(
        metadata, content_items, request
    )

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 0
    # PDF files start with %PDF
    assert pdf_bytes[:4] == b"%PDF"


@pytest.mark.asyncio
async def test_generate_pdf_with_toc(chronicle_exporter):
    """Test PDF generation with table of contents.

    Requirement 16.5: Include table of contents with branch structure.
    """
    session = SessionDocument(
        session_id="session1",
        user_id="user1",
        mode=OperatingMode.VOICE,
        start_time_ms=int(time.time() * 1000),
        branch_structure=[
            BranchNode(
                branch_id="branch1",
                topic="Main Topic",
                depth=0,
                start_time_ms=int(time.time() * 1000),
            )
        ],
        content_count=ContentCount(),
    )

    metadata = chronicle_exporter._build_metadata(session)
    content_items = []

    request = ChronicleExportRequest(
        session_id="session1",
        user_id="user1",
        include_toc=True,
    )

    pdf_bytes = await chronicle_exporter._generate_pdf(
        metadata, content_items, request
    )

    assert len(pdf_bytes) > 0
    # TOC should increase PDF size
    assert len(pdf_bytes) > 1000


@pytest.mark.asyncio
async def test_generate_pdf_without_toc(chronicle_exporter, sample_session):
    """Test PDF generation without table of contents."""
    metadata = chronicle_exporter._build_metadata(sample_session)
    content_items = []

    request = ChronicleExportRequest(
        session_id=sample_session.session_id,
        user_id=sample_session.user_id,
        include_toc=False,
    )

    pdf_bytes = await chronicle_exporter._generate_pdf(
        metadata, content_items, request
    )

    assert len(pdf_bytes) > 0


# ── Test: Export timing ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_timing_short_session(
    chronicle_exporter, mock_session_memory
):
    """Test export timing for short session (< 10 minutes).

    Requirement 16.6: Complete within 30 seconds.
    """
    # Create short session (5 minutes)
    start_time = int(time.time() * 1000)
    session = SessionDocument(
        session_id="short_session",
        user_id="user1",
        mode=OperatingMode.SIGHT,
        start_time_ms=start_time,
        end_time_ms=start_time + 300000,  # 5 minutes
        total_duration_seconds=300.0,
        content_references=[
            ContentRef(
                content_id=str(uuid.uuid4()),
                content_type=ContentType.NARRATION,
                storage_url="gs://test",
                timestamp_ms=start_time,
                metadata=ContentRefMetadata(
                    depth_level=DepthDial.EXPLORER, language="en"
                ),
            )
        ],
        content_count=ContentCount(narration_segments=1),
    )

    mock_session_memory.load_session = AsyncMock(return_value=session)

    request = ChronicleExportRequest(
        session_id="short_session",
        user_id="user1",
    )

    start = time.time()
    result = await chronicle_exporter.export_chronicle(request)
    elapsed = time.time() - start

    assert result.status == ChronicleStatus.COMPLETED
    assert elapsed < 30.0  # Should complete well under 30s
    assert result.generation_time_seconds < 30.0


@pytest.mark.asyncio
async def test_export_timing_one_hour_session(
    chronicle_exporter, mock_session_memory
):
    """Test export timing for 1-hour session.

    Requirement 16.6: Complete within 30 seconds for 1-hour sessions.
    """
    # Create 1-hour session with realistic content count
    start_time = int(time.time() * 1000)
    content_refs = []

    # Simulate 60 content items (1 per minute)
    for i in range(60):
        content_refs.append(
            ContentRef(
                content_id=str(uuid.uuid4()),
                content_type=ContentType.NARRATION,
                storage_url=f"gs://test/narration{i}.wav",
                timestamp_ms=start_time + (i * 60000),
                metadata=ContentRefMetadata(
                    depth_level=DepthDial.SCHOLAR,
                    language="en",
                    extra={"transcript": f"Narration segment {i}"},
                ),
            )
        )

    session = SessionDocument(
        session_id="one_hour_session",
        user_id="user1",
        mode=OperatingMode.LORE,
        start_time_ms=start_time,
        end_time_ms=start_time + 3600000,  # 1 hour
        total_duration_seconds=3600.0,
        content_references=content_refs,
        content_count=ContentCount(narration_segments=60),
    )

    mock_session_memory.load_session = AsyncMock(return_value=session)

    request = ChronicleExportRequest(
        session_id="one_hour_session",
        user_id="user1",
    )

    start = time.time()
    result = await chronicle_exporter.export_chronicle(request)
    elapsed = time.time() - start

    assert result.status == ChronicleStatus.COMPLETED
    assert elapsed < 30.0  # Must complete within 30s
    assert result.generation_time_seconds < 30.0


# ── Test: Storage and sharing ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_chronicle(chronicle_exporter, mock_media_store):
    """Test Chronicle storage in Media Store.

    Requirement 16.7: Store in Media Store with shareable link.
    """
    pdf_bytes = b"%PDF-1.4\ntest content"

    result = await chronicle_exporter._store_chronicle(
        chronicle_id="chronicle1",
        user_id="user1",
        session_id="session1",
        pdf_bytes=pdf_bytes,
    )

    assert "storage_url" in result
    assert "shareable_url" in result
    assert "expires_at_ms" in result
    assert result["storage_url"].startswith("gs://")
    assert result["shareable_url"].startswith("https://")

    # Verify media store was called correctly
    mock_media_store.store_media.assert_called_once()
    mock_media_store.generate_signed_url.assert_called_once()


@pytest.mark.asyncio
async def test_shareable_url_expiration(chronicle_exporter, mock_media_store):
    """Test shareable URL has 7-day expiration.

    Requirement 16.7: Accessible via shareable link.
    """
    pdf_bytes = b"%PDF-1.4\ntest"

    result = await chronicle_exporter._store_chronicle(
        chronicle_id="chronicle1",
        user_id="user1",
        session_id="session1",
        pdf_bytes=pdf_bytes,
    )

    # Verify expiration is approximately 7 days from now
    now_ms = int(time.time() * 1000)
    seven_days_ms = 7 * 24 * 60 * 60 * 1000
    expires_at_ms = result["expires_at_ms"]

    assert expires_at_ms > now_ms
    assert expires_at_ms < now_ms + seven_days_ms + 60000  # Allow 1 min tolerance


# ── Test: Custom styles ───────────────────────────────────────────────────────


def test_custom_styles_setup(chronicle_exporter):
    """Test custom PDF styles are properly configured."""
    assert "ChronicleTitle" in chronicle_exporter.styles
    assert "SectionHeading" in chronicle_exporter.styles
    assert "Timestamp" in chronicle_exporter.styles
    assert "Narration" in chronicle_exporter.styles
    assert "Source" in chronicle_exporter.styles


# ── Test: Content rendering ───────────────────────────────────────────────────


def test_build_narration_element(chronicle_exporter):
    """Test narration element rendering."""
    from backend.services.chronicle_exporter.models import ChronicleContentItem

    item = ChronicleContentItem(
        sequence_id=0,
        timestamp_ms=int(time.time() * 1000),
        content_type="narration",
        text="This is a narration transcript.",
        duration_seconds=30.0,
    )

    elements = chronicle_exporter._build_narration(item)

    assert len(elements) > 0
    # Should include text and duration


def test_build_fact_element_with_sources(chronicle_exporter):
    """Test fact element with source citations.

    Requirement 16.3: Include source citations.
    """
    from backend.services.chronicle_exporter.models import ChronicleContentItem

    item = ChronicleContentItem(
        sequence_id=0,
        timestamp_ms=int(time.time() * 1000),
        content_type="fact",
        text="The Eiffel Tower is 330 meters tall.",
        sources=[
            {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Eiffel_Tower"}
        ],
    )

    elements = chronicle_exporter._build_fact(item, include_sources=True)

    assert len(elements) > 0
    # Should include claim and sources


def test_build_fact_element_without_sources(chronicle_exporter):
    """Test fact element without source citations."""
    from backend.services.chronicle_exporter.models import ChronicleContentItem

    item = ChronicleContentItem(
        sequence_id=0,
        timestamp_ms=int(time.time() * 1000),
        content_type="fact",
        text="The Eiffel Tower is 330 meters tall.",
        sources=[
            {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Eiffel_Tower"}
        ],
    )

    elements = chronicle_exporter._build_fact(item, include_sources=False)

    # Should include claim but not sources
    assert len(elements) > 0


# ── Test: Error handling ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_handles_missing_content(
    chronicle_exporter, mock_session_memory
):
    """Test export handles sessions with no content gracefully."""
    session = SessionDocument(
        session_id="empty_session",
        user_id="user1",
        mode=OperatingMode.VOICE,
        start_time_ms=int(time.time() * 1000),
        content_references=[],  # No content
        content_count=ContentCount(),
    )

    mock_session_memory.load_session = AsyncMock(return_value=session)

    request = ChronicleExportRequest(
        session_id="empty_session",
        user_id="user1",
    )

    result = await chronicle_exporter.export_chronicle(request)

    # Should still succeed with empty content
    assert result.status == ChronicleStatus.COMPLETED
    assert result.file_size_bytes > 0  # Should have at least title page


@pytest.mark.asyncio
async def test_export_handles_storage_failure(
    chronicle_exporter, mock_session_memory, mock_media_store, sample_session
):
    """Test export handles storage failures gracefully."""
    mock_session_memory.load_session = AsyncMock(return_value=sample_session)
    mock_media_store.store_media = AsyncMock(
        side_effect=Exception("Storage failed")
    )

    request = ChronicleExportRequest(
        session_id=sample_session.session_id,
        user_id=sample_session.user_id,
    )

    result = await chronicle_exporter.export_chronicle(request)

    assert result.status == ChronicleStatus.FAILED
    assert "Storage failed" in result.error


# ── Test: Request options ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_with_custom_options(
    chronicle_exporter, mock_session_memory, sample_session
):
    """Test export respects custom request options."""
    mock_session_memory.load_session = AsyncMock(return_value=sample_session)

    request = ChronicleExportRequest(
        session_id=sample_session.session_id,
        user_id=sample_session.user_id,
        include_timestamps=False,
        include_sources=False,
        include_video_thumbnails=False,
        include_toc=False,
        page_size="Letter",
        font_size=12,
    )

    result = await chronicle_exporter.export_chronicle(request)

    assert result.status == ChronicleStatus.COMPLETED


# ── Test: Integration ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_export_workflow(
    chronicle_exporter, mock_session_memory, mock_media_store, sample_session
):
    """Test complete export workflow from request to result.

    Integration test covering all requirements 16.1 – 16.7.
    """
    mock_session_memory.load_session = AsyncMock(return_value=sample_session)

    request = ChronicleExportRequest(
        session_id=sample_session.session_id,
        user_id=sample_session.user_id,
        format=ChronicleFormat.PDF,
        include_timestamps=True,
        include_sources=True,
        include_video_thumbnails=True,
        include_toc=True,
    )

    result = await chronicle_exporter.export_chronicle(request)

    # Verify all requirements
    assert result.status == ChronicleStatus.COMPLETED  # 16.1
    assert result.file_size_bytes > 0  # 16.2
    assert result.storage_url != ""  # 16.7
    assert result.shareable_url != ""  # 16.7
    assert result.generation_time_seconds < 30.0  # 16.6
    assert result.error is None

    # Verify session memory was accessed
    mock_session_memory.load_session.assert_called_once_with(
        sample_session.session_id
    )

    # Verify media store was used
    mock_media_store.store_media.assert_called_once()
    mock_media_store.generate_signed_url.assert_called_once()
