#!/usr/bin/env python3
"""
Lightweight HTTP server for Veo video generation.
Called by the Flutter voice mode when the generate_video tool is triggered.

POST /generate
  Body: {"prompt": "..."}
  Response: {"video_url": "https://...", "duration": 8}

Generation takes 30-120s — the Flutter client uses a 3-minute timeout.

Run: python backend/services/veo_generator/video_server.py
"""
import asyncio
import logging
import os
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Remove GOOGLE_API_KEY from env so the SDK doesn't override our explicit key
os.environ.pop("GOOGLE_API_KEY", None)

MODEL_ID = os.getenv("VEO_MODEL", "veo-3.1-generate-preview")
PORT = int(os.getenv("VIDEO_SERVER_PORT", "8092"))

_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def _extract_url(operation) -> str | None:
    """Try every known attribute path to get the video URL."""
    try:
        videos = operation.response.generated_videos
        if not videos:
            return None
        v = videos[0]
        video_obj = getattr(v, "video", v)
        for attr in ("uri", "url", "video_uri", "download_uri"):
            val = getattr(video_obj, attr, None)
            if val:
                return str(val)
    except Exception as e:
        print(f"  _extract_url error: {e}")
    return None


async def handle_generate(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=_CORS)

    try:
        body = await request.json()
        prompt = body.get("prompt", "").strip()
        if not prompt:
            return web.json_response({"error": "prompt is required"}, status=400, headers=_CORS)

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)

        print(f"Generating video: {prompt[:80]}...")

        loop = asyncio.get_event_loop()

        operation = await loop.run_in_executor(
            None,
            lambda: client.models.generate_videos(
                model=MODEL_ID,
                prompt=prompt,
                config=types.GenerateVideosConfig(
                    aspect_ratio="16:9",
                    number_of_videos=1,
                    duration_seconds="8",
                ),
            ),
        )

        # Poll until done
        poll = 0
        while not operation.done:
            await asyncio.sleep(5)
            operation = await loop.run_in_executor(
                None,
                lambda: client.operations.get(operation),
            )
            poll += 1
            if poll > 72:
                return web.json_response({"error": "Generation timed out"}, status=504, headers=_CORS)

        print(f"  Operation done.")

        url = _extract_url(operation)
        if url:
            # Append API key so the Flutter video player can download without auth headers
            sep = "&" if "?" in url else "?"
            playable_url = f"{url}{sep}key={GEMINI_API_KEY}"
            print(f"Video ready: {url[:80]}")
            return web.json_response({"video_url": playable_url, "duration": 8}, headers=_CORS)

        return web.json_response(
            {"error": "No video returned from model"},
            status=500, headers=_CORS,
        )

    except Exception as e:
        logger.error("Video generation error: %s", e)
        print(f"ERROR: {e}")
        return web.json_response({"error": str(e)}, status=500, headers=_CORS)


async def main():
    if not GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY not set in .env")
        return

    print(f"Model: {MODEL_ID}")
    print(f"API key: {GEMINI_API_KEY[:8]}...")

    app = web.Application()
    app.router.add_post("/generate", handle_generate)
    app.router.add_route("OPTIONS", "/generate", handle_generate)
    app.router.add_get("/health", lambda r: web.json_response({"status": "ok", "model": MODEL_ID}))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Video server running on http://0.0.0.0:{PORT}")
    await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nVideo server stopped.")
