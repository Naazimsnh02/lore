"""ADK-based Orchestrator for LORE documentary generation.

Design reference: LORE design.md, Section 2 – Orchestrator.
Requirements: 1.1, 2.1, 3.1, 4.1, 5.1, 13.1, 15.1, 21.1–21.5.
"""

from .models import (
    ContentElement,
    ContentElementType,
    DocumentaryRequest,
    DocumentaryStream,
    Mode,
    OrchestratorError,
    TaskFailure,
    WorkflowResult,
)
from .orchestrator import DocumentaryOrchestrator
from .stream_assembler import (
    ContentSynchronizer,
    StreamAssembler,
    StreamBuffer,
    get_element_duration,
)

__all__ = [
    "ContentElement",
    "ContentElementType",
    "ContentSynchronizer",
    "DocumentaryOrchestrator",
    "DocumentaryRequest",
    "DocumentaryStream",
    "Mode",
    "OrchestratorError",
    "StreamAssembler",
    "StreamBuffer",
    "TaskFailure",
    "WorkflowResult",
    "get_element_duration",
]
