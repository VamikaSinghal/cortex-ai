"""
Smoke test for the relevance filter and image pruning stage.

Usage:
    # Prune an existing batch (must have run through description + summarization):
    python tests/smoke_prune.py <batch_id>

    # Full pipeline inline (intake → describe → summarize → prune):
    python tests/smoke_prune.py --run-intake

Requires ANTHROPIC_API_KEY. Exits 0 on success, 1 on failure.
"""

import json
import struct
import sys
import time
import zlib
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from describe_images import process_batch as describe_batch
from prune_images import STAGING_DIR, prune_batch
from summarize_transcript import summarize_batch


# ── Minimal PNG helper ─────────────────────────────────────────────────────────

def minimal_png(rgb: tuple = (100, 149, 237)) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", c)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" + bytes(rgb))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


# ── Inline pipeline ────────────────────────────────────────────────────────────

def run_full_pipeline_inline() -> str:
    now = int(time.time())
    ts1, ts2, ts3 = now - 20, now - 10, now

    batch_id = f"batch_{uuid4()}"
    batch_dir = STAGING_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    (batch_dir / f"{ts1}_first.png").write_bytes(minimal_png((255, 0, 0)))
    (batch_dir / f"{ts2}_second.png").write_bytes(minimal_png((0, 255, 0)))
    (batch_dir / f"{ts3}_third.png").write_bytes(minimal_png((0, 0, 255)))

    transcript = [
        {"speaker": "Alice", "text": "I need to finish the intake API before tonight.", "timestamp": ts1},
        {"speaker": "Bob", "text": "I'll take care of the Redis part.", "timestamp": ts2},
    ]
    (batch_dir / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
    (batch_dir / "meta.json").write_text(json.dumps({
        "batch_id": batch_id,
        "image_count": 3,
        "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=2), encoding="utf-8")

    print(f"Staged: {batch_id}")
    print("Describing images...")
    describe_batch(batch_id)
    print("Summarizing transcript...")
    summarize_batch(batch_id)
    print("Pruning...")
    return batch_id


# ── Assertions ─────────────────────────────────────────────────────────────────

def run_smoke(batch_id: str) -> None:
    prune_batch(batch_id)

    batch_dir = STAGING_DIR / batch_id

    pruning = json.loads((batch_dir / "pruning.json").read_text(encoding="utf-8"))
    descriptions = json.loads((batch_dir / "descriptions.json").read_text(encoding="utf-8"))

    # Every image must have a pruning record
    assert len(pruning) == len(descriptions), (
        f"pruning.json has {len(pruning)} entries but descriptions.json has {len(descriptions)}"
    )

    # Every record must have a non-empty reason
    for entry in pruning:
        assert entry.get("reason"), f"Empty reason for {entry.get('id')}"

    # First-in-batch must be keep
    first_desc = next((d for d in descriptions if d.get("is_first_in_batch")), None)
    assert first_desc, "No first-in-batch record found"
    first_pruning = next((p for p in pruning if p["id"] == first_desc["id"]), None)
    assert first_pruning, f"No pruning entry for first image {first_desc['id']}"
    assert first_pruning["decision"] == "keep", (
        f"First-in-batch image has decision={first_pruning['decision']}, expected keep"
    )

    meta = json.loads((batch_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta.get("pruning_status") == "complete", (
        f"Expected pruning_status=complete, got {meta.get('pruning_status')}"
    )

    print(f"\nPruning decisions ({meta['kept_count']} kept / {meta['deleted_count']} deleted):")
    for entry in pruning:
        reason = (entry.get("reason") or "")[:80]
        print(f"  [{entry['decision'].upper()}] {entry['id']}: {reason}")

    print(f"\n✅ Smoke test passed: batch_id={batch_id}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: python tests/smoke_prune.py <batch_id>")
        print("       python tests/smoke_prune.py --run-intake")
        sys.exit(1)

    if args[0] == "--run-intake":
        batch_id = run_full_pipeline_inline()
    else:
        batch_id = args[0]

    try:
        run_smoke(batch_id)
    except (AssertionError, Exception) as exc:
        print(f"❌ Smoke test failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
