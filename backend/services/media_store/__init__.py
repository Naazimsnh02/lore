"""Media Store Manager package.

Exports the primary manager class and key data models for external consumers.
"""

from .manager import (
    MediaNotFoundError,
    MediaStoreError,
    MediaStoreManager,
    QuotaExceededError,
)
from .models import (
    MediaFile,
    MediaMetadata,
    MediaStatus,
    MediaType,
    QuotaInfo,
    StoredMediaRecord,
)

__all__ = [
    # Manager
    "MediaStoreManager",
    # Exceptions
    "MediaStoreError",
    "MediaNotFoundError",
    "QuotaExceededError",
    # Models
    "MediaFile",
    "MediaMetadata",
    "MediaStatus",
    "MediaType",
    "QuotaInfo",
    "StoredMediaRecord",
]
