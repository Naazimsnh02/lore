#!/usr/bin/env python3
"""
Lightweight HTTP server for Gemini image generation.
Called by the Flutter voice mode when the generate_image tool is triggered.

POST /generate
  Body: {"prompt": "..."}
  Response: {"image_base64": "...", "mime_type": "image/png"}

Run: python backend/services/nano_illustrator/image_server.py
"""
import asyncio
import base64
import json
import logging
import os
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv

# Load .env from project root if it exists
def _load_env():
    current = Path(__file__).resolve()
    for _ in range(5):
        env_path = current.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            return
        current = current.parent
        if current == current.parent:
            break
_load_env()

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# Prefer GEMINI_API_KEY explicitly — avoid the GOOGLE_API_KEY conflict
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Remove GOOGLE_API_KEY from env so the SDK doesn't override our explicit key
os.environ.pop("GOOGLE_API_KEY", None)
USE_VERTEX = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "false").lower() == "true"
GCP_PROJECT = os.getenv("GCP_PROJECT_ID", "")
VERTEX_LOCATION = os.getenv("VERTEX_AI_LOCATION", "us-central1")
MODEL_ID = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image-preview")
PORT = int(os.getenv("IMAGE_SERVER_PORT") or os.getenv("PORT") or "8091")


def _make_client():
    from google import genai
    if USE_VERTEX:
        # Image generation models require location=global on Vertex AI
        return genai.Client(vertexai=True, project=GCP_PROJECT, location="global")
    return genai.Client(api_key=GEMINI_API_KEY)


async def handle_generate(request: web.Request) -> web.Response:
    # CORS headers so any origin (Flutter HTTP client) can reach us
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }

    if request.method == "OPTIONS":
        return web.Response(status=204, headers=headers)

    try:
        body = await request.json()
        prompt = body.get("prompt", "").strip()
        if not prompt:
            return web.json_response({"error": "prompt is required"}, status=400, headers=headers)

        # Set GEMINI_API_KEY explicitly to avoid GOOGLE_API_KEY override
        import google.genai as genai_module
        from google import genai
        from google.genai import types

        client = _make_client()

        print(f"Generating image for prompt: {prompt[:80]}...")

        # Use JPEG output to reduce payload size and transfer time.
        # Quality 85 is a good balance; set IMAGE_OUTPUT_QUALITY in .env to override.
        output_quality = int(os.getenv("IMAGE_OUTPUT_QUALITY", "85"))
        response = await client.aio.models.generate_content(
            model=MODEL_ID,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
                image_config=types.ImageConfig(
                    output_mime_type="image/jpeg",
                    output_compression_quality=output_quality,
                ),
            ),
        )

        if response and response.candidates:
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.inline_data and part.inline_data.data:
                            mime = part.inline_data.mime_type or "image/png"
                            data = part.inline_data.data
                            encoded = base64.b64encode(data).decode()
                            print(f"Image generated: {len(data)} bytes, mime={mime}")
                            return web.json_response({
                                "image_base64": encoded,
                                "mime_type": mime,
                            }, headers=headers)

        return web.json_response({"error": "No image returned from model"}, status=500, headers=headers)

    except Exception as e:
        logger.error("Image generation error: %s", e)
        print(f"ERROR: {e}")
        return web.json_response({"error": str(e)}, status=500, headers=headers)


async def main():
    if not USE_VERTEX and not GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY not set in .env (required for AI Studio mode)")
        return

    print(f"Using model: {MODEL_ID}")
    print(f"Mode: {'Vertex AI' if USE_VERTEX else 'AI Studio'}")
    if not USE_VERTEX:
        print(f"API key: {GEMINI_API_KEY[:8]}...")

    app = web.Application()
    app.router.add_post("/generate", handle_generate)
    app.router.add_route("OPTIONS", "/generate", handle_generate)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Image server running on http://0.0.0.0:{PORT}")
    await asyncio.Future()  # run forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nImage server stopped.")
