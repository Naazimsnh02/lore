"""LORE WebSocket Gateway — FastAPI application entry point.

Cloud Run service providing real-time bidirectional WebSocket communication
between mobile clients and the LORE backend.

Requirements implemented here:
  - 20.1  WebSocket server on Cloud Run with persistent connections
  - 20.2  Bidirectional communication channel per client
  - 20.3  Stream documentary content as it becomes available
  - 20.4  Buffer content for up to 30 s during network interruptions
  - 20.5  Resume streaming from the buffer on reconnection
  - 20.6  Support 1 000+ concurrent connections
  - 20.7  Message processing latency < 100 ms under normal conditions
  - 25.1  Authenticate clients with Google Cloud Identity Platform
  - 25.6  Session timeout after 24 hours
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .auth import verify_token
from .connection_manager import ConnectionManager
from .message_router import MessageRouter
from .models import ClientMessage, ServerMessage

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# ── Application-wide singletons ────────────────────────────────────────────────

connection_manager = ConnectionManager()
message_router = MessageRouter(connection_manager)


# ── Lifespan (startup / shutdown) ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage background tasks that span the full application lifetime."""
    logger.info("LORE WebSocket Gateway starting up")

    # Periodic cleanup of stale connection buffers to reclaim memory
    cleanup_task = asyncio.create_task(_buffer_cleanup_loop(), name="buffer-cleanup")

    yield  # Application is running

    cleanup_task.cancel()
    logger.info("LORE WebSocket Gateway shutting down")


async def _buffer_cleanup_loop() -> None:
    """Run ``cleanup_stale_buffers`` every 60 seconds."""
    try:
        while True:
            await asyncio.sleep(60)
            connection_manager.cleanup_stale_buffers()
    except asyncio.CancelledError:
        pass


# ── FastAPI application ────────────────────────────────────────────────────────

app = FastAPI(
    title="LORE WebSocket Gateway",
    description=(
        "Real-time bidirectional WebSocket gateway for the LORE multimodal "
        "documentary application (Requirement 20)."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# ── Health / readiness probes ──────────────────────────────────────────────────

@app.get("/health", tags=["operations"], summary="Liveness probe")
async def health_check():
    """Returns 200 when the service process is running.

    Cloud Run uses this as the liveness check.
    """
    return {"status": "ok", "timestamp": int(time.time() * 1000)}


@app.get("/ready", tags=["operations"], summary="Readiness probe")
async def readiness_check():
    """Returns 200 when the service is ready to serve WebSocket traffic.

    Cloud Run uses this to gate traffic routing (min-instances logic).
    """
    return {
        "status": "ready",
        "active_connections": connection_manager.active_count(),
        "timestamp": int(time.time() * 1000),
    }


# ── WebSocket endpoint ─────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None, alias="token"),
    client_id: Optional[str] = Query(default=None, alias="client_id"),
):
    """Primary WebSocket endpoint for the mobile client.

    **Authentication** (Requirement 25.1):
      The client can provide a Firebase ID token in one of two ways:
        1. Query parameter:  ``wss://host/ws?token=<jwt>``
        2. First message of type ``"auth"`` with payload ``{"token": "<jwt>"}``.
      If no valid token is provided within 10 seconds the connection is closed
      with code 4001.

    **Reconnection** (Requirements 20.4 / 20.5):
      Pass ``?client_id=<previous-client-id>`` to resume a session and receive
      any buffered messages that arrived during the disconnect window.

    **Message loop**:
      After authentication the gateway enters a receive-validate-route loop.
      Each incoming JSON message is parsed as a ``ClientMessage`` and passed to
      ``MessageRouter.route()``.  Any synchronous responses are sent back
      immediately; asynchronous responses (e.g. from the Orchestrator) arrive
      via ``ConnectionManager.send()`` from other coroutines.
    """
    await websocket.accept()

    # ── Step 1: Obtain auth token ──────────────────────────────────────────────
    if not token:
        # Accept a single "auth" message before the main loop
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
            msg = json.loads(raw)
            if msg.get("type") == "auth":
                token = msg.get("payload", {}).get("token")
        except (asyncio.TimeoutError, json.JSONDecodeError, Exception):
            pass

    if not token:
        await websocket.close(code=4001, reason="Authentication token required")
        return

    # ── Step 2: Verify token ───────────────────────────────────────────────────
    try:
        user_claims = await verify_token(token)
    except Exception as exc:
        detail = getattr(exc, "detail", str(exc))
        logger.warning("Auth failed: %s", detail)
        await websocket.close(code=4001, reason=str(detail))
        return

    user_id: str = user_claims["user_id"]

    # ── Step 3: Register connection (and flush any buffered content) ───────────
    info = await connection_manager.connect(
        websocket,
        user_id,
        client_id=client_id,  # None → new UUID assigned
    )
    resolved_client_id = info.client_id

    logger.info(
        "WebSocket established: client_id=%s user_id=%s",
        resolved_client_id,
        user_id,
    )

    # Confirm connection to the client
    await websocket.send_text(
        json.dumps({
            "type": "connected",
            "payload": {
                "clientId": resolved_client_id,
                "userId": user_id,
                "timestamp": int(time.time() * 1000),
            },
        })
    )

    # ── Step 4: Receive / validate / route loop ────────────────────────────────
    try:
        while True:
            raw = await websocket.receive_text()
            recv_ts = time.monotonic()

            # Skip heartbeat pong responses from the client
            if raw in ('{"type":"pong"}', '{"type":"ping"}'):
                continue

            # Validate message structure
            try:
                message = ClientMessage.model_validate_json(raw)
            except ValidationError as exc:
                logger.warning(
                    "Invalid message from client %s: %s",
                    resolved_client_id,
                    exc,
                )
                await websocket.send_text(
                    json.dumps({
                        "type": "error",
                        "payload": {
                            "errorCode": "INVALID_MESSAGE",
                            "message": "Message validation failed",
                            "timestamp": int(time.time() * 1000),
                        },
                    })
                )
                continue

            # Route to handler and send synchronous responses
            responses = await message_router.route(resolved_client_id, message)
            for response in responses:
                await websocket.send_text(response.model_dump_json())

            # Monitor processing latency (Requirement 20.7: < 100 ms)
            latency_ms = (time.monotonic() - recv_ts) * 1000
            if latency_ms > 100:
                logger.warning(
                    "Latency target exceeded: %.1f ms (client=%s type=%s)",
                    latency_ms,
                    resolved_client_id,
                    message.type,
                )
            else:
                logger.debug(
                    "Message routed in %.1f ms (client=%s type=%s)",
                    latency_ms,
                    resolved_client_id,
                    message.type,
                )

    except WebSocketDisconnect:
        logger.info("Client %s disconnected gracefully", resolved_client_id)
    except Exception as exc:
        logger.error(
            "Unexpected error for client %s: %s",
            resolved_client_id,
            exc,
            exc_info=True,
        )
    finally:
        await connection_manager.disconnect(resolved_client_id)


# ── Global exception handler ───────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request, exc: Exception):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "timestamp": int(time.time() * 1000)},
    )
