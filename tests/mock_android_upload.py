"""
mock_android_upload.py
----------------------
Sends mock uploads that precisely replicate what FrameIngestionService.kt sends:
  - JPEG-named files with epoch-second timestamps (e.g. "1750000000.jpg")
  - multipart field name "images[]"
  - transcript JSON with {speaker, text, timestamp} turns where timestamp = wallClockMs/1000

Exercises:
  1. Test-connection pattern (1×1 image, empty transcript)
  2. Normal batch (5 frames, realistic transcript)
  3. Same-second timestamp collision (2 frames same second → must NOT lose one)
  4. Max-batch (20 frames, empty transcript)
  5. Growing-session transcript (201 turns, would previously 400)
  6. Bad input: missing timestamp in filename → 400
  7. Bad input: oversized single image → 400
  8. Bad input: malformed transcript JSON → 400

Run:
    python tests/mock_android_upload.py
"""

from __future__ import annotations

import io
import json
import struct
import sys
import time
import zlib

import requests

BASE = "http://localhost:8000"
ENDPOINT = f"{BASE}/api/intake/batch"


# ── JPEG builder (no Pillow needed) ──────────────────────────────────────────

def _minimal_jpeg() -> bytes:
    """Minimal syntactically plausible JPEG (SOI + APP0 + EOI).
    The server validates filename/size only, not image content."""
    app0 = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    marker = b"\xff\xe0" + struct.pack(">H", len(app0) + 2) + app0
    return b"\xff\xd8" + marker + b"\xff\xd9"


def _png(width: int = 4, height: int = 4) -> bytes:
    """Minimal valid PNG (used for oversized-image test)."""
    def chunk(tag, data):
        c = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", c)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"\x00" + b"\xff\x00\x00" * width
    idat = zlib.compress(raw * height)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


JPEG = _minimal_jpeg()
PNG  = _png()


# ── Post helper ───────────────────────────────────────────────────────────────

def post(
    frames: list[tuple[str, bytes]],
    transcript_turns: list,
    session_id: str | None = None,
    transcript_offset: int | None = None,
) -> requests.Response:
    """
    frames: list of (filename, jpeg_bytes)
    transcript_turns: list of dicts {speaker, text, timestamp[, started_at, ended_at]}
    """
    files = [
        ("images[]", (name, data, "image/jpeg"))
        for name, data in frames
    ]
    data: dict = {"transcript": json.dumps(transcript_turns)}
    if session_id is not None:
        data["session_id"] = session_id
    if transcript_offset is not None:
        data["transcript_offset"] = str(transcript_offset)
    return requests.post(ENDPOINT, files=files, data=data, timeout=15)


def _turns_with_timing(base_ts: int, raw: list[dict]) -> list[dict]:
    """Add started_at / ended_at to transcript turns, mirroring FrameIngestionService."""
    result = []
    for t in raw:
        ts = t["timestamp"]
        result.append({**t, "started_at": ts, "ended_at": ts})
    return result


# ── Test cases ────────────────────────────────────────────────────────────────

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
failures = []


def check(label: str, cond: bool, detail: str = ""):
    if cond:
        print(f"  {PASS} {label}")
    else:
        print(f"  {FAIL} {label}{': ' + detail if detail else ''}")
        failures.append(label)


def section(title: str):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Test-connection pattern (mirrors FrameIngestionService.testConnection)
# ─────────────────────────────────────────────────────────────────────────────
section("1. Test-connection pattern (1×1 JPEG, empty transcript)")

now = int(time.time())
r = post([(f"{now}.jpg", JPEG)], [])
check("returns 202",       r.status_code == 202, str(r.status_code))
check("body has batch_id", "batch_id" in r.json(), str(r.json()))
check("batch_id prefix",   r.json().get("batch_id", "").startswith("batch_"))


# ─────────────────────────────────────────────────────────────────────────────
# 2. Normal session batch (5 frames, realistic diarized transcript)
# ─────────────────────────────────────────────────────────────────────────────
section("2. Normal batch — 5 frames @ 2 s intervals, 4-turn transcript with started_at/ended_at")

base_ts = int(time.time()) - 20
frames = [(f"{base_ts + i * 2}.jpg", JPEG) for i in range(5)]
raw_turns = [
    {"speaker": "0", "text": "Hey, what's on the agenda today?",       "timestamp": base_ts},
    {"speaker": "1", "text": "We're reviewing the pipeline designs.",   "timestamp": base_ts + 4},
    {"speaker": "0", "text": "Cool, let's start with the intake API.",  "timestamp": base_ts + 7},
    {"speaker": "1", "text": "Sure, here's what I've been working on.", "timestamp": base_ts + 11},
]
# Mirror FrameIngestionService: all turns carry started_at and ended_at
turns = _turns_with_timing(base_ts, raw_turns)
r = post(frames, turns, session_id="sess-test-001", transcript_offset=0)
check("returns 202",        r.status_code == 202, str(r.status_code))
body = r.json()
bid  = body.get("batch_id", "")
check("batch_id present",   bid.startswith("batch_"))

# Verify staged files
import os, pathlib
staging = pathlib.Path("staging") / bid
check("staging dir created",        staging.is_dir())
check("transcript.json exists",     (staging / "transcript.json").exists())
check("meta.json exists",           (staging / "meta.json").exists())
staged_imgs = [f for f in staging.iterdir() if f.suffix == ".jpg"]
check("5 images staged",            len(staged_imgs) == 5, f"found {len(staged_imgs)}")
meta = json.loads((staging / "meta.json").read_text())
check("meta.image_count == 5",      meta["image_count"] == 5)
check("meta.session_id stored",     meta.get("session_id") == "sess-test-001", str(meta.get("session_id")))
check("meta.transcript_offset == 0", meta.get("transcript_offset") == 0, str(meta.get("transcript_offset")))
transcript_saved = json.loads((staging / "transcript.json").read_text())
check("transcript has 4 turns",     len(transcript_saved) == 4, f"got {len(transcript_saved)}")
check("turns have started_at",      all("started_at" in t for t in transcript_saved))
check("turns have ended_at",        all("ended_at" in t for t in transcript_saved))
# Verify filenames include index (collision-prevention fix)
img_names = sorted(f.name for f in staged_imgs)
check("filenames include idx field", all("_" in n for n in img_names), str(img_names[:2]))


# ─────────────────────────────────────────────────────────────────────────────
# 3. Same-second timestamp collision — 3 frames, all same epoch second
# ─────────────────────────────────────────────────────────────────────────────
section("3. Same-second collision — 3 frames with identical epoch-second timestamp")

ts = int(time.time())
# Android can produce multiple frames per second (IMAGE_API_FRAME_INTERVAL_MS = 2000,
# but rounding means some batches will share a second).
frames_same = [(f"{ts}.jpg", JPEG)] * 3
r = post(frames_same, [])
check("returns 202", r.status_code == 202, str(r.status_code))
bid2 = r.json().get("batch_id", "")
staging2 = pathlib.Path("staging") / bid2
staged2 = [f for f in staging2.iterdir() if f.suffix == ".jpg"]
check("all 3 frames staged (no overwrite)", len(staged2) == 3, f"found {len(staged2)}")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Max-batch — 20 frames (server cap)
# ─────────────────────────────────────────────────────────────────────────────
section("4. Max-batch — 20 frames (server hard limit)")

base_ts = int(time.time()) - 40
frames_20 = [(f"{base_ts + i * 2}.jpg", JPEG) for i in range(20)]
r = post(frames_20, [])
check("returns 202",          r.status_code == 202, str(r.status_code))
meta_20 = json.loads((pathlib.Path("staging") / r.json()["batch_id"] / "meta.json").read_text())
check("meta.image_count == 20", meta_20["image_count"] == 20)

# One over the cap
frames_21 = [(f"{base_ts + i * 2}.jpg", JPEG) for i in range(21)]
r = post(frames_21, [])
check("21 frames → 400",      r.status_code == 400, str(r.status_code))
check("error mentions count", "max image count" in r.json().get("error", ""))


# ─────────────────────────────────────────────────────────────────────────────
# 5. Growing-session transcript — 201 turns (previously would 400 and lose frames)
# ─────────────────────────────────────────────────────────────────────────────
section("5. Growing-session transcript — 201 accumulated turns (trimmed to 200)")

base_ts = int(time.time()) - 210
long_turns = [
    {"speaker": str(i % 2), "text": f"Turn {i}: discussing the pipeline architecture details.", "timestamp": base_ts + i}
    for i in range(201)
]
r = post([(f"{base_ts}.jpg", JPEG)], long_turns)
check("returns 202 (not 400)", r.status_code == 202, f"{r.status_code} — {r.text[:120]}")
bid3 = r.json().get("batch_id", "")
saved_turns = json.loads((pathlib.Path("staging") / bid3 / "transcript.json").read_text())
check("transcript trimmed to ≤ 200 turns", len(saved_turns) <= 200, f"got {len(saved_turns)}")
check("newest turn retained",
      any(t["text"].startswith("Turn 200") for t in saved_turns))
check("oldest turn dropped",
      not any(t["text"].startswith("Turn 0:") for t in saved_turns))


# ─────────────────────────────────────────────────────────────────────────────
# 6. Very large session — 500 turns (stress the trim path)
# ─────────────────────────────────────────────────────────────────────────────
section("6. Very large session — 500 turns")

base_ts = int(time.time()) - 510
huge_turns = [
    {"speaker": str(i % 2), "text": f"Turn {i}: more pipeline discussion.", "timestamp": base_ts + i}
    for i in range(500)
]
r = post([(f"{base_ts}.jpg", JPEG)], huge_turns)
check("returns 202", r.status_code == 202, f"{r.status_code}")
bid4 = r.json().get("batch_id", "")
saved_big = json.loads((pathlib.Path("staging") / bid4 / "transcript.json").read_text())
check("trimmed to ≤ 200",    len(saved_big) <= 200, f"got {len(saved_big)}")
check("newest turn retained", any("Turn 499" in t["text"] for t in saved_big))


# ─────────────────────────────────────────────────────────────────────────────
# 7. Session tracking — session_id + transcript_offset (Android turn 2 of 3)
# ─────────────────────────────────────────────────────────────────────────────
section("7. Session tracking — session_id stored; transcript_offset=2 stored and clamped")

base_ts = int(time.time()) - 30
session_turns = _turns_with_timing(base_ts, [
    {"speaker": "0", "text": "Turn 0: old context.", "timestamp": base_ts},
    {"speaker": "1", "text": "Turn 1: more context.", "timestamp": base_ts + 5},
    {"speaker": "0", "text": "Turn 2: new goal set.", "timestamp": base_ts + 10},
])
r = post(
    [(f"{base_ts + 10}.jpg", JPEG)],
    session_turns,
    session_id="sess-dedup-001",
    transcript_offset=2,
)
check("returns 202", r.status_code == 202, str(r.status_code))
bid_sess = r.json().get("batch_id", "")
meta_sess = json.loads((pathlib.Path("staging") / bid_sess / "meta.json").read_text())
check("session_id in meta",        meta_sess.get("session_id") == "sess-dedup-001")
check("transcript_offset in meta", meta_sess.get("transcript_offset") == 2)

# Clamped offset: offset > len(turns) → clamped to len(turns)
r2 = post([(f"{base_ts}.jpg", JPEG)], session_turns, session_id="sess-clamp", transcript_offset=999)
check("oversized offset → 202", r2.status_code == 202, str(r2.status_code))
meta_clamp = json.loads((pathlib.Path("staging") / r2.json()["batch_id"] / "meta.json").read_text())
check("offset clamped to len(turns)", meta_clamp["transcript_offset"] <= len(session_turns))

# started_at/ended_at accepted (no 400)
turns_with_timing = _turns_with_timing(base_ts, [
    {"speaker": "0", "text": "spoken turn", "timestamp": base_ts},
])
r3 = post([(f"{base_ts}.jpg", JPEG)], turns_with_timing)
check("started_at/ended_at fields accepted", r3.status_code == 202, str(r3.status_code))

# started_at with bad type → 400
bad_timing_turns = [{"speaker": "0", "text": "hi", "timestamp": base_ts, "started_at": "bad"}]
r4 = post([(f"{base_ts}.jpg", JPEG)], bad_timing_turns)
check("non-int started_at → 400", r4.status_code == 400)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Error cases — same as Android would encounter on bad state
# ─────────────────────────────────────────────────────────────────────────────
section("8. Error — image filename has no timestamp")

r = post([("photo.jpg", JPEG)], [])
check("returns 400",             r.status_code == 400)
check("error names the file",    "photo.jpg" in r.json().get("error", ""))


section("9. Error — oversized single image (> 5 MB)")

big = b"\xff\xd8" + b"\x00" * (5 * 1024 * 1024 + 1) + b"\xff\xd9"
ts = int(time.time())
r = post([(f"{ts}.jpg", big)], [])
check("returns 400",        r.status_code == 400)
check("error mentions 5 MB", "5 MB" in r.json().get("error", ""))


section("10. Error — malformed transcript JSON")

ts = int(time.time())
files = [("images[]", (f"{ts}.jpg", JPEG, "image/jpeg"))]
data  = {"transcript": "this is not json {{{"}
r = requests.post(ENDPOINT, files=files, data=data, timeout=10)
check("returns 400",              r.status_code == 400)
check("error mentions valid JSON", "valid JSON" in r.json().get("error", ""))


section("11. Error — transcript turn missing required field")

ts = int(time.time())
bad_turns = [{"speaker": "0", "text": "missing timestamp field"}]  # no timestamp
r = post([(f"{ts}.jpg", JPEG)], bad_turns)
check("returns 400",               r.status_code == 400)
check("error mentions 'timestamp'", "timestamp" in r.json().get("error", ""))


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'═'*60}")
if failures:
    print(f"  \033[31mFAILED: {len(failures)} check(s)\033[0m")
    for f in failures:
        print(f"    • {f}")
    sys.exit(1)
else:
    print(f"  \033[32mAll checks passed.\033[0m")
    sys.exit(0)
