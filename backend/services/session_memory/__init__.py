"""Session Memory Manager service for LORE.

Persists user interactions, locations, and generated content across sessions
using Google Cloud Firestore as the backing store.
"""

from .manager import SessionMemoryManager
from .models import (
    BranchNode,
    ContentRef,
    LocationVisit,
    QueryResult,
    SessionDocument,
    SessionStatus,
    UserInteraction,
)

__all__ = [
    "SessionMemoryManager",
    "BranchNode",
    "ContentRef",
    "LocationVisit",
    "QueryResult",
    "SessionDocument",
    "SessionStatus",
    "UserInteraction",
]
