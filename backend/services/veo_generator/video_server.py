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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Remove GOOGLE_API_KEY from env so the SDK doesn't override our explicit key
os.environ.pop("GOOGLE_API_KEY", None)
USE_VERTEX = os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "false").lower() == "true"
GCP_PROJECT = os.getenv("GCP_PROJECT_ID", "")
VERTEX_LOCATION = os.getenv("VERTEX_AI_LOCATION", "us-central1")

MODEL_ID = os.getenv("VEO_MODEL", "veo-3.1-generate-preview")
PORT = int(os.getenv("VIDEO_SERVER_PORT") or os.getenv("PORT") or "8092")


def _make_client():
    from google import genai
    if USE_VERTEX:
        return genai.Client(vertexai=True, project=GCP_PROJECT, location=VERTEX_LOCATION)
    return genai.Client(api_key=GEMINI_API_KEY)

_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def _gcs_to_signed_url(gcs_uri: str) -> str | None:
    """Convert a gs://bucket/object URI to a short-lived signed HTTPS URL."""
    try:
        from google.cloud import storage
        # gs://bucket/path/to/object
        without_scheme = gcs_uri[len("gs://"):]
        bucket_name, _, blob_name = without_scheme.partition("/")
        client = storage.Client(project=GCP_PROJECT)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        import datetime
        url = blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(hours=1),
            method="GET",
        )
        return url
    except Exception as e:
        print(f"  _gcs_to_signed_url error: {e}")
        return None


def _extract_url(operation) -> str | None:
    """Extract video URL from a completed GenerateVideosOperation.

    Handles two Vertex AI response shapes:
      - URI present: returns the GCS/HTTPS URI string
      - video_bytes present (no output GCS bucket configured): returns a
        data URI so the caller can serve the bytes directly
    """
    import base64

    try:
        raw = getattr(operation, "__dict__", {})

        # Check for error first
        raw_error = raw.get("error")
        if raw_error:
            print(f"  _extract_url: operation has error: {raw_error}")
            return None

        # Vertex AI puts generated_videos inside raw['response'] as a dict
        raw_response = raw.get("response") or {}
        if isinstance(raw_response, dict):
            raw_videos = raw_response.get("generated_videos", [])
        else:
            raw_videos = getattr(raw_response, "generated_videos", None) or []

        if not raw_videos:
            print(f"  _extract_url: no generated_videos. raw keys={list(raw.keys())}, response={raw_response}")
            return None

        v = raw_videos[0]

        def _resolve_video_obj(entry):
            """Return the innermost video object whether entry is dict or namespace."""
            if isinstance(entry, dict):
                return entry.get("video") or entry
            return getattr(entry, "video", entry)

        video_obj = _resolve_video_obj(v)

        # --- Try URI first ---
        if isinstance(video_obj, dict):
            for key in ("uri", "url", "video_uri", "download_uri", "gcs_uri"):
                val = video_obj.get(key)
                if val:
                    return str(val)
            # Fallback: raw bytes returned by Vertex AI when no GCS bucket set
            raw_bytes = video_obj.get("video_bytes")
            mime = video_obj.get("mime_type", "video/mp4")
        else:
            for attr in ("uri", "url", "video_uri", "download_uri", "gcs_uri"):
                val = getattr(video_obj, attr, None)
                if val:
                    return str(val)
            raw_bytes = getattr(video_obj, "video_bytes", None)
            mime = getattr(video_obj, "mime_type", "video/mp4") or "video/mp4"

        if raw_bytes:
            encoded = base64.b64encode(raw_bytes).decode("ascii")
            return f"data:{mime};base64,{encoded}"

        print(f"  _extract_url: no uri found in video object: video={video_obj}")
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

        from google.genai import types

        client = _make_client()

        print(f"Generating video: {prompt[:80]}...")

        loop = asyncio.get_event_loop()

        # Resolution and duration are the main latency levers:
        # resolution: "720p" < "1080p" < "4k" (speed vs quality)
        # duration_seconds: 4 | 6 | 8 (shorter = faster)
        video_resolution = os.getenv("VEO_RESOLUTION", "720p")
        video_duration = os.getenv("VEO_DURATION_SECONDS", "5")

        operation = await loop.run_in_executor(
            None,
            lambda: client.models.generate_videos(
                model=MODEL_ID,
                prompt=prompt,
                config=types.GenerateVideosConfig(
                    aspect_ratio="16:9",
                    number_of_videos=1,
                    duration_seconds=video_duration,
                    resolution=video_resolution,
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
            if USE_VERTEX and url.startswith("gs://"):
                # Vertex AI returned a GCS URI — generate a signed download URL
                playable_url = _gcs_to_signed_url(url)
                if not playable_url:
                    return web.json_response({"error": "Failed to sign GCS URL"}, status=500, headers=_CORS)
            elif url.startswith("data:"):
                # Vertex AI returned raw bytes (no GCS output bucket configured)
                # Serve the data URI directly — Flutter video_player can handle it
                playable_url = url
            else:
                # AI Studio: append API key so Flutter video player can download without auth headers
                sep = "&" if "?" in url else "?"
                playable_url = f"{url}{sep}key={GEMINI_API_KEY}"
            print(f"Video ready: {url[:80]}")
            return web.json_response({"video_url": playable_url, "duration": int(video_duration)}, headers=_CORS)

        return web.json_response(
            {"error": "No video returned from model"},
            status=500, headers=_CORS,
        )

    except Exception as e:
        logger.error("Video generation error: %s", e)
        print(f"ERROR: {e}")
        return web.json_response({"error": str(e)}, status=500, headers=_CORS)


async def main():
    if not USE_VERTEX and not GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY not set in .env (required for AI Studio mode)")
        return

    print(f"Model: {MODEL_ID}")
    print(f"Mode: {'Vertex AI' if USE_VERTEX else 'AI Studio'}")
    if not USE_VERTEX:
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
