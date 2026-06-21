"""
Smoke test for the Haiku image description stage.

Usage:
    # Describe an existing staged batch:
    python tests/smoke_describe.py <batch_id>

    # Stage a new batch inline, then describe it:
    python tests/smoke_describe.py --run-intake

Requires ANTHROPIC_API_KEY in the environment.
Exits 0 on success, 1 on failure.
"""

import json
import struct
import sys
import time
import zlib
from pathlib import Path
from uuid import uuid4

# Allow importing from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from describe_images import process_batch, STAGING_DIR


# ── Minimal PNG helper (no Pillow needed) ──────────────────────────────────────

def minimal_png(width: int = 1, height: int = 1, rgb: tuple = (100, 149, 237)) -> bytes:
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


# ── Inline staging ─────────────────────────────────────────────────────────────

def stage_batch_inline() -> str:
    """Create a staging batch directly on disk (no HTTP needed)."""
    now = int(time.time())
    ts1, ts2, ts3 = now - 10, now - 5, now

    batch_id = f"batch_{uuid4()}"
    batch_dir = STAGING_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    images = [
        (ts1, f"{ts1}_red.png", minimal_png(rgb=(255, 0, 0))),
        (ts2, f"capture_{ts2}.png", minimal_png(rgb=(0, 255, 0))),
        (ts3, f"img_{ts3}_frame.png", minimal_png(rgb=(0, 0, 255))),
    ]

    for ts, filename, data in images:
        (batch_dir / f"{ts}_{filename}").write_bytes(data)

    meta = {
        "batch_id": batch_id,
        "image_count": len(images),
        "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (batch_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"Staged inline batch: {batch_id}")
    return batch_id


# ── Assertions ────────────────────────────────────────────────────────────────

def run_smoke(batch_id: str) -> None:
    print(f"Describing batch: {batch_id} ...")
    process_batch(batch_id)

    batch_dir = STAGING_DIR / batch_id

    # descriptions.json assertions
    descriptions_path = batch_dir / "descriptions.json"
    assert descriptions_path.exists(), "descriptions.json not written"
    records = json.loads(descriptions_path.read_text(encoding="utf-8"))

    image_files = [
        f for f in batch_dir.iterdir()
        if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
    ]
    assert len(records) == len(image_files), (
        f"Expected {len(image_files)} records, got {len(records)}"
    )

    first_count = sum(1 for r in records if r.get("is_first_in_batch"))
    assert first_count == 1, f"Expected exactly 1 is_first_in_batch, got {first_count}"

    for r in records:
        assert r.get("description"), f"Empty description for id={r['id']}: error={r.get('error')}"
        ts_from_filename = int(r["id"].split("_")[0])
        assert r["observed_at"] == ts_from_filename, (
            f"observed_at mismatch for {r['id']}: got {r['observed_at']}, expected {ts_from_filename}"
        )

    print("\nDescriptions (truncated to 120 chars each):")
    for r in records:
        desc = (r["description"] or "")[:120]
        first_tag = " [FIRST]" if r.get("is_first_in_batch") else ""
        print(f"  {r['id']}{first_tag}: {desc}")

    # meta.json assertions
    meta = json.loads((batch_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta.get("description_status") == "complete", (
        f"Expected description_status=complete, got {meta.get('description_status')}"
    )

    print(f"\n✅ Smoke test passed: batch_id={batch_id}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if not args:
        print("Usage: python tests/smoke_describe.py <batch_id>")
        print("       python tests/smoke_describe.py --run-intake")
        sys.exit(1)

    if args[0] == "--run-intake":
        batch_id = stage_batch_inline()
    else:
        batch_id = args[0]

    try:
        run_smoke(batch_id)
    except (AssertionError, Exception) as exc:
        print(f"❌ Smoke test failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
