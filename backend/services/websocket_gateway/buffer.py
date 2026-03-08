"""Per-client message buffer for network interruptions.

Requirements:
  - 20.4: Buffer documentary content for up to 30 seconds when the client
          is temporarily disconnected.
  - 20.5: Flush the buffer to the client when connectivity is restored.

Design decisions:
  - Uses asyncio.Lock to be safe across coroutines on the same event loop.
  - Implements a bounded deque (maxlen) so memory cannot grow unboundedly
    even if no expiry pruning occurs.
  - Expired entries (older than ``max_age_seconds``) are pruned lazily on
    every enqueue/flush call rather than via a background timer, keeping the
    implementation simple and free of scheduling edge-cases.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Maximum wall-clock age of a buffered message before it is discarded.
BUFFER_DURATION_SECONDS: float = 30.0

# Hard cap on the number of messages to prevent memory exhaustion.
# At ~1 kB per JSON message, 500 messages ≈ 500 kB per disconnected client.
MAX_BUFFER_SIZE: int = 500


@dataclass
class _BufferedEntry:
    """Internal wrapper keeping the payload alongside its enqueue timestamp."""

    data: Any
    enqueued_at: float = field(default_factory=time.monotonic)


class MessageBuffer:
    """FIFO buffer for a single client's outgoing messages.

    Thread/coroutine safety is provided by ``asyncio.Lock``.  All public
    methods are coroutines to make the locking contract explicit.

    Usage::

        buf = MessageBuffer("client-abc")

        # When the gateway wants to send to an offline client:
        await buf.enqueue(serialised_json_string)

        # When the client reconnects:
        pending = await buf.flush()
        for msg in pending:
            await websocket.send_text(msg)
    """

    def __init__(
        self,
        client_id: str,
        max_age_seconds: float = BUFFER_DURATION_SECONDS,
    ) -> None:
        self.client_id = client_id
        self.max_age_seconds = max_age_seconds
        self._queue: deque[_BufferedEntry] = deque(maxlen=MAX_BUFFER_SIZE)
        self._lock = asyncio.Lock()

    # ── Public coroutines ──────────────────────────────────────────────────────

    async def enqueue(self, data: Any) -> None:
        """Add *data* to the buffer.

        If the buffer is full the oldest entry is silently dropped to make
        room (overflow handling — Requirement 20.4).  Expired entries are
        pruned first to reclaim space before dropping live messages.
        """
        async with self._lock:
            self._prune_expired()

            if len(self._queue) >= MAX_BUFFER_SIZE:
                dropped = self._queue.popleft()
                age = time.monotonic() - dropped.enqueued_at
                logger.warning(
                    "Buffer overflow for client %s — dropped message aged %.1fs",
                    self.client_id,
                    age,
                )

            self._queue.append(_BufferedEntry(data=data))

    async def flush(self) -> list[Any]:
        """Return all non-expired buffered messages and clear the queue.

        Called when the client reconnects (Requirement 20.5).

        Returns:
            Ordered list of buffered payloads, oldest first.
        """
        async with self._lock:
            self._prune_expired()
            messages = [entry.data for entry in self._queue]
            self._queue.clear()

        if messages:
            logger.info(
                "Flushed %d buffered messages for client %s",
                len(messages),
                self.client_id,
            )
        return messages

    async def peek_size(self) -> int:
        """Return the current number of buffered (non-expired) entries."""
        async with self._lock:
            self._prune_expired()
            return len(self._queue)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _prune_expired(self) -> None:
        """Remove all entries older than ``max_age_seconds`` from the front.

        Must be called while ``self._lock`` is held.
        """
        cutoff = time.monotonic() - self.max_age_seconds
        pruned = 0
        while self._queue and self._queue[0].enqueued_at < cutoff:
            self._queue.popleft()
            pruned += 1
        if pruned:
            logger.debug(
                "Pruned %d expired buffer entries for client %s",
                pruned,
                self.client_id,
            )

    def __repr__(self) -> str:
        return (
            f"MessageBuffer(client_id={self.client_id!r}, "
            f"size={len(self._queue)}, "
            f"max_age={self.max_age_seconds}s)"
        )
