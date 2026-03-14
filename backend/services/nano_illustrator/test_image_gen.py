#!/usr/bin/env python3
"""
Quick standalone test for Gemini image generation.
Run: python backend/services/nano_illustrator/test_image_gen.py
"""
import asyncio
import os
import sys
from pathlib import Path

# Load .env from project root
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL_ID = "gemini-3.1-flash-image-preview"  # correct model for AI Studio image gen

async def test_generate():
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        print("ERROR: google-genai not installed. Run: pip install google-genai")
        sys.exit(1)

    if not GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY not set in .env")
        sys.exit(1)

    print(f"Using model: {MODEL_ID}")
    print(f"API key: {GEMINI_API_KEY[:8]}...")

    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = "A dramatic documentary-style illustration of the Roman Colosseum at sunset, photorealistic"
    print(f"\nPrompt: {prompt}\n")

    try:
        response = await client.aio.models.generate_content(
            model=MODEL_ID,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )

        if response and response.candidates:
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.inline_data and part.inline_data.data:
                            mime = part.inline_data.mime_type or "image/png"
                            data = part.inline_data.data
                            ext = "jpg" if "jpeg" in mime else "png"
                            out_path = Path(__file__).parent / f"test_output.{ext}"
                            out_path.write_bytes(data)
                            print(f"SUCCESS: Image saved to {out_path}")
                            print(f"  MIME: {mime}, Size: {len(data)} bytes")
                            return
                        elif part.text:
                            print(f"  Text part: {part.text[:200]}")

        print("FAIL: No image data in response")
        print(f"Response: {response}")

    except Exception as e:
        print(f"ERROR: {e}")
        # Try fallback model name
        print("\nTrying fallback model: gemini-3.1-flash-image-preview")
        try:
            response = await client.aio.models.generate_content(
                model="gemini-3.1-flash-image-preview",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                ),
            )
            if response and response.candidates:
                for candidate in response.candidates:
                    if candidate.content and candidate.content.parts:
                        for part in candidate.content.parts:
                            if part.inline_data and part.inline_data.data:
                                data = part.inline_data.data
                                out_path = Path(__file__).parent / "test_output_fallback.png"
                                out_path.write_bytes(data)
                                print(f"SUCCESS with fallback: {out_path}, {len(data)} bytes")
                                return
        except Exception as e2:
            print(f"Fallback also failed: {e2}")

if __name__ == "__main__":
    asyncio.run(test_generate())
