"""
cortex/intake_api.py
--------------------
POST /api/intake/batch — stage a batch of images + transcript for async processing.

Returns 202 + batch_id immediately. No LLM calls, no intent-graph writes.
Later pipeline stages read from staging/<batch_id>/.

Run: uvicorn intake_api:app --reload
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

STAGING_DIR = Path("staging")
MAX_IMAGES = 20
MAX_IMAGE_BYTES = 5 * 1024 * 1024   # 5 MB
MAX_TRANSCRIPT_BYTES = 500 * 1024    # 500 KB soft limit — exceeded turns are trimmed
HARD_MAX_TRANSCRIPT_BYTES = MAX_TRANSCRIPT_BYTES * 20  # 10 MB ceiling before parse
MAX_TRANSCRIPT_TURNS = 200           # oldest turns dropped when client sends full session history

TURN_REQUIRED_FIELDS = ("speaker", "text", "timestamp")
TURN_INT_FIELDS = ("timestamp",)
TURN_OPTIONAL_INT_FIELDS = ("started_at", "ended_at")


def extract_timestamp(filename: str) -> int | None:
    """Return first 8+-digit integer found in filename, or None."""
    match = re.search(r"\d{8,}", filename)
    return int(match.group()) if match else None


def _trim_oldest_turns(turns: list, max_bytes: int) -> list:
    """Drop the oldest turns until the JSON encoding fits within max_bytes."""
    while turns and len(json.dumps(turns).encode("utf-8")) > max_bytes:
        turns = turns[1:]
    return turns


# ── Transcript validation ─────────────────────────────────────────────────────

def validate_transcript(raw: str) -> tuple[list, str | None]:
    """Parse and validate transcript JSON. Returns (turns, error_msg)."""
    try:
        turns = json.loads(raw)
    except json.JSONDecodeError:
        return [], "transcript must be valid JSON"

    if not isinstance(turns, list):
        return [], "transcript must be a JSON array"

    for i, turn in enumerate(turns):
        if not isinstance(turn, dict):
            return [], f"transcript[{i}] must be an object"
        for field in TURN_REQUIRED_FIELDS:
            if field not in turn:
                return [], f"transcript[{i}] missing required field: {field}"
        for field in TURN_INT_FIELDS:
            if not isinstance(turn[field], int):
                return [], f"transcript[{i}] field '{field}' must be an integer"
        for field in TURN_OPTIONAL_INT_FIELDS:
            if field in turn and not isinstance(turn[field], int):
                return [], f"transcript[{i}] field '{field}' must be an integer"

    return turns, None


# ── Route ─────────────────────────────────────────────────────────────────────

@app.post("/api/intake/batch")
async def intake_batch(request: Request):
    content_type = request.headers.get("content-type", "")
    if not content_type or (
        "multipart/form-data" not in content_type
        and "application/x-www-form-urlencoded" not in content_type
    ):
        return JSONResponse(
            status_code=400,
            content={"error": "Content-Type must be multipart/form-data"},
        )

    try:
        form = await request.form()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "malformed multipart request"})

    # ── Collect images ────────────────────────────────────────────────────────
    images = form.getlist("images[]")
    if not images:
        return JSONResponse(status_code=400, content={"error": "images[] must contain at least one image"})

    if len(images) > MAX_IMAGES:
        return JSONResponse(
            status_code=400,
            content={"error": f"batch exceeds max image count ({MAX_IMAGES})"},
        )

    # ── Read image bytes, extract per-image timestamp from filename ───────────
    image_data: list[tuple[str, int, int, bytes]] = []  # (filename, observed_at, idx, data)

    for idx, upload in enumerate(images):
        # Strip directory components to prevent path traversal
        filename = Path(upload.filename or f"image_{idx}").name or f"image_{idx}"
        ts = extract_timestamp(filename)
        if ts is None:
            return JSONResponse(
                status_code=400,
                content={"error": f"image '{filename}' has no timestamp in filename (need 8+ digit sequence)"},
            )
        data = await upload.read()
        if len(data) > MAX_IMAGE_BYTES:
            return JSONResponse(
                status_code=400,
                content={"error": f"image '{filename}' exceeds max size (5 MB)"},
            )
        image_data.append((filename, ts, idx, data))

    # ── Transcript ────────────────────────────────────────────────────────────
    transcript_raw = form.get("transcript", "")
    if not isinstance(transcript_raw, str):
        # UploadFile instead of a plain string — shouldn't happen but guard it
        transcript_raw = (await transcript_raw.read()).decode("utf-8")

    # Hard ceiling: guard against OOM before attempting to parse
    if len(transcript_raw.encode("utf-8")) > HARD_MAX_TRANSCRIPT_BYTES:
        return JSONResponse(
            status_code=400,
            content={"error": "transcript exceeds max size (500 KB)"},
        )

    turns, err = validate_transcript(transcript_raw)
    if err:
        return JSONResponse(status_code=400, content={"error": err})

    # Android clients accumulate all turns across a session and replay them on every upload.
    # Trim the oldest turns first so the byte check below only fires on genuinely malformed input
    # (e.g. a single turn whose text field alone exceeds the limit).
    if len(turns) > MAX_TRANSCRIPT_TURNS:
        turns = turns[-MAX_TRANSCRIPT_TURNS:]

    # After semantic trim, reject if the transcript is still over the byte limit.
    # This catches turns with pathologically large individual text fields.
    transcript_raw = json.dumps(turns)
    if len(transcript_raw.encode("utf-8")) > MAX_TRANSCRIPT_BYTES:
        return JSONResponse(
            status_code=400,
            content={"error": "transcript exceeds max size (500 KB)"},
        )

    # ── Optional session metadata ─────────────────────────────────────────────
    session_id_raw = form.get("session_id", None)
    session_id: str | None = str(session_id_raw).strip() or None if session_id_raw is not None else None

    transcript_offset_raw = form.get("transcript_offset", None)
    if transcript_offset_raw is not None:
        try:
            transcript_offset = int(transcript_offset_raw)
            if transcript_offset < 0:
                raise ValueError
        except (ValueError, TypeError):
            return JSONResponse(
                status_code=400,
                content={"error": "transcript_offset must be a non-negative integer"},
            )
    else:
        transcript_offset = 0

    # Clamp so downstream can index safely
    transcript_offset = max(0, min(transcript_offset, len(turns)))

    # ── Stage to filesystem ───────────────────────────────────────────────────
    batch_id = f"batch_{uuid4()}"
    batch_dir = STAGING_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    for filename, ts, idx, data in image_data:
        # Include the batch index so two frames with the same second-level timestamp
        # get distinct filenames instead of silently overwriting each other.
        dest = batch_dir / f"{ts}_{idx:03d}_{filename}"
        dest.write_bytes(data)

    (batch_dir / "transcript.json").write_text(transcript_raw, encoding="utf-8")

    meta = {
        "batch_id": batch_id,
        "image_count": len(image_data),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "transcript_offset": transcript_offset,
    }
    (batch_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return JSONResponse(status_code=202, content={"batch_id": batch_id})
