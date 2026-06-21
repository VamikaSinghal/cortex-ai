"""
cortex/intake_api.py
--------------------
POST /api/intake/batch — stage a batch of images + transcript for async processing.

Returns 202 + batch_id immediately. No LLM calls, no intent-graph writes.
Later pipeline stages read from staging/<batch_id>/.

Run: uvicorn intake_api:app --reload
"""

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
MAX_TRANSCRIPT_BYTES = 500 * 1024    # 500 KB

TURN_REQUIRED_FIELDS = ("speaker", "text", "started_at", "ended_at")
TURN_INT_FIELDS = ("started_at", "ended_at")


# ── Timestamp extraction ──────────────────────────────────────────────────────

def extract_timestamp(filename: str, index: int, timestamps_companion: list[str]) -> int | None:
    """
    Extract a UNIX timestamp for an image.
    Primary: longest run of digits (≥8 chars) in the filename.
    Fallback: parallel timestamps[] form field at the same index.
    """
    integers = re.findall(r"\d+", filename)
    if integers:
        longest = max(integers, key=len)
        if len(longest) >= 8:
            return int(longest)

    if index < len(timestamps_companion):
        try:
            return int(timestamps_companion[index])
        except (ValueError, TypeError):
            pass

    return None


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

    # ── Collect optional parallel timestamps ──────────────────────────────────
    timestamps_companion = form.getlist("timestamps[]")

    # ── Read image bytes + validate size + extract timestamps ─────────────────
    image_data: list[tuple[str, int, bytes]] = []  # (filename, timestamp, data)

    for idx, upload in enumerate(images):
        filename = upload.filename or f"image_{idx}"
        data = await upload.read()

        if len(data) > MAX_IMAGE_BYTES:
            return JSONResponse(
                status_code=400,
                content={"error": f"image '{filename}' exceeds max size (5 MB)"},
            )

        ts = extract_timestamp(filename, idx, timestamps_companion)
        if ts is None:
            return JSONResponse(
                status_code=400,
                content={"error": f"image '{filename}' missing timestamp"},
            )

        image_data.append((filename, ts, data))

    # ── Transcript ────────────────────────────────────────────────────────────
    transcript_raw = form.get("transcript", "")
    if not isinstance(transcript_raw, str):
        # UploadFile instead of a plain string — shouldn't happen but guard it
        transcript_raw = (await transcript_raw.read()).decode("utf-8")

    if len(transcript_raw.encode("utf-8")) > MAX_TRANSCRIPT_BYTES:
        return JSONResponse(
            status_code=400,
            content={"error": "transcript exceeds max size (500 KB)"},
        )

    turns, err = validate_transcript(transcript_raw)
    if err:
        return JSONResponse(status_code=400, content={"error": err})

    # ── Stage to filesystem ───────────────────────────────────────────────────
    batch_id = f"batch_{uuid4()}"
    batch_dir = STAGING_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    for filename, ts, data in image_data:
        dest = batch_dir / f"{ts}_{filename}"
        dest.write_bytes(data)

    (batch_dir / "transcript.json").write_text(transcript_raw, encoding="utf-8")

    meta = {
        "batch_id": batch_id,
        "image_count": len(image_data),
        "received_at": datetime.now(timezone.utc).isoformat(),
    }
    (batch_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return JSONResponse(status_code=202, content={"batch_id": batch_id})
