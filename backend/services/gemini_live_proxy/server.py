#!/usr/bin/env python3
"""
Gemini Live API WebSocket Proxy Server for LORE VoiceMode.

Based on the official Google Cloud demo:
  docs/GoogleCloudPlatform generative-ai main gemini-multimodal-live-api.../server.py

This server:
  1. Accepts WebSocket connections from the Flutter client
  2. Reads the first message to get the service_url (and optional bearer_token)
  3. If no bearer_token, generates one via Google Cloud ADC or GEMINI_API_KEY
  4. Proxies all subsequent messages bidirectionally between client and Gemini Live API

The Flutter client sends the Gemini setup message directly — this proxy is
intentionally transparent and does NOT interpret message content.
"""

import asyncio
import json
import logging
import os
import ssl
import certifi
import websockets
from websockets.legacy.server import WebSocketServerProtocol
from websockets.legacy.protocol import WebSocketCommonProtocol
from websockets.exceptions import ConnectionClosed
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root if it exists
def _load_env():
    # 1. Check current directory
    # 2. Check up to 4 levels up (for local dev backend/services/gemini_live_proxy/server.py -> lore/.env)
    current = Path(__file__).resolve()
    for _ in range(5):
        env_path = current.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            return
        current = current.parent
        if current == current.parent: # reached root
            break
_load_env()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "WARNING"),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
# Silence the websockets library's per-frame debug output
logging.getLogger("websockets").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

WS_PORT = int(os.getenv("GEMINI_PROXY_PORT") or os.getenv("PORT") or "8090")
USE_VERTEX = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "false").lower() == "true"
GCP_PROJECT = os.getenv("GCP_PROJECT_ID", "")
VERTEX_LOCATION = os.getenv("VERTEX_AI_LOCATION", "us-central1")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Gemini Live API endpoints
_VERTEX_HOST = f"{VERTEX_LOCATION}-aiplatform.googleapis.com"
_VERTEX_SERVICE_URL = (
    f"wss://{_VERTEX_HOST}/ws/google.cloud.aiplatform.v1beta1.LlmBidiService/BidiGenerateContent"
)
_AI_STUDIO_SERVICE_URL = (
    "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)


def _get_vertex_token() -> str | None:
    """Get a Google Cloud access token via ADC."""
    try:
        import google.auth
        from google.auth.transport.requests import Request
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        if not creds.valid:
            creds.refresh(Request())
        return creds.token
    except Exception as e:
        logger.error("Failed to get ADC token: %s", e)
        return None


async def _proxy_task(
    source: WebSocketCommonProtocol,
    dest: WebSocketCommonProtocol,
    label: str,
) -> None:
    """Forward all messages from source to dest."""
    try:
        async for message in source:
            try:
                if label == "client→gemini":
                    try:
                        # Log non-binary messages for debugging
                        if isinstance(message, str):
                            data = json.loads(message)
                            # Skip media chunks to avoid log spam
                            if not (isinstance(data, dict) and "realtime_input" in data and "media_chunks" in data["realtime_input"]):
                                logger.info("Client Message: %s", json.dumps(data, indent=2))
                    except Exception:
                        pass
                await dest.send(message)
            except Exception as e:
                logger.warning("Forward error (%s): %s", label, e)
                break
    except ConnectionClosed as e:
        logger.info("Connection closed (%s): %s", label, e)
    except Exception as e:
        logger.warning("Proxy error (%s): %s", label, e)
    finally:
        try:
            await dest.close()
        except Exception:
            pass


async def _create_proxy(
    client_ws: WebSocketCommonProtocol,
    bearer_token: str,
    service_url: str,
) -> None:
    """Connect to Gemini and run bidirectional proxy."""
    # Only send Authorization header for Vertex AI — AI Studio uses key in URL
    headers = {"Content-Type": "application/json"}
    if bearer_token and bearer_token != "unused":
        headers["Authorization"] = f"Bearer {bearer_token}"

    ssl_ctx = ssl.create_default_context(cafile=certifi.where())

    logger.info("Connecting to Gemini Live API: %s", service_url[:60])
    try:
        async with websockets.connect(
            service_url,
            additional_headers=headers,
            ssl=ssl_ctx,
        ) as gemini_ws:
            logger.info("Connected to Gemini Live API")

            c2g = asyncio.create_task(_proxy_task(client_ws, gemini_ws, "client→gemini"))
            g2c = asyncio.create_task(_proxy_task(gemini_ws, client_ws, "gemini→client"))

            done, pending = await asyncio.wait(
                [c2g, g2c], return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    except ConnectionClosed as e:
        logger.warning("Gemini connection closed: %s", e)
        if not client_ws.closed:
            await client_ws.close(code=e.code, reason=e.reason)
    except Exception as e:
        logger.error("Failed to connect to Gemini: %s", e)
        if not client_ws.closed:
            await client_ws.close(code=1008, reason="Upstream connection failed")


async def _handle_client(client_ws: WebSocketServerProtocol) -> None:
    """
    Handle a new Flutter client connection.

    Expected first message (JSON):
      {
        "service_url": "wss://...",   // optional — defaults based on USE_VERTEX
        "bearer_token": "..."         // optional — generated from ADC if absent
      }
    """
    logger.info("New client connection from %s", client_ws.remote_address)
    try:
        raw = await asyncio.wait_for(client_ws.recv(), timeout=15.0)
        data = json.loads(raw)

        service_url: str = data.get("service_url", "")
        bearer_token: str = data.get("bearer_token", "")

        # Resolve service URL
        if not service_url:
            if USE_VERTEX:
                service_url = _VERTEX_SERVICE_URL
            else:
                # AI Studio — API key goes in the URL, not the header
                if not GEMINI_API_KEY:
                    logger.error("No GEMINI_API_KEY set and not using Vertex AI")
                    await client_ws.close(code=1008, reason="No API key configured")
                    return
                service_url = f"{_AI_STUDIO_SERVICE_URL}?key={GEMINI_API_KEY}"
                bearer_token = "unused"  # AI Studio uses key param, not bearer

        # Resolve bearer token (Vertex AI only)
        if not bearer_token and USE_VERTEX:
            logger.info("Generating ADC token for Vertex AI...")
            bearer_token = _get_vertex_token()
            if not bearer_token:
                await client_ws.close(code=1008, reason="Authentication failed")
                return
            logger.info("ADC token obtained")

        if not bearer_token:
            bearer_token = "unused"

        await _create_proxy(client_ws, bearer_token, service_url)

    except asyncio.TimeoutError:
        logger.warning("Timeout waiting for setup message")
        await client_ws.close(code=1008, reason="Setup timeout")
    except json.JSONDecodeError as e:
        logger.warning("Invalid JSON in setup message: %s", e)
        await client_ws.close(code=1008, reason="Invalid JSON")
    except Exception as e:
        logger.error("Client handler error: %s", e)
        if not client_ws.closed:
            await client_ws.close(code=1011, reason="Internal error")


async def main() -> None:
    mode = "Vertex AI" if USE_VERTEX else "AI Studio (API Key)"
    print(f"""
╔══════════════════════════════════════════════════════╗
║   LORE — Gemini Live API Proxy                      ║
╠══════════════════════════════════════════════════════╣
║  WebSocket: ws://localhost:{WS_PORT:<5}                    ║
║  Mode: {mode:<44} ║
╚══════════════════════════════════════════════════════╝
""")
    async with websockets.serve(_handle_client, "0.0.0.0", WS_PORT):
        logger.info("Proxy listening on ws://0.0.0.0:%d", WS_PORT)
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nProxy stopped.")
