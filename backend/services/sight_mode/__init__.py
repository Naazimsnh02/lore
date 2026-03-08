"""SightMode handler — camera-based documentary generation.

Design reference: LORE design.md, SightMode Implementation section.
Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6.
"""

from .frame_buffer import FrameBuffer
from .handler import SightModeHandler

__all__ = ["FrameBuffer", "SightModeHandler"]
