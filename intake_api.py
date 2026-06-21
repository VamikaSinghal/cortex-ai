"""
cortex/intake_api.py
--------------------
POST /api/intake/batch — receive images + transcript, extract context with Claude Haiku.

Returns 200 with extracted context immediately (synchronous).
Images are staged to disk; transcript is summarized inline.

Run: uvicorn intake_api:app --reload
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import anthropic
from dotenv import load_dotenv
from fastapi import FastAPI, Request

load_dotenv()
from fastapi.responses import JSONResponse

app = FastAPI()

STAGING_DIR = Path("staging")
MAX_IMAGES = 20
MAX_IMAGE_BYTES = 5 * 1024 * 1024   # 5 MB
MAX_TRANSCRIPT_TURNS = 200

TURN_REQUIRED_FIELDS = ("speaker", "text", "timestamp")
TURN_INT_FIELDS = ("timestamp",)
TURN_OPTIONAL_INT_FIELDS = ("started_at", "ended_at")

EXTRACTION_SYSTEM_PROMPT = """You are a context extraction engine for a personal AI assistant called Cortex.

Given a conversation transcript with labeled speakers, extract structured context.

Each item in KEY_INSIGHTS, DECISIONS, OPEN_QUESTIONS, and ACTION_ITEMS must be an object with:
  {"speaker": "<speaker_id>", "text": "<the insight/decision/question/action>"}

Use the exact speaker ID from the transcript (e.g. "0", "1").
If an item involves multiple speakers or is general, use the speaker who most drove it.

PEOPLE: people mentioned — {"name": "...", "context": "..."}
PROJECTS: projects or work items discussed — {"name": "...", "context": "..."}
SUMMARY: 2-3 sentence summary of the overall conversation (plain string)

Rules:
- Be concise — capture signal, not noise
- Only include items with real informational value
- Empty lists are fine if a category has nothing
- Return ONLY valid JSON, no markdown fences, no commentary

Example output:
{
  "KEY_INSIGHTS": [{"speaker": "0", "text": "Redis vector search is fast enough for real-time retrieval"}],
  "DECISIONS": [{"speaker": "1", "text": "Use GitHub instead of Obsidian for note storage"}],
  "OPEN_QUESTIONS": [{"speaker": "0", "text": "Does the Omi webhook support streaming?"}],
  "PEOPLE": [{"name": "Sarah", "context": "Teammate handling the MCP server"}],
  "PROJECTS": [{"name": "Cortex", "context": "Universal context layer for AI"}],
  "ACTION_ITEMS": [{"speaker": "0", "text": "Set up Redis Stack with Docker before hacking starts"}],
  "SUMMARY": "Discussed Cortex architecture. Decided on GitHub for storage and Redis for vector search."
}"""


def extract_timestamp(filename: str) -> int | None:
    match = re.search(r"\d{8,}", filename)
    return int(match.group()) if match else None


def validate_transcript(raw: str) -> tuple[list, str | None]:
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


def format_transcript(turns: list) -> str:
    lines = []
    for turn in turns:
        lines.append(f"[Speaker {turn['speaker']}]: {turn['text']}")
    return "\n".join(lines)


def _parse_json(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    return None


@app.post("/api/intake/batch")
async def intake_batch(request: Request):
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type and "application/x-www-form-urlencoded" not in content_type:
        return JSONResponse(status_code=400, content={"error": "Content-Type must be multipart/form-data"})

    try:
        form = await request.form()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "malformed multipart request"})

    # ── Images ────────────────────────────────────────────────────────────────
    images = form.getlist("images[]")
    if not images:
        return JSONResponse(status_code=400, content={"error": "images[] must contain at least one image"})
    if len(images) > MAX_IMAGES:
        return JSONResponse(status_code=400, content={"error": f"batch exceeds max image count ({MAX_IMAGES})"})

    image_data: list[tuple[str, int, int, bytes]] = []
    for idx, upload in enumerate(images):
        filename = Path(upload.filename or f"image_{idx}").name or f"image_{idx}"
        ts = extract_timestamp(filename)
        if ts is None:
            return JSONResponse(status_code=400, content={"error": f"image '{filename}' has no timestamp in filename (need 8+ digit sequence)"})
        data = await upload.read()
        if len(data) > MAX_IMAGE_BYTES:
            return JSONResponse(status_code=400, content={"error": f"image '{filename}' exceeds max size (5 MB)"})
        image_data.append((filename, ts, idx, data))

    # ── Transcript ────────────────────────────────────────────────────────────
    transcript_raw = form.get("transcript", "")
    if not isinstance(transcript_raw, str):
        transcript_raw = (await transcript_raw.read()).decode("utf-8")

    turns, err = validate_transcript(transcript_raw)
    if err:
        return JSONResponse(status_code=400, content={"error": err})

    if len(turns) > MAX_TRANSCRIPT_TURNS:
        turns = turns[-MAX_TRANSCRIPT_TURNS:]

    # ── Stage images ──────────────────────────────────────────────────────────
    batch_id = f"batch_{uuid4()}"
    batch_dir = STAGING_DIR / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    for filename, ts, idx, data in image_data:
        dest = batch_dir / f"{ts}_{idx:03d}_{filename}"
        dest.write_bytes(data)

    (batch_dir / "transcript.json").write_text(json.dumps(turns, indent=2), encoding="utf-8")

    # ── Extract context with Haiku ────────────────────────────────────────────
    extraction: dict = {
        "KEY_INSIGHTS": [],
        "DECISIONS": [],
        "OPEN_QUESTIONS": [],
        "PEOPLE": [],
        "PROJECTS": [],
        "ACTION_ITEMS": [],
        "SUMMARY": "",
    }

    if turns:
        formatted = format_transcript(turns)
        speakers = sorted({str(t["speaker"]) for t in turns})

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2000,
            system=EXTRACTION_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Speakers in this conversation: {', '.join(speakers)}\n\n"
                    f"{formatted}\n\n"
                    "Extract context as JSON:"
                ),
            }],
        )

        raw_output = response.content[0].text if response.content else ""
        parsed = _parse_json(raw_output)
        if parsed:
            extraction.update(parsed)

    extraction["_source"] = "glasses"
    extraction["_timestamp"] = datetime.now(timezone.utc).isoformat()
    extraction["_batch_id"] = batch_id
    extraction["_image_count"] = len(image_data)
    extraction["_speakers"] = sorted({str(t["speaker"]) for t in turns})

    (batch_dir / "extraction.json").write_text(json.dumps(extraction, indent=2), encoding="utf-8")

    return JSONResponse(status_code=200, content=extraction)
