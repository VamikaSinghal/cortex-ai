"""
Smoke test for the intent-graph summarization stage.

Usage:
    # Summarize an existing staged+described batch:
    python tests/smoke_summarize.py <batch_id>

    # Stage a new batch, describe images, then summarize — all inline:
    python tests/smoke_summarize.py --run-intake

Requires ANTHROPIC_API_KEY. TTC_API_KEY is optional (falls back to uncompressed).
Exits 0 on success, 1 on failure.
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
from summarize_transcript import STAGING_DIR, summarize_batch


# ── Minimal PNG helper ─────────────────────────────────────────────────────────

def minimal_png(rgb: tuple = (100, 149, 237)) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", c)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" + bytes(rgb))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


# ── Inline staging ─────────────────────────────────────────────────────────────

def stage_and_describe_inline() -> str:
    """Stage a batch + describe images, returning batch_id ready for summarization."""
    now = int(time.time())
    ts1, ts2 = now - 10, now - 5

    batch_id = f"batch_{uuid4()}"
    batch_dir = STAGING_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    # Write images
    (batch_dir / f"{ts1}_red.png").write_bytes(minimal_png((255, 0, 0)))
    (batch_dir / f"{ts2}_blue.png").write_bytes(minimal_png((0, 0, 255)))

    # Write transcript (2-turn conversation with clear goal + action item)
    transcript = [
        {
            "speaker": "Alice",
            "text": "I need to finish the intake API before the demo tonight.",
            "started_at": ts1,
            "ended_at": ts1 + 4,
        },
        {
            "speaker": "Bob",
            "text": "I'll handle the Redis indexing part. You focus on the endpoint.",
            "started_at": ts2,
            "ended_at": ts2 + 4,
        },
    ]
    (batch_dir / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")

    meta = {
        "batch_id": batch_id,
        "image_count": 2,
        "received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (batch_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"Staged inline batch: {batch_id}")

    # Describe images via Haiku
    print("Describing images via Haiku...")
    describe_batch(batch_id)

    return batch_id


# ── Assertions ─────────────────────────────────────────────────────────────────

def run_smoke(batch_id: str) -> None:
    print(f"Summarizing batch: {batch_id} ...")
    summarize_batch(batch_id)

    batch_dir = STAGING_DIR / batch_id

    # intent_graph.json assertions
    graph_path = batch_dir / "intent_graph.json"
    assert graph_path.exists(), "intent_graph.json not written"
    nodes = json.loads(graph_path.read_text(encoding="utf-8"))

    assert len(nodes) >= 1, f"Expected at least 1 intent-graph node, got 0"

    has_related = any(node.get("related_images") for node in nodes)
    assert has_related, (
        "Expected at least one node with non-empty related_images, but all are empty. "
        "Check that descriptions.json exists and timestamps overlap with transcript."
    )

    print(f"\nExtracted {len(nodes)} node(s):")
    for node in nodes:
        desc = (node.get("description") or "")[:120]
        imgs = node.get("related_images", [])
        print(f"  [{node.get('node_type')}] {desc} (images: {imgs})")

    # meta.json assertions
    meta = json.loads((batch_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta.get("summarization_status") == "complete", (
        f"Expected summarization_status=complete, got {meta.get('summarization_status')}"
    )

    print(f"\n✅ Smoke test passed: batch_id={batch_id}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if not args:
        print("Usage: python tests/smoke_summarize.py <batch_id>")
        print("       python tests/smoke_summarize.py --run-intake")
        sys.exit(1)

    if args[0] == "--run-intake":
        batch_id = stage_and_describe_inline()
    else:
        batch_id = args[0]

    try:
        run_smoke(batch_id)
    except (AssertionError, Exception) as exc:
        print(f"❌ Smoke test failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
