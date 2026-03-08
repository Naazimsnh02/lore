"""FrameBuffer — maintains a sliding window of recent camera frames.

Stores the last N frames and provides quality-based frame selection
for improved recognition accuracy.

Design reference: LORE design.md, SightMode Implementation / Task 8.1.1.
Requirements: 2.2, 2.4.
"""

from __future__ import annotations

import logging
import struct
from collections import deque
from typing import Optional

from .models import BufferedFrame, FrameMetadata

logger = logging.getLogger(__name__)

# Default brightness threshold below which flash is suggested (Req 2.6)
BRIGHTNESS_THRESHOLD: float = 30.0


class FrameBuffer:
    """Circular buffer holding the most recent camera frames.

    Parameters
    ----------
    size:
        Maximum number of frames to keep (design spec: 5).
    brightness_threshold:
        Minimum average brightness (0–255) to consider lighting sufficient.
    """

    def __init__(
        self,
        size: int = 5,
        brightness_threshold: float = BRIGHTNESS_THRESHOLD,
    ) -> None:
        self._buffer: deque[BufferedFrame] = deque(maxlen=size)
        self._brightness_threshold = brightness_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, frame_bytes: bytes, mime_type: str = "image/jpeg") -> FrameMetadata:
        """Add a new camera frame to the buffer.

        Returns the computed metadata for the frame.
        """
        metadata = self._compute_metadata(frame_bytes, mime_type)
        self._buffer.append(BufferedFrame(data=frame_bytes, metadata=metadata))
        logger.debug(
            "Frame buffered: brightness=%.1f quality=%.3f buffer_size=%d",
            metadata.brightness,
            metadata.quality_score,
            len(self._buffer),
        )
        return metadata

    def get_best_frame(self) -> Optional[BufferedFrame]:
        """Return the frame with the highest quality score, or None if empty."""
        if not self._buffer:
            return None
        return max(self._buffer, key=lambda f: f.metadata.quality_score)

    def get_latest_frame(self) -> Optional[BufferedFrame]:
        """Return the most recently added frame, or None if empty."""
        if not self._buffer:
            return None
        return self._buffer[-1]

    def check_lighting(self, frame_bytes: Optional[bytes] = None) -> bool:
        """Check if the latest (or given) frame has sufficient lighting.

        Returns True if brightness >= threshold.
        """
        if frame_bytes is not None:
            brightness = self._estimate_brightness(frame_bytes)
        elif self._buffer:
            brightness = self._buffer[-1].metadata.brightness
        else:
            return True  # No frame to judge — don't block
        return brightness >= self._brightness_threshold

    def clear(self) -> None:
        """Remove all buffered frames."""
        self._buffer.clear()

    @property
    def count(self) -> int:
        """Number of frames currently buffered."""
        return len(self._buffer)

    @property
    def is_full(self) -> bool:
        """Whether the buffer has reached its maximum capacity."""
        return len(self._buffer) == (self._buffer.maxlen or 0)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _compute_metadata(self, frame_bytes: bytes, mime_type: str) -> FrameMetadata:
        """Compute quality metadata for a raw image frame.

        For a lightweight heuristic we estimate brightness from a sample of
        raw bytes.  In production this could be replaced with PIL/OpenCV for
        real JPEG decoding, but we avoid heavy dependencies here.
        """
        brightness = self._estimate_brightness(frame_bytes)
        size_score = min(len(frame_bytes) / 100_000, 1.0)  # bigger → sharper (heuristic)
        brightness_score = min(brightness / 128.0, 1.0)  # 128 is "well-lit"

        quality = 0.6 * brightness_score + 0.4 * size_score

        return FrameMetadata(
            brightness=brightness,
            quality_score=round(quality, 4),
            mime_type=mime_type,
        )

    @staticmethod
    def _estimate_brightness(data: bytes) -> float:
        """Estimate average brightness from raw image bytes.

        This samples evenly-spaced bytes from the payload and interprets them
        as unsigned 8-bit intensity values.  For JPEG data the result is a
        rough proxy — acceptable for the flash-suggestion heuristic.
        """
        if not data:
            return 0.0

        # Sample up to 1000 bytes spread across the image
        step = max(1, len(data) // 1000)
        total = 0
        count = 0
        for i in range(0, len(data), step):
            total += data[i]
            count += 1

        return total / count if count else 0.0
