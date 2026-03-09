"""Chronicle PDF exporter implementation.

Generates illustrated PDF documents from documentary sessions using ReportLab.
Requirements: 16.1 – 16.7
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..media_store.manager import MediaStoreManager
from ..media_store.models import MediaType
from ..session_memory.manager import SessionMemoryManager
from ..session_memory.models import ContentType, SessionDocument
from .models import (
    ChronicleContentItem,
    ChronicleExportRequest,
    ChronicleExportResult,
    ChronicleFormat,
    ChronicleMetadata,
    ChronicleSection,
    ChronicleStatus,
)

logger = logging.getLogger(__name__)


class ChronicleExporter:
    """Exports documentary sessions as illustrated PDF documents.

    Requirements:
    - 16.1: Provide Chronicle export functionality
    - 16.2: Generate illustrated PDF document
    - 16.3: Include narration transcripts, illustrations, video thumbnails, sources
    - 16.4: Organize content chronologically with timestamps
    - 16.5: Include table of contents with branch structure
    - 16.6: Complete export within 30 seconds for 1-hour sessions
    - 16.7: Store in Media Store with shareable link
    """

    def __init__(
        self,
        session_memory_manager: SessionMemoryManager,
        media_store_manager: MediaStoreManager,
    ):
        """Initialize Chronicle exporter.

        Args:
            session_memory_manager: Manager for session data retrieval
            media_store_manager: Manager for storing generated PDFs
        """
        self.session_memory = session_memory_manager
        self.media_store = media_store_manager
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()

    def _setup_custom_styles(self) -> None:
        """Set up custom paragraph styles for the Chronicle."""
        # Title style
        self.styles.add(
            ParagraphStyle(
                name="ChronicleTitle",
                parent=self.styles["Heading1"],
                fontSize=24,
                textColor=colors.HexColor("#1a1a1a"),
                spaceAfter=30,
                alignment=1,  # Center
            )
        )

        # Section heading style
        self.styles.add(
            ParagraphStyle(
                name="SectionHeading",
                parent=self.styles["Heading2"],
                fontSize=16,
                textColor=colors.HexColor("#2c3e50"),
                spaceBefore=20,
                spaceAfter=12,
            )
        )

        # Timestamp style
        self.styles.add(
            ParagraphStyle(
                name="Timestamp",
                parent=self.styles["Normal"],
                fontSize=9,
                textColor=colors.HexColor("#7f8c8d"),
                spaceAfter=6,
            )
        )

        # Narration style
        self.styles.add(
            ParagraphStyle(
                name="Narration",
                parent=self.styles["Normal"],
                fontSize=11,
                leading=16,
                spaceAfter=12,
            )
        )

        # Source citation style
        self.styles.add(
            ParagraphStyle(
                name="Source",
                parent=self.styles["Normal"],
                fontSize=9,
                textColor=colors.HexColor("#3498db"),
                leftIndent=20,
                spaceAfter=6,
            )
        )

    async def export_chronicle(
        self, request: ChronicleExportRequest
    ) -> ChronicleExportResult:
        """Export a session as a Chronicle PDF.

        Requirement 16.6: Complete within 30 seconds for 1-hour sessions.

        Args:
            request: Export request with session ID and options

        Returns:
            ChronicleExportResult with storage URL and metadata

        Raises:
            ValueError: If session not found or invalid
            TimeoutError: If export exceeds 30 second timeout
        """
        start_time = time.time()
        chronicle_id = str(uuid.uuid4())

        try:
            # Set 30-second timeout (Requirement 16.6)
            result = await asyncio.wait_for(
                self._generate_chronicle(chronicle_id, request),
                timeout=30.0,
            )

            result.generation_time_seconds = time.time() - start_time
            logger.info(
                f"Chronicle {chronicle_id} generated in "
                f"{result.generation_time_seconds:.2f}s"
            )
            return result

        except asyncio.TimeoutError:
            logger.error(
                f"Chronicle {chronicle_id} generation exceeded 30s timeout"
            )
            return ChronicleExportResult(
                chronicle_id=chronicle_id,
                session_id=request.session_id,
                user_id=request.user_id,
                status=ChronicleStatus.FAILED,
                error="Export exceeded 30 second timeout",
                generation_time_seconds=time.time() - start_time,
            )

        except Exception as e:
            logger.exception(f"Chronicle {chronicle_id} generation failed: {e}")
            return ChronicleExportResult(
                chronicle_id=chronicle_id,
                session_id=request.session_id,
                user_id=request.user_id,
                status=ChronicleStatus.FAILED,
                error=str(e),
                error_details={"exception_type": type(e).__name__},
                generation_time_seconds=time.time() - start_time,
            )

    async def _generate_chronicle(
        self, chronicle_id: str, request: ChronicleExportRequest
    ) -> ChronicleExportResult:
        """Internal method to generate Chronicle PDF.

        Requirements:
        - 16.2: Generate illustrated PDF
        - 16.3: Include all content types
        - 16.4: Chronological organization
        - 16.5: Table of contents
        """
        # Load session data
        session = await self.session_memory.load_session(request.session_id)
        if not session:
            raise ValueError(f"Session {request.session_id} not found")

        # Build Chronicle metadata
        metadata = self._build_metadata(session)

        # Prepare content items
        content_items = self._prepare_content_items(session)

        # Generate PDF
        pdf_bytes = await self._generate_pdf(
            metadata, content_items, request
        )

        # Store in Media Store (Requirement 16.7)
        storage_result = await self._store_chronicle(
            chronicle_id, request.user_id, request.session_id, pdf_bytes
        )

        return ChronicleExportResult(
            chronicle_id=chronicle_id,
            session_id=request.session_id,
            user_id=request.user_id,
            status=ChronicleStatus.COMPLETED,
            storage_url=storage_result["storage_url"],
            shareable_url=storage_result["shareable_url"],
            shareable_url_expires_at_ms=storage_result["expires_at_ms"],
            file_size_bytes=len(pdf_bytes),
            page_count=storage_result.get("page_count", 0),
        )

    def _build_metadata(self, session: SessionDocument) -> ChronicleMetadata:
        """Build Chronicle metadata from session document.

        Requirement 16.5: Include branch structure for table of contents.
        """
        # Build sections from branch structure
        sections = [
            ChronicleSection(
                section_id=branch.branch_id,
                title=branch.topic,
                depth=branch.depth,
                parent_section_id=branch.parent_branch_id,
                start_time_ms=branch.start_time_ms,
                end_time_ms=branch.end_time_ms,
                content_ids=branch.content_ids,
            )
            for branch in session.branch_structure
        ]

        # Generate title
        mode_name = session.mode.value.capitalize()
        date_str = datetime.fromtimestamp(
            session.start_time_ms / 1000
        ).strftime("%B %d, %Y")
        title = f"LORE Chronicle - {mode_name} Mode - {date_str}"

        return ChronicleMetadata(
            title=title,
            session_id=session.session_id,
            user_id=session.user_id,
            mode=session.mode.value,
            depth_dial=session.depth_dial.value,
            language=session.language,
            start_time_ms=session.start_time_ms,
            end_time_ms=session.end_time_ms,
            total_duration_seconds=session.total_duration_seconds,
            location_count=len(session.locations),
            content_count={
                "narration": session.content_count.narration_segments,
                "video": session.content_count.video_clips,
                "illustration": session.content_count.illustrations,
                "fact": session.content_count.facts,
            },
            sections=sections,
        )

    def _prepare_content_items(
        self, session: SessionDocument
    ) -> list[ChronicleContentItem]:
        """Prepare content items for PDF generation.

        Requirement 16.4: Organize chronologically with timestamps.
        """
        items: list[ChronicleContentItem] = []

        # Convert content references to Chronicle items
        for idx, content_ref in enumerate(
            sorted(session.content_references, key=lambda x: x.timestamp_ms)
        ):
            item = ChronicleContentItem(
                sequence_id=idx,
                timestamp_ms=content_ref.timestamp_ms,
                content_type=content_ref.content_type.value,
                duration_seconds=content_ref.duration_seconds,
                metadata={
                    "depth_level": content_ref.metadata.depth_level.value,
                    "language": content_ref.metadata.language,
                    "emotional_tone": content_ref.metadata.emotional_tone,
                },
            )

            # Type-specific fields
            if content_ref.content_type == ContentType.NARRATION:
                # Extract transcript from storage URL or metadata
                item.text = content_ref.metadata.extra.get("transcript", "")

            elif content_ref.content_type == ContentType.VIDEO:
                item.video_url = content_ref.storage_url
                item.image_url = content_ref.metadata.extra.get(
                    "thumbnail_url", ""
                )

            elif content_ref.content_type == ContentType.ILLUSTRATION:
                item.image_url = content_ref.storage_url

            elif content_ref.content_type == ContentType.FACT:
                item.text = content_ref.metadata.extra.get("claim", "")
                # Convert sources to dict format
                item.sources = [
                    {"title": src, "url": src}
                    for src in content_ref.metadata.sources
                ]

            items.append(item)

        return items

    async def _generate_pdf(
        self,
        metadata: ChronicleMetadata,
        content_items: list[ChronicleContentItem],
        request: ChronicleExportRequest,
    ) -> bytes:
        """Generate PDF document using ReportLab.

        Requirements:
        - 16.2: Generate illustrated PDF
        - 16.3: Include all content types
        - 16.5: Table of contents
        """
        buffer = io.BytesIO()

        # Determine page size
        page_size = A4 if request.page_size == "A4" else LETTER

        # Create PDF document
        doc = SimpleDocTemplate(
            buffer,
            pagesize=page_size,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=72,
        )

        # Build story (content flow)
        story = []

        # Title page
        story.extend(self._build_title_page(metadata))
        story.append(PageBreak())

        # Table of contents (Requirement 16.5)
        if request.include_toc and metadata.sections:
            story.extend(self._build_table_of_contents(metadata))
            story.append(PageBreak())

        # Content pages (Requirement 16.3)
        for item in content_items:
            story.extend(
                self._build_content_item(item, request)
            )

        # Build PDF
        doc.build(story)

        return buffer.getvalue()

    def _build_title_page(
        self, metadata: ChronicleMetadata
    ) -> list[Any]:
        """Build title page elements."""
        elements = []

        # Title
        elements.append(
            Paragraph(metadata.title, self.styles["ChronicleTitle"])
        )
        elements.append(Spacer(1, 0.5 * inch))

        # Metadata table
        data = [
            ["Mode:", metadata.mode.capitalize()],
            ["Depth Level:", metadata.depth_dial.capitalize()],
            ["Language:", metadata.language.upper()],
            [
                "Date:",
                datetime.fromtimestamp(metadata.start_time_ms / 1000).strftime(
                    "%B %d, %Y at %I:%M %p"
                ),
            ],
            [
                "Duration:",
                f"{int(metadata.total_duration_seconds // 60)} minutes",
            ],
            ["Locations Visited:", str(metadata.location_count)],
        ]

        table = Table(data, colWidths=[2 * inch, 4 * inch])
        table.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 11),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        elements.append(table)

        return elements

    def _build_table_of_contents(
        self, metadata: ChronicleMetadata
    ) -> list[Any]:
        """Build table of contents from branch structure.

        Requirement 16.5: Include table of contents with branch structure.
        """
        elements = []

        elements.append(
            Paragraph("Table of Contents", self.styles["Heading1"])
        )
        elements.append(Spacer(1, 0.3 * inch))

        # Build hierarchical TOC
        for section in metadata.sections:
            indent = section.depth * 20
            title = f"{'  ' * section.depth}• {section.title}"

            style = ParagraphStyle(
                name=f"TOC{section.depth}",
                parent=self.styles["Normal"],
                fontSize=11 - section.depth,
                leftIndent=indent,
                spaceAfter=6,
            )

            elements.append(Paragraph(title, style))

        return elements

    def _build_content_item(
        self, item: ChronicleContentItem, request: ChronicleExportRequest
    ) -> list[Any]:
        """Build PDF elements for a single content item.

        Requirement 16.3: Include narration transcripts, illustrations,
        video thumbnails with links, and source citations.
        """
        elements = []

        # Timestamp (Requirement 16.4)
        if request.include_timestamps:
            timestamp_str = datetime.fromtimestamp(
                item.timestamp_ms / 1000
            ).strftime("%I:%M:%S %p")
            elements.append(
                Paragraph(timestamp_str, self.styles["Timestamp"])
            )

        # Content type specific rendering
        if item.content_type == "narration":
            elements.extend(self._build_narration(item))

        elif item.content_type == "video":
            elements.extend(
                self._build_video(item, request.include_video_thumbnails)
            )

        elif item.content_type == "illustration":
            elements.extend(self._build_illustration(item))

        elif item.content_type == "fact":
            elements.extend(
                self._build_fact(item, request.include_sources)
            )

        elements.append(Spacer(1, 0.2 * inch))

        return elements

    def _build_narration(self, item: ChronicleContentItem) -> list[Any]:
        """Build narration transcript element."""
        elements = []

        if item.text:
            elements.append(
                Paragraph(f"🎙️ {item.text}", self.styles["Narration"])
            )

        if item.duration_seconds:
            duration_str = f"Duration: {int(item.duration_seconds)}s"
            elements.append(
                Paragraph(duration_str, self.styles["Timestamp"])
            )

        return elements

    def _build_video(
        self, item: ChronicleContentItem, include_thumbnail: bool
    ) -> list[Any]:
        """Build video element with thumbnail and link.

        Requirement 16.3: Include video thumbnails with links.
        """
        elements = []

        # Video thumbnail (if available and requested)
        if include_thumbnail and item.image_url:
            try:
                # Note: In production, download thumbnail from URL
                # For now, add placeholder
                elements.append(
                    Paragraph("🎬 Video Clip", self.styles["SectionHeading"])
                )
            except Exception as e:
                logger.warning(f"Failed to load video thumbnail: {e}")

        # Video link
        if item.video_url:
            link_text = f'<link href="{item.video_url}">Watch Video</link>'
            elements.append(Paragraph(link_text, self.styles["Source"]))

        if item.duration_seconds:
            duration_str = f"Duration: {int(item.duration_seconds)}s"
            elements.append(
                Paragraph(duration_str, self.styles["Timestamp"])
            )

        return elements

    def _build_illustration(self, item: ChronicleContentItem) -> list[Any]:
        """Build illustration element."""
        elements = []

        if item.image_url:
            try:
                # Note: In production, download image from URL
                # For now, add placeholder
                elements.append(
                    Paragraph("🎨 Illustration", self.styles["SectionHeading"])
                )
            except Exception as e:
                logger.warning(f"Failed to load illustration: {e}")

        return elements

    def _build_fact(
        self, item: ChronicleContentItem, include_sources: bool
    ) -> list[Any]:
        """Build fact element with source citations.

        Requirement 16.3: Include source citations.
        """
        elements = []

        if item.text:
            elements.append(
                Paragraph(f"📚 {item.text}", self.styles["Narration"])
            )

        # Source citations (Requirement 16.3)
        if include_sources and item.sources:
            elements.append(
                Paragraph("Sources:", self.styles["Timestamp"])
            )
            for source in item.sources:
                source_text = f'• <link href="{source["url"]}">{source["title"]}</link>'
                elements.append(Paragraph(source_text, self.styles["Source"]))

        return elements

    async def _store_chronicle(
        self,
        chronicle_id: str,
        user_id: str,
        session_id: str,
        pdf_bytes: bytes,
    ) -> dict[str, Any]:
        """Store Chronicle PDF in Media Store.

        Requirement 16.7: Store in Media Store with shareable link.
        """
        # Store PDF
        storage_url = await self.media_store.store_media(
            media_data=pdf_bytes,
            user_id=user_id,
            session_id=session_id,
            media_type=MediaType.CHRONICLE,
            media_id=chronicle_id,
            mime_type="application/pdf",
            description=f"Chronicle export for session {session_id}",
        )

        # Generate shareable signed URL (7 days expiration)
        expires_at = datetime.now() + timedelta(days=7)
        shareable_url = await self.media_store.generate_signed_url(
            media_id=chronicle_id,
            expiration_minutes=7 * 24 * 60,  # 7 days
        )

        return {
            "storage_url": storage_url,
            "shareable_url": shareable_url,
            "expires_at_ms": int(expires_at.timestamp() * 1000),
        }
