"""WebSocket connection registry.

Responsibilities:
  - Track all active WebSocket connections (client_id → WebSocket).
  - Maintain per-connection metadata (user_id, mode, depth dial, language).
  - Keep per-client message buffers for network interruptions (30 s window).
  - Flush buffered messages on client reconnection.
  - Send periodic heartbeat pings to detect dead connections early.
  - Support 1 000+ concurrent connections (Requirement 20.6).

Thread / coroutine safety: all mutable state is guarded by ``asyncio.Lock``.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import WebSocket

from .buffer import MessageBuffer
from .models import ConnectionInfo, DepthDial, OperatingMode, ServerMessage

logger = logging.getLogger(__name__)

# Seconds between heartbeat pings.  Should be shorter than any load-balancer
# idle-connection timeout (Cloud Run default is 3 600 s, so 20 s is safe).
_HEARTBEAT_INTERVAL: float = 20.0

# Seconds after last contact before a disconnected client's buffer/metadata
# is eligible for cleanup.
_STALE_THRESHOLD: float = 60.0


class ConnectionManager:
    """Registry of active and recently disconnected WebSocket clients."""

    def __init__(self) -> None:
        # Primary connection store: client_id → live WebSocket
        self._connections: Dict[str, WebSocket] = {}
        # Per-connection metadata
        self._info: Dict[str, ConnectionInfo] = {}
        # Per-client outbound buffers (survive temporary disconnects)
        self._buffers: Dict[str, MessageBuffer] = {}
        # Per-client BranchDocumentaryManager instances (Task 23)
        self._branch_managers: Dict[str, "Any"] = {}
        # Background heartbeat tasks
        self._heartbeat_tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    # ── Connection lifecycle ───────────────────────────────────────────────────

    async def connect(
        self,
        websocket: WebSocket,
        user_id: str,
        client_id: Optional[str] = None,
    ) -> ConnectionInfo:
        """Register a new or reconnecting WebSocket connection.

        Args:
            websocket:  Accepted FastAPI WebSocket instance.
            user_id:    Verified user ID from the auth token.
            client_id:  Optional stable client ID from a previous session;
                        enables reconnection buffer flush.

        Returns:
            ``ConnectionInfo`` for the registered connection.
        """
        if client_id is None:
            client_id = str(uuid.uuid4())

        now = time.time()
        info = ConnectionInfo(
            client_id=client_id,
            user_id=user_id,
            connected_at=now,
            last_seen=now,
        )

        async with self._lock:
            self._connections[client_id] = websocket
            self._info[client_id] = info
            # Ensure a buffer exists (may have been created during a prior disconnect)
            if client_id not in self._buffers:
                self._buffers[client_id] = MessageBuffer(client_id)

        logger.info(
            "Client connected: client_id=%s user_id=%s total_active=%d",
            client_id,
            user_id,
            len(self._connections),
        )

        # Replay any content that arrived while the client was offline (Req 20.5)
        await self._flush_buffer(client_id, websocket)

        # Heartbeat to detect dead connections without waiting for send errors
        task = asyncio.create_task(
            self._heartbeat_loop(client_id),
            name=f"heartbeat-{client_id}",
        )
        async with self._lock:
            self._heartbeat_tasks[client_id] = task

        return info

    async def disconnect(self, client_id: str) -> None:
        """Deregister a connection while keeping buffer/metadata for reconnects.

        The buffer is retained for ``BUFFER_DURATION_SECONDS`` (30 s) to
        support the reconnection flush flow (Requirement 20.5).
        """
        async with self._lock:
            self._connections.pop(client_id, None)
            task = self._heartbeat_tasks.pop(client_id, None)
            if task:
                task.cancel()
            # Update last_seen so stale-cleanup timer can fire correctly
            if client_id in self._info:
                self._info[client_id].last_seen = time.time()

        logger.info(
            "Client disconnected: client_id=%s total_active=%d",
            client_id,
            len(self._connections),
        )

    # ── Messaging ──────────────────────────────────────────────────────────────

    async def send(self, client_id: str, message: ServerMessage) -> None:
        """Send a message to a specific client.

        If the client is currently offline the message is buffered for up to
        30 seconds (Requirement 20.4).  If the send fails due to a broken
        connection the client is removed and the message is buffered.
        """
        data = message.model_dump_json()

        async with self._lock:
            websocket = self._connections.get(client_id)

        if websocket is not None:
            try:
                await websocket.send_text(data)
                async with self._lock:
                    if client_id in self._info:
                        self._info[client_id].last_seen = time.time()
                return
            except Exception as exc:
                logger.warning(
                    "Send failed for client %s (%s) — buffering message",
                    client_id,
                    exc,
                )
                # Remove the broken websocket so future sends go straight to buffer
                async with self._lock:
                    self._connections.pop(client_id, None)

        # Client offline — buffer the message (Requirement 20.4)
        buf = await self._get_or_create_buffer(client_id)
        await buf.enqueue(data)
        logger.debug(
            "Buffered message for offline client %s (buffer_size=%d)",
            client_id,
            await buf.peek_size(),
        )

    async def broadcast(self, message: ServerMessage) -> None:
        """Send a message to all currently active clients."""
        async with self._lock:
            client_ids = list(self._connections.keys())

        await asyncio.gather(
            *(self.send(cid, message) for cid in client_ids),
            return_exceptions=True,
        )

    # ── State accessors / mutators ─────────────────────────────────────────────

    def get_connection_info(self, client_id: str) -> Optional[ConnectionInfo]:
        """Return metadata for a connection (active or recently disconnected)."""
        return self._info.get(client_id)

    def update_mode(self, client_id: str, mode: OperatingMode) -> None:
        if client_id in self._info:
            self._info[client_id].mode = mode

    def update_depth_dial(self, client_id: str, depth_dial: DepthDial) -> None:
        if client_id in self._info:
            self._info[client_id].depth_dial = depth_dial

    def update_language(self, client_id: str, language: str) -> None:
        if client_id in self._info:
            self._info[client_id].language = language

    def update_session(self, client_id: str, session_id: str) -> None:
        if client_id in self._info:
            self._info[client_id].session_id = session_id

    def set_branch_manager(self, client_id: str, manager: "Any") -> None:
        """Attach a BranchDocumentaryManager to a client connection (Task 23)."""
        self._branch_managers[client_id] = manager

    def get_branch_manager(self, client_id: str) -> "Optional[Any]":
        """Return the BranchDocumentaryManager for a client, or None."""
        return self._branch_managers.get(client_id)

    def active_count(self) -> int:
        """Return the number of live WebSocket connections."""
        return len(self._connections)

    # ── Housekeeping ───────────────────────────────────────────────────────────

    def cleanup_stale_buffers(self) -> None:
        """Evict metadata and buffers for clients not seen recently.

        Called periodically from the application lifespan background task.
        """
        cutoff = time.time() - _STALE_THRESHOLD
        stale = [
            cid
            for cid, info in self._info.items()
            if cid not in self._connections and info.last_seen < cutoff
        ]
        for cid in stale:
            self._buffers.pop(cid, None)
            self._info.pop(cid, None)
            self._branch_managers.pop(cid, None)

        if stale:
            logger.debug("Evicted %d stale client records", len(stale))

    # ── Private helpers ────────────────────────────────────────────────────────

    async def _flush_buffer(self, client_id: str, websocket: WebSocket) -> None:
        """Replay buffered messages to a newly (re)connected client."""
        buffer = self._buffers.get(client_id)
        if buffer is None:
            return

        messages = await buffer.flush()
        if not messages:
            return

        logger.info(
            "Replaying %d buffered messages to reconnected client %s",
            len(messages),
            client_id,
        )
        for msg in messages:
            try:
                await websocket.send_text(msg)
            except Exception as exc:
                logger.warning(
                    "Failed to replay buffer message to %s: %s", client_id, exc
                )
                # Stop replaying if the connection drops again
                break

    async def _get_or_create_buffer(self, client_id: str) -> MessageBuffer:
        """Return existing buffer or create a new one (coroutine-safe)."""
        async with self._lock:
            if client_id not in self._buffers:
                self._buffers[client_id] = MessageBuffer(client_id)
            return self._buffers[client_id]

    async def _heartbeat_loop(self, client_id: str) -> None:
        """Send periodic pings to detect dead connections before the OS does.

        A failed ping triggers ``disconnect`` which removes the socket and
        allows future sends to buffer instead of hanging.
        """
        try:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL)

                async with self._lock:
                    websocket = self._connections.get(client_id)
                if websocket is None:
                    break  # Connection already removed

                try:
                    await websocket.send_text('{"type":"ping"}')
                except Exception:
                    logger.info("Heartbeat failed for client %s — marking disconnected", client_id)
                    await self.disconnect(client_id)
                    break

        except asyncio.CancelledError:
            pass  # Normal shutdown
