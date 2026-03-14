#!/usr/bin/env python3
"""
Quick standalone test for Veo video generation via AI Studio (API key).
Veo generation takes 30-120s — be patient.

Run: python backend/services/veo_generator/test_veo_gen.py
"""
import asyncio
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL_ID = os.getenv("VEO_MODEL", "veo-3.1-generate-preview")

async def test_generate():
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print("ERROR: google-genai not installed.")
        sys.exit(1)

    if not GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY not set in .env")
        sys.exit(1)

    print(f"Model: {MODEL_ID}")
    print(f"API key: {GEMINI_API_KEY[:8]}...")

    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = (
        "Cinematic documentary footage of the Roman Colosseum at golden hour, "
        "slow aerial pan, professional cinematography, 4K quality"
    )
    print(f"\nPrompt: {prompt}\n")
    print("Submitting generation request (this takes 30-120s)...")

    start = time.time()

    try:
        operation = client.models.generate_videos(
            model=MODEL_ID,
            prompt=prompt,
            config=types.GenerateVideosConfig(
                aspect_ratio="16:9",
                number_of_videos=1,
                duration_seconds="8",
            ),
        )

        print("Polling for completion...")
        poll = 0
        while not operation.done:
            elapsed = time.time() - start
            print(f"  [{elapsed:.0f}s] Still generating...")
            await asyncio.sleep(5)
            operation = client.operations.get(operation)
            poll += 1
            if poll > 60:
                print("TIMEOUT after 300s")
                return

        elapsed = time.time() - start
        print(f"\nCompleted in {elapsed:.1f}s")

        if operation.response and operation.response.generated_videos:
            video = operation.response.generated_videos[0].video
            url = getattr(video, "uri", None) or getattr(video, "url", None)
            print(f"SUCCESS: video URL/URI = {url}")
            # Save URI to file for inspection
            out = Path(__file__).parent / "test_video_uri.txt"
            out.write_text(url or "no url")
            print(f"URI saved to {out}")
        else:
            print(f"FAIL: no video in response")
            print(f"Response: {operation.response}")

    except Exception as e:
        print(f"ERROR with {MODEL_ID}: {e}")
        # Try veo-3.0-generate-preview as fallback
        fallback = "veo-3.0-generate-preview"
        print(f"\nTrying fallback model: {fallback}")
        try:
            operation = client.models.generate_videos(
                model=fallback,
                prompt=prompt,
                config=types.GenerateVideosConfig(
                    aspect_ratio="16:9",
                    number_of_videos=1,
                    duration_seconds="8",
                ),
            )
            poll = 0
            while not operation.done:
                await asyncio.sleep(5)
                operation = client.operations.get(operation)
                poll += 1
                if poll > 60:
                    print("TIMEOUT")
                    return
            if operation.response and operation.response.generated_videos:
                video = operation.response.generated_videos[0].video
                url = getattr(video, "uri", None) or getattr(video, "url", None)
                print(f"SUCCESS with {fallback}: {url}")
            else:
                print(f"FAIL with fallback too")
        except Exception as e2:
            print(f"Fallback also failed: {e2}")

if __name__ == "__main__":
    asyncio.run(test_generate())
