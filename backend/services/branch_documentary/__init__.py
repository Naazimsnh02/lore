"""Branch Documentary system for LORE.

Allows users to explore related sub-topics without losing the main
documentary thread.  Supports nesting up to 3 levels deep.

Design reference: LORE design.md, Branch Documentary section.
Requirements: 13.1–13.6.
"""

from .manager import BranchDocumentaryManager
from .models import (
    BranchDepthExceeded,
    BranchDocumentary,
    BranchDocumentaryContext,
    BranchDocumentaryError,
    BranchStackEntry,
    NoBranchToReturn,
)

__all__ = [
    "BranchDepthExceeded",
    "BranchDocumentary",
    "BranchDocumentaryContext",
    "BranchDocumentaryError",
    "BranchDocumentaryManager",
    "BranchStackEntry",
    "NoBranchToReturn",
]
