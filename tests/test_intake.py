"""
Integration tests for POST /api/intake/batch.

Run: pytest tests/test_intake.py -v
"""

import json
import os
import shutil
import struct
import time
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Set staging dir to a temp location for tests
os.environ.setdefault("STAGING_DIR", "staging_test")

from intake_api import app, STAGING_DIR

CLIENT = TestClient(app, raise_server_exceptions=False)


# ── Helpers ───────────────────────────────────────────────────────────────────

def minimal_png() -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", c)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


PNG = minimal_png()
NOW = int(time.time())

VALID_TRANSCRIPT = json.dumps([
    {"speaker": "A", "text": "hello", "timestamp": NOW},
    {"speaker": "B", "text": "world", "timestamp": NOW + 3},
])


def ts_image(ts: int, suffix: str = ".png") -> tuple[str, bytes, str]:
    """Image with an 8+-digit timestamp embedded in the filename."""
    return (f"{ts}{suffix}", PNG, "image/png")


def post_batch(images, transcript=VALID_TRANSCRIPT, extra_data=None):
    files = [("images[]", img) for img in images]
    data = {"transcript": transcript}
    if extra_data:
        data.update(extra_data)
    return CLIENT.post("/api/intake/batch", files=files, data=data)


@pytest.fixture(autouse=True)
def clean_staging():
    staging = Path("staging_test")
    staging.mkdir(exist_ok=True)
    yield
    shutil.rmtree(staging, ignore_errors=True)


# ── 8.1 Valid batch ───────────────────────────────────────────────────────────

def test_valid_batch_returns_202():
    images = [ts_image(NOW), ts_image(NOW + 1), ts_image(NOW + 2)]
    resp = post_batch(images)
    assert resp.status_code == 202
    body = resp.json()
    assert "batch_id" in body
    assert body["batch_id"].startswith("batch_")


# ── 8.2 Image with no timestamp in filename ───────────────────────────────────

def test_missing_timestamp_returns_400_naming_image():
    images = [ts_image(NOW), ("photo.png", PNG, "image/png")]
    resp = post_batch(images)
    assert resp.status_code == 400
    assert "photo.png" in resp.json()["error"]


# ── 8.3 Timestamp too short (fewer than 8 digits) ────────────────────────────

def test_short_timestamp_returns_400():
    images = [("img_123.png", PNG, "image/png")]  # 123 is only 3 digits
    resp = post_batch(images)
    assert resp.status_code == 400
    assert "img_123.png" in resp.json()["error"]


# ── 8.4 Missing required transcript field ─────────────────────────────────────

def test_missing_transcript_field_returns_400():
    transcript = json.dumps([
        {"speaker": "A", "text": "hi"}
        # timestamp missing
    ])
    resp = post_batch([ts_image(NOW)], transcript=transcript)
    assert resp.status_code == 400
    body = resp.json()
    assert "timestamp" in body["error"]
    assert "transcript[0]" in body["error"]


# ── 8.5 Transcript not JSON ───────────────────────────────────────────────────

def test_transcript_not_json_returns_400():
    resp = post_batch([ts_image(NOW)], transcript="not json at all")
    assert resp.status_code == 400
    assert "valid JSON" in resp.json()["error"]


# ── 8.6 Empty images array ────────────────────────────────────────────────────

def test_empty_images_returns_400():
    resp = CLIENT.post(
        "/api/intake/batch",
        files=[],
        data={"transcript": VALID_TRANSCRIPT},
    )
    assert resp.status_code == 400
    assert "at least one image" in resp.json()["error"]


# ── 8.7 Too many images ───────────────────────────────────────────────────────

def test_too_many_images_returns_400():
    images = [ts_image(NOW + i) for i in range(21)]
    resp = post_batch(images)
    assert resp.status_code == 400
    assert "max image count" in resp.json()["error"]


# ── 8.8 Oversized image ───────────────────────────────────────────────────────

def test_oversized_image_returns_400():
    big = b"x" * (5 * 1024 * 1024 + 1)
    images = [(f"{NOW}.png", big, "image/png")]
    resp = post_batch(images)
    assert resp.status_code == 400
    body = resp.json()
    assert "5 MB" in body["error"]
    assert f"{NOW}.png" in body["error"]


# ── 8.9 Oversized transcript ──────────────────────────────────────────────────

def test_oversized_transcript_returns_400():
    big_transcript = json.dumps([
        {"speaker": "A", "text": "x" * (500 * 1024), "timestamp": NOW}
    ])
    resp = post_batch([ts_image(NOW)], transcript=big_transcript)
    assert resp.status_code == 400
    assert "500 KB" in resp.json()["error"]


# ── 8.10 Malformed multipart ──────────────────────────────────────────────────

def test_malformed_multipart_returns_400_not_500():
    resp = CLIENT.post(
        "/api/intake/batch",
        content=b"this is not multipart",
        headers={"content-type": "multipart/form-data; boundary=MISSING"},
    )
    assert resp.status_code == 400
    assert resp.status_code != 500


# ── 8.11 Staged files exist after 202 ────────────────────────────────────────

def test_staged_files_exist_after_202(tmp_path, monkeypatch):
    import intake_api
    staging = tmp_path / "staging"
    monkeypatch.setattr(intake_api, "STAGING_DIR", staging)

    images = [ts_image(NOW), ts_image(NOW + 1)]
    resp = post_batch(images)
    assert resp.status_code == 202

    batch_id = resp.json()["batch_id"]
    batch_dir = staging / batch_id

    assert batch_dir.exists(), f"staging dir not found: {batch_dir}"
    assert (batch_dir / "transcript.json").exists()
    assert (batch_dir / "meta.json").exists()

    meta = json.loads((batch_dir / "meta.json").read_text())
    assert meta["batch_id"] == batch_id
    assert meta["image_count"] == 2

    staged_images = [f for f in batch_dir.iterdir() if f.suffix == ".png"]
    assert len(staged_images) == 2


# ── 8.12 session_id stored in meta ───────────────────────────────────────────

def test_session_id_stored_in_meta(tmp_path, monkeypatch):
    import intake_api
    monkeypatch.setattr(intake_api, "STAGING_DIR", tmp_path / "staging")

    resp = post_batch([ts_image(NOW)], extra_data={"session_id": "test-sess-001"})
    assert resp.status_code == 202
    meta = json.loads((tmp_path / "staging" / resp.json()["batch_id"] / "meta.json").read_text())
    assert meta["session_id"] == "test-sess-001"


def test_session_id_null_when_omitted(tmp_path, monkeypatch):
    import intake_api
    monkeypatch.setattr(intake_api, "STAGING_DIR", tmp_path / "staging")

    resp = post_batch([ts_image(NOW)])
    assert resp.status_code == 202
    meta = json.loads((tmp_path / "staging" / resp.json()["batch_id"] / "meta.json").read_text())
    assert meta["session_id"] is None


# ── 8.13 transcript_offset stored in meta ────────────────────────────────────

def test_transcript_offset_stored_in_meta(tmp_path, monkeypatch):
    import intake_api
    monkeypatch.setattr(intake_api, "STAGING_DIR", tmp_path / "staging")

    transcript = json.dumps([
        {"speaker": "A", "text": "t0", "timestamp": NOW},
        {"speaker": "B", "text": "t1", "timestamp": NOW + 1},
        {"speaker": "A", "text": "t2", "timestamp": NOW + 2},
        {"speaker": "B", "text": "t3", "timestamp": NOW + 3},
    ])
    resp = post_batch([ts_image(NOW)], transcript=transcript, extra_data={"transcript_offset": "3"})
    assert resp.status_code == 202
    meta = json.loads((tmp_path / "staging" / resp.json()["batch_id"] / "meta.json").read_text())
    assert meta["transcript_offset"] == 3


def test_transcript_offset_defaults_to_zero(tmp_path, monkeypatch):
    import intake_api
    monkeypatch.setattr(intake_api, "STAGING_DIR", tmp_path / "staging")

    resp = post_batch([ts_image(NOW)])
    assert resp.status_code == 202
    meta = json.loads((tmp_path / "staging" / resp.json()["batch_id"] / "meta.json").read_text())
    assert meta["transcript_offset"] == 0


def test_transcript_offset_invalid_returns_400():
    resp = post_batch([ts_image(NOW)], extra_data={"transcript_offset": "not-a-number"})
    assert resp.status_code == 400
    assert "transcript_offset" in resp.json()["error"]


# ── 8.14 started_at / ended_at accepted as optional turn fields ───────────────

def test_started_at_ended_at_accepted():
    transcript = json.dumps([{
        "speaker": "A",
        "text": "hello",
        "timestamp": NOW,
        "started_at": NOW,
        "ended_at": NOW + 2,
    }])
    resp = post_batch([ts_image(NOW)], transcript=transcript)
    assert resp.status_code == 202


def test_started_at_must_be_int_if_present():
    transcript = json.dumps([{
        "speaker": "A",
        "text": "hello",
        "timestamp": NOW,
        "started_at": "not-a-number",
    }])
    resp = post_batch([ts_image(NOW)], transcript=transcript)
    assert resp.status_code == 400
    assert "started_at" in resp.json()["error"]
