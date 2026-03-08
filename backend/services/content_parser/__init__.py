"""Content Parser and Serializer service.

Implements the Documentary Content Format (DCF) grammar with bidirectional
conversion between DCF JSON strings and ContentElement objects.

Requirements: 28.1 – 28.7
Design: design.md §2 – Documentary Content Format
"""

from backend.services.content_parser.models import (
    DCFElement,
    DCFStream,
    DCFVersion,
    FactContent,
    IllustrationContent,
    NarrationContent,
    SourceCitation,
    TransitionContent,
    VideoContent,
)
from backend.services.content_parser.parser import ContentParser, ParseError
from backend.services.content_parser.serializer import ContentSerializer

__all__ = [
    "ContentParser",
    "ContentSerializer",
    "ParseError",
    "DCFElement",
    "DCFStream",
    "DCFVersion",
    "NarrationContent",
    "VideoContent",
    "IllustrationContent",
    "FactContent",
    "TransitionContent",
    "SourceCitation",
]
