"""
Unit tests for describe_images.py.

Failure scenarios use a mocked Anthropic client.
Real API calls only in smoke_describe.py and the redaction spot-check below.

Run: pytest tests/test_describe.py -v
"""

import json
import os
import struct
import sys
import zlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import describe_images
from describe_images import describe_image, process_batch

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def minimal_png(rgb: tuple = (100, 149, 237)) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", c)

    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat_data = zlib.compress(b"\x00" + bytes(rgb))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr_data)
        + chunk(b"IDAT", idat_data)
        + chunk(b"IEND", b"")
    )


def good_response(text: str = "A scene with chairs and a window.") -> MagicMock:
    r = MagicMock()
    r.stop_reason = "end_turn"
    r.content = [MagicMock(text=text)]
    return r


def make_batch(staging_dir: Path, images: list[tuple[int, str, bytes]]) -> str:
    """Write a staged batch to staging_dir. Returns batch_id."""
    from uuid import uuid4
    batch_id = f"batch_{uuid4()}"
    batch_dir = staging_dir / batch_id
    batch_dir.mkdir()
    for observed_at, orig_name, data in images:
        (batch_dir / f"{observed_at}_{orig_name}").write_bytes(data)
    meta = {
        "batch_id": batch_id,
        "image_count": len(images),
        "received_at": "2024-06-20T00:00:00+00:00",
    }
    (batch_dir / "meta.json").write_text(json.dumps(meta))
    return batch_id


@pytest.fixture
def staging(tmp_path, monkeypatch):
    monkeypatch.setattr(describe_images, "STAGING_DIR", tmp_path)
    return tmp_path


# ── Test 4.1: Normal batch ────────────────────────────────────────────────────

def test_normal_batch(staging):
    """3 images → 3 records in observed_at order, all descriptions non-empty, exactly one first."""
    T = 1718900000
    batch_id = make_batch(staging, [
        (T + 5, "c.png", minimal_png((0, 0, 255))),
        (T,     "a.png", minimal_png((255, 0, 0))),
        (T + 2, "b.png", minimal_png((0, 255, 0))),
    ])

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        good_response("desc A"),
        good_response("desc B"),
        good_response("desc C"),
    ]

    with patch.object(describe_images.anthropic, "Anthropic", return_value=mock_client):
        meta = process_batch(batch_id)

    records = json.loads((staging / batch_id / "descriptions.json").read_text())

    assert len(records) == 3
    assert [r["observed_at"] for r in records] == [T, T + 2, T + 5], "not sorted ascending"

    for r in records:
        assert r["description"], f"empty description for {r['id']}"
        assert r["error"] is None
        assert r["redaction_applied"] is True

    first_flags = [r["is_first_in_batch"] for r in records]
    assert first_flags.count(True) == 1
    assert records[0]["is_first_in_batch"] is True

    assert meta["description_status"] == "complete"
    assert meta["described_count"] == 3
    assert meta["failed_count"] == 0


# ── Test 4.2: Single image fails ─────────────────────────────────────────────

def test_single_image_fails(staging):
    """B fails → A and C have descriptions, B has error; status partial."""
    T = 1718900000
    batch_id = make_batch(staging, [
        (T,     "a.png", minimal_png()),
        (T + 1, "b.png", minimal_png()),
        (T + 2, "c.png", minimal_png()),
    ])

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        good_response("desc A"),
        Exception("network timeout"),
        good_response("desc C"),
    ]

    with patch.object(describe_images.anthropic, "Anthropic", return_value=mock_client):
        meta = process_batch(batch_id)

    records = json.loads((staging / batch_id / "descriptions.json").read_text())

    assert records[0]["description"] == "desc A"
    assert records[0]["error"] is None

    assert records[1]["description"] is None
    assert records[1]["error"] == "network timeout"

    assert records[2]["description"] == "desc C"
    assert records[2]["error"] is None

    assert meta["description_status"] == "partial"
    assert meta["described_count"] == 2
    assert meta["failed_count"] == 1


# ── Test 4.3: All images fail ─────────────────────────────────────────────────

def test_all_images_fail(staging):
    """Every image fails → status failed, described_count 0."""
    T = 1718900000
    batch_id = make_batch(staging, [
        (T,     "x.png", minimal_png()),
        (T + 1, "y.png", minimal_png()),
    ])

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("service unavailable")

    with patch.object(describe_images.anthropic, "Anthropic", return_value=mock_client):
        meta = process_batch(batch_id)

    records = json.loads((staging / batch_id / "descriptions.json").read_text())

    assert all(r["description"] is None for r in records)
    assert all(r["error"] for r in records)
    assert meta["description_status"] == "failed"
    assert meta["described_count"] == 0
    assert meta["failed_count"] == 2


# ── Test 4.4: Redaction spot-check (live API, manual) ────────────────────────

@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set — live redaction spot-check skipped",
)
def test_redaction_spot_check(tmp_path):
    """
    Live call with a synthetic image. Confirm the API returns a description
    (we cannot programmatically verify omissions — this is a spot-check).

    Note: This is a spot-check, not a guarantee — redaction is prompt-level.
    """
    png = minimal_png(rgb=(200, 100, 50))
    record = describe_image(png, observed_at=1718900000, image_id="1718900000_spot.png")

    print(f"\nSpot-check description: {record['description']}")
    print("Note: This is a spot-check, not a guarantee — redaction is prompt-level.")

    assert record["description"], f"Expected a description, got error: {record['error']}"
    assert record["redaction_applied"] is True
    assert record["error"] is None


# ── Test 4.5: Batch of exactly 1 image ───────────────────────────────────────

def test_single_image_batch(staging):
    """1 image → is_first_in_batch True, status complete."""
    T = 1718900000
    batch_id = make_batch(staging, [(T, "solo.png", minimal_png())])

    mock_client = MagicMock()
    mock_client.messages.create.return_value = good_response("a single lonely frame")

    with patch.object(describe_images.anthropic, "Anthropic", return_value=mock_client):
        meta = process_batch(batch_id)

    records = json.loads((staging / batch_id / "descriptions.json").read_text())

    assert len(records) == 1
    assert records[0]["is_first_in_batch"] is True
    assert records[0]["description"] == "a single lonely frame"
    assert meta["description_status"] == "complete"
    assert meta["described_count"] == 1
    assert meta["failed_count"] == 0


# ── Test 4.6: Ordering assertion ──────────────────────────────────────────────

def test_out_of_order_filenames_sorted(staging):
    """Images staged out of order → descriptions.json records in ascending observed_at."""
    T = 1718900000
    # Write in reverse order; process_batch must sort them
    batch_id = make_batch(staging, [
        (T + 5, "last.png",   minimal_png((0,   0, 255))),
        (T,     "first.png",  minimal_png((255, 0, 0))),
        (T + 2, "middle.png", minimal_png((0, 255, 0))),
    ])

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        good_response("first"),
        good_response("middle"),
        good_response("last"),
    ]

    with patch.object(describe_images.anthropic, "Anthropic", return_value=mock_client):
        process_batch(batch_id)

    records = json.loads((staging / batch_id / "descriptions.json").read_text())

    observed_ats = [r["observed_at"] for r in records]
    assert observed_ats == sorted(observed_ats), f"Records not sorted: {observed_ats}"
    assert observed_ats == [T, T + 2, T + 5]
    assert records[0]["description"] == "first"
    assert records[1]["description"] == "middle"
    assert records[2]["description"] == "last"
