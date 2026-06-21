"""
Smoke test for POST /api/intake/batch.

Run against a live server:
    uvicorn intake_api:app --port 8000 &
    python tests/smoke_intake.py

Exits 0 on success, 1 on failure.
"""

import json
import struct
import sys
import time
import zlib

import requests

BASE_URL = "http://localhost:8000"


def minimal_png(width: int = 1, height: int = 1, rgb: tuple = (100, 149, 237)) -> bytes:
    """Build a minimal valid PNG in memory — no Pillow needed."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", c)

    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw_row = b"\x00" + bytes(rgb) * width
    idat_data = zlib.compress(raw_row * height)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr_data)
        + chunk(b"IDAT", idat_data)
        + chunk(b"IEND", b"")
    )


def main():
    now = int(time.time())
    ts1, ts2, ts3 = now - 10, now - 5, now

    images = [
        (f"{ts1}.png", minimal_png(rgb=(255, 0, 0))),
        (f"capture_{ts2}.png", minimal_png(rgb=(0, 255, 0))),
        (f"img_{ts3}_frame.png", minimal_png(rgb=(0, 0, 255))),
    ]

    transcript = [
        {"speaker": "A", "text": "Hey, how's it going?", "timestamp": ts1},
        {"speaker": "B", "text": "Pretty good, working on Cortex.", "timestamp": ts2},
    ]

    files = [("images[]", (name, data, "image/png")) for name, data in images]
    data = {"transcript": json.dumps(transcript)}

    print(f"POSTing batch with {len(images)} images + {len(transcript)}-turn transcript...")

    try:
        resp = requests.post(f"{BASE_URL}/api/intake/batch", files=files, data=data, timeout=10)
    except requests.ConnectionError:
        print(f"❌ Could not connect to {BASE_URL} — is the server running?")
        sys.exit(1)

    if resp.status_code != 202:
        print(f"❌ Expected 202, got {resp.status_code}: {resp.text}")
        sys.exit(1)

    body = resp.json()
    batch_id = body.get("batch_id", "")
    if not batch_id.startswith("batch_"):
        print(f"❌ batch_id missing or malformed: {body}")
        sys.exit(1)

    print(f"✅ Smoke test passed: batch_id={batch_id}")


if __name__ == "__main__":
    main()
